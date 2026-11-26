"""Approved document and handoff exports into a profile-owned directory."""

import hashlib
import html
import os
import re
import tempfile
from email.message import EmailMessage

from .storage import child
from .core import propose, require, text_field


def render(kind, fields):
    if kind == "audio":
        import io
        import math
        import wave

        frequency, duration = fields["frequency"], fields["milliseconds"]
        require(
            type(frequency) is int and 100 <= frequency <= 4000, "invalid frequency"
        )
        require(type(duration) is int and 50 <= duration <= 2000, "invalid duration")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setparams((1, 1, 16000, 0, "NONE", "not compressed"))
            output.writeframes(
                bytes(
                    round(128 + 48 * math.sin(i * 2 * math.pi * frequency / 16000))
                    for i in range(16 * duration)
                )
            )
        return buffer.getvalue()
    if kind == "document":
        return text_field(fields["text"], "text", 65536).encode()
    if kind == "message":
        msg = EmailMessage()
        msg["To"] = text_field(fields["to"], "to", 320)
        msg["Subject"] = text_field(fields["subject"], "subject", 256)
        msg.set_content(text_field(fields["text"], "text", 65536))
        return msg.as_bytes()
    require(kind == "goal-report", "invalid export format")
    goal = fields["goal"]
    rows = "".join(
        "<li>"
        + html.escape(t["title"])
        + " ("
        + html.escape(t["state"])
        + ")<pre>"
        + html.escape(t.get("evidence", ""))
        + "</pre></li>"
        for t in goal["tasks"].values()
    )
    title = html.escape(goal["title"])
    return (
        f'<!doctype html><meta charset="utf-8"><title>{title}</title><h1>{title}</h1><ol>{rows}</ol>'
    ).encode()


def plan(profile, args):
    kind = args["format"]
    ext = {"audio": "wav", "document": "txt", "message": "eml", "goal-report": "html"}
    require(kind in ext, "invalid format")
    name = args["name"]
    require(
        isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name),
        "invalid filename",
    )
    require(
        name.upper()
        not in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(10)),
            *(f"LPT{i}" for i in range(10)),
        },
        "reserved filename",
    )
    fields = {
        k: v
        for k, v in args.items()
        if k in {"text", "subject", "to", "frequency", "milliseconds"}
    }
    if kind == "goal-report":
        fields = {"goal": profile.get("goals", args["goal"])}
        require(fields["goal"], "unknown goal")
    data = render(kind, fields)
    dest = f"{name}.{ext[kind]}"
    target = child(profile.directory, "exports", directory=True) / dest
    require(
        not target.parent.is_symlink() and not os.path.lexists(target),
        "destination unavailable",
    )
    return propose(
        profile,
        "export",
        dest,
        dict(format=kind, fields=fields, sha256=hashlib.sha256(data).hexdigest()),
        "absent",
        ["profile.export"],
    )


def write(profile, dest, content):
    directory = child(profile.directory, "exports", directory=True)
    require(not directory.is_symlink(), "unsafe export directory")
    directory.mkdir(exist_ok=True, mode=0o700)
    target = child(directory, dest)
    data = render(content["format"], content["fields"])
    require(hashlib.sha256(data).hexdigest() == content["sha256"], "artifact changed")
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
        temporary = out.name
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
            out.close()
            os.link(temporary, target)
        finally:
            os.unlink(temporary)
    return {"path": str(target.resolve()), "sha256": content["sha256"]}
