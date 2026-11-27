"""Local profile, manifests and single-use action transactions."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from .storage import child, profile_root
from uuid import uuid4


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_field(value, name, limit=4096):
    require(isinstance(value, str) and 0 < len(value) <= limit, f"invalid {name}")
    return value


def identifier(value, name):
    import re

    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value),
        f"invalid {name}",
    )
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS objects(kind TEXT, name TEXT, value TEXT NOT NULL,
 PRIMARY KEY(kind,name));
"""


class Profile:
    def __init__(self, directory, *, read_only=False):
        self.directory = profile_root(directory)
        for name in ("core.db", "core.db-wal", "core.db-shm", "core.db-journal"):
            child(self.directory, name)
        database = self.directory / "core.db"
        location = database.as_uri() + "?mode=ro" if read_only else database
        self.db = sqlite3.connect(
            location, uri=read_only, isolation_level=None, timeout=15
        )
        if not read_only:
            self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, kind, name, default=None):
        row = self.db.execute(
            "SELECT value FROM objects WHERE kind=? AND name=?", (kind, name)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        rows = self.db.execute(
            "SELECT name,value FROM objects WHERE kind=? ORDER BY name", (kind,)
        )
        return {name: json.loads(value) for name, value in rows}

    def put(self, kind, name, data):
        self.db.execute(
            "INSERT OR REPLACE INTO objects VALUES(?,?,?)", (kind, name, encode(data))
        )

    def drop(self, kind, name):
        self.db.execute("DELETE FROM objects WHERE kind=? AND name=?", (kind, name))

    def settings(self):
        return self.get("profile", "preferences", {"language": "en", "theme": "system"})

    def configure(self, field, value):
        if field == "billing_policy":
            require(value in ("strict", "legacy"), "invalid billing policy")
            require(
                value != "legacy" or not self.settings().get("zero_cost"),
                "DAY_07 profile is locked to zero-cost routing",
            )
        valid = {
            "billing_policy": ("strict", "legacy"),
            "language": ("en", "tr"),
            "theme": ("system", "light", "dark"),
            "provider": ("off", "local-llama", "engine"),
        }
        if field == "provider_port":
            require(
                type(value) is int and 1 <= value <= 65535,
                "invalid setting provider_port",
            )
        else:
            require(value in valid.get(field, ()), f"invalid setting {field}")
        current = self.settings()
        current[field] = value
        self.put("profile", "preferences", current)
        return current


OPS = {"upper", "lower", "count", "sha256", "reverse"}


def manifest(data):
    fields = {
        "provider",
        "action",
        "version",
        "runtime",
        "license",
        "input",
        "output",
        "permissions",
        "operation",
    }
    require(
        isinstance(data, dict) and set(data) - {"limits"} == fields,
        "invalid manifest fields",
    )
    if "limits" in data:
        limits = data["limits"]
        require(
            isinstance(limits, dict)
            and set(limits) == {"memory_mib", "timeout_s", "accelerator"},
            "invalid runtime limits",
        )
        require(limits["accelerator"] == "cpu", "unavailable accelerator")
        require(
            type(limits["memory_mib"]) is int and limits["memory_mib"] in (96, 192),
            "unsupported memory limit",
        )
        require(
            type(limits["timeout_s"]) is int and 1 <= limits["timeout_s"] <= 3,
            "unsupported timeout",
        )
    for name in ("provider", "action", "version"):
        identifier(data[name], name)
    text_field(data["license"], "license", 128)
    require(
        data["operation"] in OPS and data["permissions"] == [],
        "unsupported operation or permissions",
    )
    require(
        data["runtime"] == "raya-text-1"
        and data["input"] == data["output"] == "string",
        "invalid contract",
    )
    return f"{data['provider']}/{data['action']}"


def propose(profile, operation, destination, content, baseline, permissions):
    token = uuid4().hex
    request = dict(
        request_id=token,
        operation=operation,
        destination=destination,
        content=content,
        baseline=baseline,
        permissions=permissions,
    )
    if operation == "install":
        from .runtime import lock

        request["runtime_lock"] = lock(content)
    request.update(digest=digest(request), state="pending")
    profile.put("requests", token, request)
    return {"id": token, **request}


def plan_install(profile, data):
    key = manifest(data)
    current = profile.get("skills", key)
    require(
        not current
        or current["manifest"]["version"] != data["version"]
        or current["manifest"] == data,
        "conflicting registration",
    )
    return propose(
        profile,
        "install",
        key,
        data,
        digest(profile.get("skills", key)),
        ["profile.write"],
    )


def plan_run(profile, key, argument):
    from .vault import values

    text_field(argument, "input", 16384)
    require(
        not any(v in argument for v in values(profile)), "protected value in command"
    )
    skill = profile.get("skills", key)
    require(skill, "unknown skill")
    return propose(
        profile, "run", key, argument, digest(skill), skill["manifest"]["permissions"]
    )


def decide(profile, token, approved_digest, allow, emit=None, *, stop=None):
    from . import engine
    from .runtime import execute, install

    require(profile.db.in_transaction, "approval requires a profile transaction")
    request = profile.get("requests", token)
    require(request and request["state"] == "pending", "unknown or consumed request")
    from .vault import contains, values

    if any(contains(request["content"], value) for value in values(profile)):
        allow = False
    key = request["destination"]
    current = profile.get("skills", key)
    descriptor = {
        k: request[k]
        for k in ("operation", "destination", "content", "baseline", "permissions")
    }
    # Old pending proposals predate request identity binding. They must be reissued.
    if request.get("request_id") != token:
        allow = False
    descriptor["request_id"] = token
    if "runtime_lock" in request:
        from .runtime import lock

        descriptor["runtime_lock"] = request["runtime_lock"]
        if request["runtime_lock"] != lock(request["content"]):
            allow = False
    baseline = (
        profile.settings()
        if request["operation"] in {"generate", "ai-generate"}
        else current
    )
    baseline_digest = (
        digest(profile.get("dev_tasks", key))
        if request["operation"] == "dev-commit"
        else digest(baseline)
    )
    if request["operation"] == "voice-cloud":
        from .voice import cloud_baseline

        baseline_digest = cloud_baseline(profile)
    if request["operation"] in {"export", "capture"}:
        import os

        baseline_digest = (
            "occupied"
            if os.path.lexists(profile.directory / "exports" / key)
            else "absent"
        )
    if (
        not allow
        or digest(descriptor) != approved_digest
        or request["digest"] != approved_digest
        or baseline_digest != request["baseline"]
    ):
        request["state"] = "denied"
    else:
        # Hardware and provider effects cannot roll back with SQLite. Durably
        # consume their approval first. An interrupted claim requires review.
        if request["operation"] in {
            "generate",
            "ai-generate",
            "speech",
            "voice-cloud",
            "export",
            "browse",
            "copy",
            "tone",
            "capture",
        }:
            request["state"] = "in_progress"
            profile.put("requests", token, request)
            profile.db.commit()
        try:
            if request["operation"] == "dev-commit":
                from .development import commit

                request["result"] = commit(profile, profile.get("dev_tasks", key))
            elif request["operation"] == "install":
                runtime = install(profile, request["content"])
                record = {
                    "manifest": request["content"],
                    "runtime": runtime,
                    "registration": uuid4().hex,
                }
                probe = execute(profile, record, "Ray")
                expected = {
                    "upper": "RAY",
                    "lower": "ray",
                    "count": "3",
                    "sha256": hashlib.sha256(b"Ray").hexdigest(),
                    "reverse": "yaR",
                }
                require(
                    probe == expected[record["manifest"]["operation"]],
                    "skill verification failed",
                )
                profile.put("history", digest(record), record)
                profile.put("skills", key, record)
                request["result"] = record
            elif request["operation"] == "run":
                request["result"] = execute(profile, current, request["content"])
            elif request["operation"] == "capture":
                from .visual import capture

                request["result"] = capture(profile, key)
            elif request["operation"] == "export":
                from .exports import write

                request["result"] = write(profile, key, request["content"])
            elif request["operation"] in {"browse", "copy", "tone"}:
                from .desktop import perform

                request["result"] = perform(request["operation"], request["content"])
            elif request["operation"] == "voice-cloud":
                import threading
                from .voice_audio import cloud_pcm, playback

                stop = stop or threading.Event()
                data = request["content"]
                playback(cloud_pcm(profile, data, stop), data["speaker"], stop)
                if stop.is_set():
                    raise engine.Cancelled("speech interrupted")
                request["result"] = {"played": True}
            elif request["operation"] == "speech":
                from .speech import speech

                request["result"] = speech(request["content"])
            elif request["operation"] == "ai-generate":
                request["result"] = engine.execute(
                    profile, token, request["content"], baseline, emit
                )
            elif request["operation"] == "generate":
                from .provider import generate

                request["result"] = generate(
                    request["content"], baseline.get("provider_port", 8080)
                )
            elif request["operation"] == "remove":
                profile.drop("skills", key)
            else:
                raise ValueError("unknown operation")
            if not profile.db.in_transaction:
                profile.db.execute("BEGIN IMMEDIATE")
            if any(contains(request.get("result"), value) for value in values(profile)):
                request["result"] = "[redacted]"
            if request["operation"] == "ai-generate" and profile.get(
                "ai_cancel", token
            ):
                raise engine.Cancelled("generation cancelled")
            request["state"] = "completed"
        except engine.Cancelled:
            request.pop("result", None)
            request["state"] = "cancelled"
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            request.update(state="failed", error=str(exc))
    profile.put("requests", token, request)
    return {"id": token, **request}
