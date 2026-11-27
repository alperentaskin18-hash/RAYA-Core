"""Resumable, single-target development with owner-approved disclosure and commits."""

import difflib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import uuid

from . import cloud, engine, goals, index, vault
from .core import digest, propose, require
from .storage import child

TARGETS = {
    "feature": (
        "nonempty_lines",
        "Implement counting nonblank lines in a string; whitespace-only lines do not count.",
    ),
    "repair": (
        "percent",
        "Diagnose and repair the boundary defect in the inclusive 0..100 clamp.",
    ),
    "optimize": (
        "distinct",
        "Improve distinct integer counting without changing results. Beat the reference CPU time by at least 20 percent.",
    ),
}
RUNNER = Path(__file__).with_name("dev_runner.py")


def cases(kind):
    if kind == "feature":
        return ["", " \n\t", "A\n\nB\n", " Ç \n  \nİ"], [0, 0, 2, 2]
    if kind == "repair":
        return [-1, 0, 1, 98, 99, 100, 101, 1000], [0, 0, 1, 98, 99, 100, 100, 100]
    return [[], [2, 2, 1], list(range(2000)) * 2], [0, 2, 2000]


def sandbox(source, inputs):
    require(
        isinstance(source, str) and 0 < len(source) <= 16384, "invalid candidate size"
    )
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        RUNNER.read_text(encoding="utf-8"),
    ]
    if os.name == "nt":
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/wsl.exe"),
            "--exec",
            "/usr/bin/python3",
            *command[1:],
        ]
    elif not sys.platform.startswith("linux"):
        raise ValueError("restricted Linux runner or WSL required")
    with tempfile.TemporaryDirectory() as private, tempfile.TemporaryFile() as output:
        try:
            run = subprocess.run(
                command,
                input=json.dumps({"source": source, "inputs": inputs}).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=8,
                cwd=private,
                env={k: os.environ[k] for k in ("SystemRoot",) if k in os.environ},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError(
                "restricted test process unavailable or timed out"
            ) from None
        output.seek(0)
        payload = output.read(65537)
    require(
        len(payload) <= 65536 and run.returncode == 0,
        "restricted test process rejected candidate or sandbox unavailable",
    )
    try:
        data = json.loads(payload)
        values, elapsed = data["results"], data["cpu_ns"]
        require(
            isinstance(values, list)
            and len(values) == len(inputs)
            and type(elapsed) is int
            and elapsed >= 0,
            "invalid restricted test result",
        )
        return values, elapsed
    except (ValueError, TypeError, KeyError):
        raise ValueError("restricted runner returned no valid result") from None


def git(repo, *args, data=None):
    env = {
        k: os.environ[k]
        for k in ("PATH", "SystemRoot", "TEMP", "TMP", "TMPDIR")
        if k in os.environ
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="RAYA Core",
        GIT_AUTHOR_EMAIL="raya@localhost.invalid",
        GIT_COMMITTER_NAME="RAYA Core",
        GIT_COMMITTER_EMAIL="raya@localhost.invalid",
    )
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "protocol.allow=never",
            "-C",
            str(repo),
            *args,
        ],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=20,
    )
    require(result.returncode == 0, "isolated Git operation failed: " + args[0])
    return result.stdout


def paths(p, t):
    parent = child(p.directory, "worktrees", directory=True)
    return child(parent, t["id"], directory=True)


def start(p, args):
    kind = args.get("kind")
    require(kind in TARGETS, "unknown development task")
    cloud.enable(p)
    repo = Path(args.get("repository", ".")).resolve(strict=True)
    base = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    target = "development_targets/" + TARGETS[kind][0] + ".py"
    original = git(repo, "show", base + ":" + target).decode("utf-8")
    require(len(original) <= 16384, "source exceeds disclosure bound")
    ident = uuid.uuid4().hex
    goal = goals.change(p, "goal-create", {"title": TARGETS[kind][1]})
    goal = goals.change(
        p,
        "task-add",
        {
            "goal": goal["id"],
            "title": "Generate and independently test a single-file change",
        },
    )
    t = dict(
        id=ident,
        kind=kind,
        phase="planning",
        base=base,
        path=target,
        repository=str(repo),
        goal=goal["id"],
        goal_task=goal["latest_task"],
        rounds=0,
        route=args.get("route", "auto"),
        original=original,
        symbols=index.parse(original, target, "")["symbols"],
    )
    p.put("dev_tasks", ident, t)
    return summary(t)


