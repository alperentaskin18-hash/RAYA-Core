"""Command interface. Each invocation accepts one JSON object and emits one JSON result."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.error import URLError

from . import goals, index, learning, provider, speech, exports, desktop, vault, visual
from .core import (
    Profile,
    decide,
    digest,
    encode,
    manifest,
    plan_install,
    plan_run,
    propose,
    require,
)


def dispatch(p, cmd, a):
    if cmd == "capabilities":
        return {
            "local": [
                "settings",
                "manifest discovery",
                "text skills",
                "code index",
                "goals",
                "reminders",
                "documents",
                "search",
                "inventory",
            ],
            "features": {
                "dashboard": "loopback HTTP or operator-configured SSH tunnel",
                "capture": "Pillow and a graphical desktop",
                "inspect-image": "Pillow",
                "clipboard": "Windows Tk, wl-copy or xclip",
                "browse": "configured system browser",
                "tone": "Windows audio device",
                "generate": "enabled loopback llama.cpp server and installed model",
                "speech": "Windows recognizer / synthesizer and matching audio hardware",
                "monitor": "local profile filesystem",
                "plan": "manual development steps",
            },
            "learn": "Public GitHub metadata connection",
            "hud": "Tk and a graphical session",
            "coding_provider": provider.available(p),
            "voice": "Windows SAPI (use devices)" if os.name == "nt" else "unavailable",
            "typing": True,
            "native_agents": False,
            "automatic_deployment": False,
        }
    if cmd in {"browse", "copy", "tone"}:
        return desktop.plan(p, cmd, a)
    if cmd == "devices":
        return speech.speech({"operation": "devices"})
    if cmd in {"speak", "listen", "conversation"}:
        return speech.plan(p, cmd, a)
    if cmd == "capture":
        return visual.plan(p, a)
    if cmd == "inspect-image":
        return visual.inspect(p, a["name"], a.get("frame", 0))
    if cmd == "export":
        return exports.plan(p, a)
    if cmd == "provider-status":
        return {
            "state": provider.available(p),
            "destination": provider.destination(p.settings()),
        }
    if cmd == "generate":
        return provider.request(p, a)
    if cmd == "settings":
        return p.settings()
    if cmd == "set":
        return p.configure(a["field"], a["value"])
    if cmd in {
        "skills",
        "goals",
        "requests",
        "history",
        "imports",
        "notes",
        "reminders",
    }:
        return p.all(cmd)
    if cmd == "discover":
        return {"skill": manifest(a), "metadata": a}
    if cmd == "plan":
        scope = a["scope"]
        acceptance = a["acceptance"]
        from .core import text_field

        text_field(scope, "scope", 4096)
        text_field(acceptance, "acceptance", 4096)
        return {
            "scope": scope,
            "acceptance": acceptance,
            "steps": [
                "Reproduce the acceptance case",
                "Identify affected interfaces and tests",
                "Implement the scoped change",
                "Run acceptance and impacted tests",
            ],
            "provider": provider.available(p),
            "dispatch": "manual",
        }
    if cmd == "install":
        return plan_install(p, a)
    if cmd == "run":
        return plan_run(p, a["skill"], a["input"])
    if cmd in {"approve", "deny"}:
        return decide(p, a["id"], a["digest"], cmd == "approve")
    if cmd == "remove":
        key = a["skill"]
        current = p.get("skills", key)
        require(current, "unknown skill")
        return propose(p, "remove", key, None, digest(current), ["profile.write"])
    if cmd == "rollback":
        record = p.get("history", a["record"])
        require(record, "unknown revision")
        return plan_install(p, record["manifest"])
    if cmd == "index":
        return index.index(p, a["root"])
    if cmd == "context":
        return index.context(p, a["root"], a["symbol"])
    if cmd == "learn":
        return learning.learn(p, a["url"], a["revision"])
    if cmd == "digest":
        return digest(a["value"])
    if cmd in {"goal-create", "goal-state", "task-add", "handoff", "collect", "review"}:
        return goals.change(p, cmd, a)
    if cmd in {"note", "remind", "search", "due", "inventory", "monitor"}:
        from .local import local

        return local(p, cmd, a)
    raise ValueError("unknown command")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Start with capabilities. Use provider-status to check the local model, "
            "ask PROMPT for a reviewed completion, or generate/approve for JSON automation."
        ),
    )
    parser.add_argument(
        "--profile", default=os.environ.get("RAYA_PROFILE", str(Path.home() / ".raya"))
    )
    parser.add_argument("command", nargs="?", default="capabilities")
    parser.add_argument(
        "arguments", nargs="?", default="{}", help="JSON object or - for stdin"
    )
    args = parser.parse_args()
    try:
        p = Profile(args.profile)
        if args.command == "ask":
            require(args.arguments != "{}", "supply a prompt after ask")
            with p.transaction():
                require(
                    not any(vault.contains(args.arguments, v) for v in vault.values(p)),
                    "protected value in prompt",
                )
                request = provider.request(p, {"prompt": args.arguments})
            # Escape control characters in the exact operation shown for consent.
            print(json.dumps(request, ensure_ascii=True, indent=2), file=sys.stderr)
            print(
                "Send this request? Type yes to approve: ",
                file=sys.stderr,
                end="",
                flush=True,
            )
            allow = sys.stdin.readline(16).strip() == "yes"
            with p.transaction():
                receipt = decide(p, request["id"], request["digest"], allow)
            print(json.dumps(receipt, ensure_ascii=True))
            if receipt["state"] != "completed":
                sys.exit(1)
            return
        if args.command == "hud":
            from .hud import open_hud

            open_hud(p)
            return
        if args.command == "secret-set":
            import getpass

            value = (
                getpass.getpass("Secret: ")
                if sys.stdin.isatty()
                else sys.stdin.readline().rstrip("\r\n")
            )
            with p.transaction():
                result = vault.store(p, args.arguments, value)
            print(json.dumps(result, ensure_ascii=True, allow_nan=False))
            return
        raw = sys.stdin.read(262145) if args.arguments == "-" else args.arguments
        require(len(raw) <= 262144, "command too large")
        a = json.loads(raw)
        require(isinstance(a, dict), "expected JSON object")
        if args.command == "dashboard":
            from .dashboard import serve

            serve(p.directory, int(a.get("port", 0)))
            return
        with p.transaction():
            require(
                not any(vault.contains(a, v) for v in vault.values(p)),
                "protected value in command",
            )
            result = dispatch(p, args.command, a)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    except (
        ImportError,
        ValueError,
        KeyError,
        TypeError,
        OSError,
        sqlite3.Error,
        URLError,
        RuntimeError,
    ) as exc:
        print(encode({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
