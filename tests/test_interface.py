"""Black-box acceptance scenarios using independent synthetic inputs."""

import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Interface(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = self.root / "profile"

    def call(self, command, data=None, fail=False):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "raya",
                "--profile",
                str(self.profile),
                command,
                "-",
            ],
            cwd=ROOT,
            input=json.dumps(data or {}),
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, int(fail), process.stderr)
        return json.loads(process.stderr if fail else process.stdout)

    def approval(self, req, command="approve"):
        return self.call(command, {"id": req["id"], "digest": req["digest"]})

    def skill(self, provider="paper", version="1", operation="reverse"):
        return dict(
            action="fold",
            provider=provider,
            version=version,
            operation=operation,
            runtime="raya-text-1",
            permissions=[],
            input="string",
            output="string",
            license="CC0-1.0",
        )

    def install(self, *args):
        return self.approval(self.call("install", self.skill(*args)))

    def test_clean_profile_and_failed_save(self):
        self.assertTrue(self.call("capabilities")["typing"])
        self.call("set", {"field": "language", "value": "tr"})
        self.assertIn(
            "language",
            self.call("set", {"field": "language", "value": "?"}, True)["error"],
        )
        self.assertEqual(self.call("settings")["language"], "tr")
        self.assertEqual(self.call("capabilities")["coding_provider"], "unconfigured")

    def test_competing_consumers_and_profile_reuse(self):
        self.install()
        request = self.call("run", {"skill": "paper/fold", "input": "aλb"})

        def consume(_):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "raya",
                    "--profile",
                    str(self.profile),
                    "approve",
                    json.dumps({"id": request["id"], "digest": request["digest"]}),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            outputs = list(pool.map(consume, range(4)))
        self.assertEqual(sorted(p.returncode for p in outputs), [0, 1, 1, 1])
        self.assertEqual(self.call("requests")[request["id"]]["result"], "bλa")
        self.install("cloth")
        self.assertEqual(len(list((self.profile / "runtimes").glob("*/worker.py"))), 1)
        self.approval(self.call("remove", {"skill": "paper/fold"}))
        self.assertIn("cloth/fold", self.call("skills"))
        record = next(
            k
            for k, v in self.call("history").items()
            if v["manifest"]["provider"] == "paper"
        )
        self.approval(self.call("rollback", {"record": record}))
        self.assertIn("paper/fold", self.call("skills"))

    def test_default_deny_and_stale(self):
        req = self.call("install", self.skill())
        self.assertEqual(self.call("skills"), {})
        self.assertEqual(
            self.call("approve", {"id": req["id"], "digest": "0" * 64})["state"],
            "denied",
        )
        req = self.call("install", self.skill())
        self.assertEqual(self.approval(req, "deny")["state"], "denied")
        self.install()
        self.call("install", self.skill(operation="lower"), True)
        pending = self.call("run", {"skill": "paper/fold", "input": "efg"})
        self.install("paper", "2", "upper")
        self.assertEqual(self.approval(pending)["state"], "denied")
        self.call("install", dict(self.skill(), permissions=["network"]), True)
        self.call("install", dict(self.skill(), operation="exec"), True)

    def test_incompatible_resource_locks(self):
        original = self.install()["result"]
        skill = self.skill(provider="ink")
        skill["limits"] = dict(memory_mib=96, timeout_s=1, accelerator="cpu")
        req = self.call("install", skill)
        self.assertEqual(req["runtime_lock"]["memory_mib"], 96)
        other = self.approval(req)["result"]
        self.assertNotEqual(original["runtime"], other["runtime"])
        skill["limits"]["accelerator"] = "gpu"
        self.assertIn("accelerator", self.call("install", skill, True)["error"])

    def test_damaged_runtime(self):
        self.install()
        worker = next((self.profile / "runtimes").glob("*/worker.py"))
        worker.write_text("raise SystemExit(0)")
        req = self.call("run", {"skill": "paper/fold", "input": "q"})
        self.assertEqual(self.approval(req)["state"], "failed")

    def test_incremental_analysis(self):
        root = self.root / "src"
        root.mkdir()
        (root / "library.py").write_text("def blend():\n    return 5\n")
        (root / "test_library.py").write_text("from library import blend\n")
        (root / "private").mkdir()
        (root / "private" / "secret.py").write_text("pass")

        def scan():
            return self.call("index", {"root": str(root)})

        self.assertEqual(scan(), {"parsed": 2, "files": 2})
        self.assertEqual(scan()["parsed"], 0)
        result = self.call("context", {"root": str(root), "symbol": "blend"})
        self.assertEqual(result["definitions"], {"library.py": [["blend", 1, 2]]})
        self.assertEqual(result["impacted_tests"], ["test_library.py"])
        (root / "library.py").rename(root / "other.py")
        (root / "test_library.py").unlink()
        (root / "other.py").write_text("def new(): pass\n")
        self.assertEqual(scan(), {"files": 1, "parsed": 1})

    def test_manual_goal_and_review(self):
        goal = self.call("goal-create", {"title": "Proofread two passages"})["id"]

        def task(title, deps):
            return self.call("task-add", dict(goal=goal, title=title, depends=deps))[
                "latest_task"
            ]

        first, second = task("Read A", []), task("Read B", [])
        review = task("Compare", [first, second])
        self.call("handoff", dict(goal=goal, task=review), True)
        for tid in (first, second):
            args = dict(goal=goal, task=tid)
            receipt = self.call("handoff", args)["tasks"][tid]["handoff"]
            self.assertEqual(
                receipt, self.call("handoff", args)["tasks"][tid]["handoff"]
            )
            evidence = "read"
            checksum = hashlib.sha256(b'"read"').hexdigest()
            body = dict(args, handoff=receipt, evidence=evidence, digest=checksum)
            self.call("collect", dict(body, digest="bad"), True)
            self.call(
                "collect", dict(body, handoff=dict(receipt, scope="changed")), True
            )
            self.assertEqual(self.call("collect", body), self.call("collect", body))
            self.call(
                "review",
                dict(
                    args,
                    digest=checksum,
                    independent_check="Compared signed review sheet",
                ),
            )
        self.call("goal-state", dict(goal=goal, state="paused"))
        self.call("handoff", dict(goal=goal, task=review), True)
        self.call("goal-state", dict(goal=goal, state="active"))
        self.call("handoff", dict(goal=goal, task=review))
        self.call("goal-state", dict(goal=goal, state="cancelled"))
        self.call("goal-state", dict(goal=goal, state="active"), True)

    def test_notes_and_due(self):
        note = self.call("note", {"text": "Amaranth"})["id"]
        self.assertIn(note, self.call("search", {"query": "AMAR"}))
        rem = self.call(
            "remind", {"text": "Water plants", "at": "2001-01-01T00:00:00Z"}
        )["id"]
        self.assertIn(rem, self.call("due"))
