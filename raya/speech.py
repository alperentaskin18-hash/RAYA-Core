"""Bounded Windows speech processes. Interrupt terminates the owned adapter."""

import json
import os
import subprocess
from pathlib import Path

from .core import digest, encode, propose, require, text_field


def adapter():
    require(os.name == "nt", "Windows speech is unavailable on this platform")
    exe = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    require(exe.is_file(), "Windows PowerShell unavailable")
    return exe


def speech(args):
    executable = adapter()
    cmd = [
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(Path(__file__).with_name("speech.ps1")),
    ]
    with subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    ) as child:
        try:
            output, _ = child.communicate(
                encode(args), timeout=args.get("seconds", 20) + 5
            )
        except (KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
            child.kill()
            child.communicate()
            raise RuntimeError("speech interrupted or timed out") from exc
        require(child.returncode == 0, "speech device or runtime unavailable")
        return json.loads(output)


def plan(profile, command, args):
    adapter()
    if command == "conversation":
        seconds = args.get("seconds", 30)
        require(type(seconds) is int and 5 <= seconds <= 90, "invalid session duration")
        wake = args.get("wake", "").strip()
        require(not wake or wake.isalpha() and len(wake) <= 20, "invalid wake word")
        prefix = wake + " " if wake else ""
        answers = {
            prefix + "goal count": f"{len(profile.all('goals'))} goals",
            prefix + "skill count": f"{len(profile.all('skills'))} skills",
            prefix + "help": "Ask for goal count or skill count",
        }
        data = dict(
            operation=command,
            seconds=seconds,
            answers=answers,
            stop=prefix + "stop",
            phrases=[*answers, prefix + "stop"],
            device="Windows default microphone",
        )
        if args.get("voice"):
            data["voice"] = text_field(args["voice"], "voice", 512)
        return propose(
            profile,
            "speech",
            data["device"],
            data,
            digest(None),
            ["microphone.capture", "audio.output", "profile.transcript"],
        )
    if command == "listen":
        data = dict(operation=command, device=text_field(args["device"], "device", 512))
        permission = "microphone.capture"
    else:
        data = dict(operation=command, text=text_field(args["text"], "text", 4096))
        if args.get("voice"):
            data["voice"] = text_field(args["voice"], "voice", 512)
        permission = "audio.output"
    return propose(
        profile,
        "speech",
        data.get("device", "Windows audio output"),
        data,
        digest(None),
        [permission],
    )
