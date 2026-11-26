"""Consent-bound desktop actions. Never evaluate user text as shell input."""

import os
import shutil
import subprocess
import webbrowser
from urllib.parse import urlsplit

from .core import digest, propose, require, text_field


def plan(profile, command, args):
    if command == "browse":
        url = text_field(args["url"], "url", 2048)
        parts = urlsplit(url)
        require(
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
            and not any(ord(c) < 33 for c in url),
            "expected an HTTPS URL",
        )
        content = {"url": url}
        destination = url
        permission = "browser.open"
    elif command == "copy":
        require(
            os.name == "nt" or shutil.which("wl-copy") or shutil.which("xclip"),
            "clipboard unavailable",
        )
        content = {"text": text_field(args["text"], "text", 16384)}
        destination, permission = "system clipboard", "clipboard.write"
    else:
        require(os.name == "nt", "Windows audio output required")
        frequency, duration = args["frequency"], args["milliseconds"]
        require(
            type(frequency) is int and 100 <= frequency <= 4000, "invalid frequency"
        )
        require(type(duration) is int and 50 <= duration <= 2000, "invalid duration")
        content = {"frequency": frequency, "milliseconds": duration}
        destination, permission = "Windows audio output", "audio.output"
    return propose(profile, command, destination, content, digest(None), [permission])


def perform(command, data):
    if command == "browse":
        require(webbrowser.open_new_tab(data["url"]), "browser launch unavailable")
    elif command == "tone":
        import winsound

        winsound.Beep(data["frequency"], data["milliseconds"])
    else:
        if os.name == "nt":
            import tkinter as tk

            root = tk.Tk()
            try:
                root.withdraw()
                root.clipboard_clear()
                root.clipboard_append(data["text"])
                root.update()
            finally:
                root.destroy()
        else:
            cmd = (
                ["wl-copy"]
                if shutil.which("wl-copy")
                else ["xclip", "-selection", "clipboard"]
            )
            try:
                subprocess.run(
                    cmd,
                    input=data["text"].encode(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=True,
                )
            except subprocess.SubprocessError as exc:
                raise RuntimeError("clipboard service unavailable") from exc
    return {"completed": True}
