import platform
from datetime import datetime, timezone
from uuid import uuid4

from .core import require, text_field


def local(profile, command, args):
    if command == "monitor":
        import shutil

        space = shutil.disk_usage(profile.directory)
        return {
            "free_bytes": space.free,
            "profile_bytes": (profile.directory / "core.db").stat().st_size,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    if command == "inventory":
        return {
            "system": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        }
    if command == "search":
        query = text_field(args["query"], "query").casefold()
        return {
            key: note
            for key, note in profile.all("notes").items()
            if query in note.casefold()
        }
    if command == "due":
        now = datetime.now(timezone.utc)
        return {
            key: row
            for key, row in profile.all("reminders").items()
            if datetime.fromisoformat(row["at"]) <= now
        }
    value = text_field(args["text"], "text", 65536)
    ident = uuid4().hex
    if command == "note":
        profile.put("notes", ident, value)
    else:
        at = datetime.fromisoformat(args["at"])
        require(at.tzinfo, "reminder requires time zone")
        profile.put(
            "reminders",
            ident,
            {"text": value, "at": at.astimezone(timezone.utc).isoformat()},
        )
    return {"id": ident}