def summary(t):
    return {
        k: t[k]
        for k in (
            "id",
            "kind",
            "phase",
            "goal",
            "route",
            "rounds",
            "reason",
            "request",
            "proposed_commit",
            "metrics",
            "diff",
            "plan",
        )
        if k in t
    }


def save(p, t, phase, reason=""):
    t.update(phase=phase, reason=reason)
    p.put("dev_tasks", t["id"], t)
    return summary(t)


def propose_model(p, t):
    if not engine.config(p)["routes"]:
        return save(p, t, "paused", "no cloud routes configured; no disclosure sent")
    planning = "plan" not in t
    prompt = {
        "task": TARGETS[t["kind"]][1],
        "file": t["path"],
        "symbols": t["symbols"],
        "source": t["original"],
    }
    if planning:
        prompt["instruction"] = "Provide a short implementation plan. No tools."
    else:
        prompt.update(
            plan=t["plan"],
            diagnosis=t.get("reason"),
            previous=t.get("candidate"),
            instruction="Return only a JSON object with base_digest and replacement. replacement is one complete Python solve function, without imports, annotations or decorators.",
            base_digest=digest(t["original"]),
        )
    text = json.dumps(prompt, ensure_ascii=False)
    require(
        not any(vault.contains(text, s) for s in vault.values(p)),
        "protected value in proposed disclosure",
    )
    try:
        req = engine.prepare(
            p, {"route": t["route"], "prompt": text, "task_kind": t["kind"]}
        )
    except ValueError as exc:
        return save(p, t, "paused", str(exc))
    t["request"] = req["id"]
    save(p, t, "plan_approval" if planning else "patch_approval")
    return dict(summary(t), approval=req)


def checkout(p, t):
    path = paths(p, t)
    if not path.exists():
        path.parent.mkdir(mode=0o700, exist_ok=True)
        git(
            t["repository"],
            "worktree",
            "add",
            "--detach",
            "--no-checkout",
            str(path),
            t["base"],
        )
        git(path, "read-tree", t["base"])
    require(
        git(path, "rev-parse", "HEAD").decode().strip() == t["base"],
        "worktree baseline changed",
    )
    return path


