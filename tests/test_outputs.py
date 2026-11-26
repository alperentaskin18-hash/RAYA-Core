import email
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from importlib.util import find_spec


class Outputs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.profile = Path(temp.name)

    def call(self, command, args):
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "raya",
                "--profile",
                str(self.profile),
                command,
                "-",
            ],
            input=json.dumps(args),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(p.stdout)

    def approved(self, command, args):
        request = self.call(command, args)
        result = self.call(
            "approve", {"id": request["id"], "digest": request["digest"]}
        )
        self.assertEqual(result["state"], "completed", result)
        return result["result"]

    def test_media_message_report(self):
        audio = self.approved(
            "export",
            dict(format="audio", name="signal", frequency=400, milliseconds=250),
        )
        with wave.open(audio["path"], "rb") as source:
            self.assertEqual(
                (source.getnframes(), source.getframerate()), (4000, 16000)
            )
            self.assertGreater(len(set(source.readframes(4000))), 10)
        draft = self.approved(
            "export",
            dict(
                format="message",
                name="notice",
                to="review@example.invalid",
                subject="Read sample",
                text="Sample ready",
            ),
        )
        msg = email.message_from_bytes(Path(draft["path"]).read_bytes())
        self.assertEqual(msg["Subject"], "Read sample")
        goal = self.call("goal-create", {"title": "<branch>"})
        report = self.approved(
            "export", dict(format="goal-report", name="summary", goal=goal["id"])
        )
        self.assertIn("&lt;branch&gt;", Path(report["path"]).read_text())

    @unittest.skipUnless(
        find_spec("PIL") and (os.name == "nt" or os.environ.get("DISPLAY")),
        "capture requires Pillow and a display",
    )
    def test_screen_and_animation(self):
        from PIL import Image

        result = self.approved("capture", {"name": "desktop.png"})
        image = self.call("inspect-image", {"name": "desktop.png"})
        self.assertEqual([image["width"], image["height"]], result["size"])
        directory = self.profile / "exports"
        self.assertEqual(
            result["sha256"],
            hashlib.sha256((directory / "desktop.png").read_bytes()).hexdigest(),
        )
        first = Image.new("RGB", (6, 4), "red")
        first.save(
            directory / "two.gif",
            save_all=True,
            append_images=[Image.new("RGB", (6, 4), "blue")],
            duration=100,
        )
        self.assertEqual(
            self.call("inspect-image", {"name": "two.gif", "frame": 1})["mean_rgb"],
            [0, 0, 255],
        )