def test_candidate(p, t, answer):
    # The host owns the filename, the comparison oracle and every command.
    patch = json.loads(answer)
    require(
        isinstance(patch, dict)
        and set(patch) == {"base_digest", "replacement"}
        and patch["base_digest"] == digest(t["original"]),
        "stale or invalid replacement",
    )
    code = patch["replacement"]
    require(
        isinstance(code, str) and 0 < len(code) <= 16384 and "\x00" not in code,
        "candidate too large",
    )
    t["candidate"] = code
    tree = checkout(p, t)
    folder = child(tree, "development_targets", directory=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    dest = child(folder, Path(t["path"]).name)
    dest.write_bytes(code.encode())
    t["diff"] = "".join(
        difflib.unified_diff(
            t["original"].splitlines(True),
            code.splitlines(True),
            fromfile=t["path"],
            tofile=t["path"],
        )
    )
    inputs, expected = cases(t["kind"])
    actual, ns = sandbox(code, inputs)
    require(
        type(actual) is list
        and all(type(x) is int for x in actual)
        and actual == expected,
        "acceptance output mismatch",
    )
    t["metrics"] = {"passed_cases": len(inputs), "cpu_ns": ns}
    if t["kind"] == "optimize":
        before = statistics.median(sandbox(t["original"], inputs)[1] for _ in range(3))
        after = statistics.median(sandbox(code, inputs)[1] for _ in range(3))
        t["metrics"].update(reference_ns=before, candidate_ns=after)
        require(
            before > 0 and after < before * 0.8, "measured improvement below 20 percent"
        )


def step(p, ident):
    t = p.get("dev_tasks", ident)
    require(
        t
        and t["phase"]
        in ("planning", "paused", "repair", "plan_approval", "patch_approval"),
        "task cannot advance",
    )
    require(cloud.strict(p), "strict spending policy required")
    if t["phase"] in ("planning", "paused", "repair"):
        return propose_model(p, t)
    req = p.get("requests", t["request"])
    if req["state"] in ("pending", "in_progress"):
        return dict(summary(t), approval=dict(req, id=t["request"]))
    if req["state"] != "completed":
        return save(p, t, "paused", "previous request " + req["state"])
    output = req["result"]
    if not isinstance(output, dict) or not isinstance(output.get("text"), str):
        return save(p, t, "paused", "no usable model output")
    answer = output["text"]
    if t["phase"] == "plan_approval":
        t["plan"] = answer[:4096]
        return propose_model(p, t)
    t["rounds"] += 1
    try:
        test_candidate(p, t, answer)
        passed = True
        reason = ""
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        passed = False
        reason = str(exc)[:256]
    plan = next(r for r in req["content"]["routes"] if r["alias"] == output["route"])
    attempt = p.get("ai_attempts", f"{t['request']}/{output['attempts']}", {})
    cloud.evaluation(
        p, plan, t["kind"], passed, output.get("usage", {}), attempt.get("latency_ms")
    )
    return save(
        p, t, "review" if passed else "repair" if t["rounds"] < 3 else "stopped", reason
    )


def commit_proposal(p, ident):
    t = p.get("dev_tasks", ident)
    require(t and t["phase"] == "review", "tested candidate required")
    req = propose(
        p,
        "dev-commit",
        ident,
        {
            "file": t["path"],
            "base": t["base"],
            "diff": t["diff"],
            "metrics": t["metrics"],
        },
        digest(t),
        ["git.proposed_commit"],
    )
    return req


def commit(p, t):
    require(t["phase"] == "review", "candidate no longer reviewable")
    path = checkout(p, t)
    require(
        (path / t["path"]).read_bytes() == t["candidate"].encode(), "candidate changed"
    )
    blob = (
        git(path, "hash-object", "-w", "--stdin", data=t["candidate"].encode())
        .decode()
        .strip()
    )
    git(path, "read-tree", t["base"])
    git(
        path, "update-index", "--add", "--cacheinfo", "100644," + blob + "," + t["path"]
    )
    tree = git(path, "write-tree").decode().strip()
    sha = (
        git(
            path,
            "commit-tree",
            tree,
            "-p",
            t["base"],
            "-m",
            "Propose reviewed " + t["kind"] + " change",
        )
        .decode()
        .strip()
    )
    ref = "refs/heads/proposals/" + t["id"]
    existing = git(path, "for-each-ref", "--format=%(objectname)", ref).decode().strip()
    if existing:
        require(
            git(path, "rev-parse", existing + "^{tree}").decode().strip() == tree
            and git(path, "rev-parse", existing + "^").decode().strip() == t["base"],
            "proposal ref already used",
        )
        sha = existing
    else:
        git(path, "update-ref", ref, sha, "0" * len(sha))
    goal = goals.change(p, "handoff", {"goal": t["goal"], "task": t["goal_task"]})
    handoff = goal["tasks"][t["goal_task"]]["handoff"]
    evidence = json.dumps({"commit": sha, "metrics": t["metrics"]}, sort_keys=True)
    goals.change(
        p,
        "collect",
        {
            "goal": t["goal"],
            "task": t["goal_task"],
            "handoff": handoff,
            "evidence": evidence,
            "digest": digest(evidence),
        },
    )
    goals.change(
        p,
        "review",
        {
            "goal": t["goal"],
            "task": t["goal_task"],
            "digest": digest(evidence),
            "independent_check": "Restricted runner compared candidate results to host-owned cases; owner approved the displayed diff.",
        },
    )
    t["proposed_commit"] = sha
    return save(p, t, "proposed")


def cancel(p, ident, rollback=False):
    t = p.get("dev_tasks", ident)
    require(t, "unknown task")
    req = p.get("requests", t.get("request", ""))
    if req and req["state"] in ("pending", "in_progress"):
        engine.cancel(p, t["request"])
    if rollback:
        path = paths(p, t)
        if path.is_dir():
            allowed = {".git", "development_targets", t["path"]}
            require(
                all(
                    x.relative_to(path).as_posix() in allowed and not x.is_symlink()
                    for x in path.rglob("*")
                ),
                "worktree contains unrelated files",
            )
            file = path / t["path"]
            require(
                not file.exists()
                or file.read_bytes() == t.get("candidate", "").encode(),
                "worktree edited outside session",
            )
            git(t["repository"], "worktree", "remove", "--force", str(path))
        if t.get("proposed_commit"):
            git(
                t["repository"],
                "update-ref",
                "-d",
                "refs/heads/proposals/" + ident,
                t["proposed_commit"],
            )
            t.pop("proposed_commit")
    if goals.goal(p, t["goal"])["state"] != "cancelled":
        goals.change(p, "goal-state", {"goal": t["goal"], "state": "cancelled"})
    return save(p, t, "rolled_back" if rollback else "cancelled")
