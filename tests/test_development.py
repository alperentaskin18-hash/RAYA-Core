"""Development orchestration fixtures. No solution to the three live tasks is supplied."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from raya import cloud, development as dev, engine, vault
from raya.core import Profile, decide, digest


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.p = Profile(self.root / "profile")
        self.addCleanup(self.p.db.close)
        repo = self.repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        targets = repo / "development_targets"
        targets.mkdir()
        (targets / "fixture.py").write_text(
            "def solve(value):\n    return None\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.base = dev.git(repo, "rev-parse", "HEAD")
        scope = patch.dict(
            dev.TARGETS,
            {"feature": ("fixture", "Return the supplied synthetic scalar.")},
        )
        scope.start()
        self.addCleanup(scope.stop)

    def start(self):
        return dev.start(self.p, {"kind": "feature", "repository": str(self.repo)})[
            "id"
        ]

    def authorize_fixture(self):
        r = {"provider": "openrouter", "model": "fixture:free"}
        vault.store(self.p, "ai-openrouter", "synthetic-development-key")
        engine.configure(self.p, {"offline": False, "routes": {"test": r}})

        def get(p, route, path):
            return (
                {"data": {"is_free_tier": True}}
                if path.endswith("/key")
                else {
                    "data": [
                        {
                            "id": "fixture:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                        }
                    ]
                }
            ), None

        with patch.object(cloud, "get_json", side_effect=get):
            cloud.discover(self.p, "test")

    def answer(self, req, text):
        with (
            patch.object(
                engine,
                "exchange",
                return_value={"text": text, "usage": {}, "tool_calls": []},
            ),
            self.p.transaction(),
        ):
            result = decide(self.p, req["id"], req["digest"], True)
        self.assertEqual(result["state"], "completed", result)

    def test_pause_resume_cancellation_and_zero_disclosure(self):
        ident = self.start()
        with patch.object(engine, "exchange") as transport:
            self.assertEqual(dev.step(self.p, ident)["phase"], "paused")
            self.assertEqual(self.p.all("requests"), {})
            self.authorize_fixture()
            req = dev.step(self.p, ident)["approval"]
            dev.cancel(self.p, ident)
            self.assertEqual(self.p.get("requests", req["id"])["state"], "cancelled")
            transport.assert_not_called()
        self.assertEqual(dev.cancel(self.p, ident, True)["phase"], "rolled_back")

    def test_bounded_repair_never_changes_checkout(self):
        self.authorize_fixture()
        ident = self.start()
        self.answer(dev.step(self.p, ident)["approval"], "fixture plan")
        for i in range(3):
            req = dev.step(self.p, ident)["approval"]
            self.answer(req, '{"path":"raya/core.py","replacement":"untrusted"}')
            result = dev.step(self.p, ident)
            self.assertEqual(result["phase"], "stopped" if i == 2 else "repair")
        self.assertEqual(dev.git(self.repo, "status", "--porcelain"), b"")
        self.assertEqual(dev.git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            next(iter(self.p.all("cloud_evaluations").values()))["passes"], 0
        )

    def test_os_confinement_and_output_comparison(self):
        if sys.platform.startswith("linux"):
            self.assertEqual(
                dev.sandbox("def solve(value):\n    return len(value)", [[4, 2]])[0],
                [2],
            )
        else:
            # Hosted Windows needs a separately installed WSL distro for execution.
            try:
                values, _ = dev.sandbox(
                    "def solve(value):\n    return len(value)", [[4, 2]]
                )
            except ValueError as exc:
                self.assertIn("restricted", str(exc))
            else:
                self.assertEqual(values, [2])
        for src in (
            'def solve(x):\n    return open("/dev/null").read()',
            "def solve(x):\n    import socket\n    return socket.socket()",
            "def solve(x):\n    return x.__class__",
        ):
            with self.assertRaises(ValueError):
                dev.sandbox(src, [3])

    def test_approved_fixture_commit_and_rollback(self):
        self.authorize_fixture()
        ident = self.start()
        self.answer(dev.step(self.p, ident)["approval"], "fixture planning reply")
        req = dev.step(self.p, ident)["approval"]
        t = self.p.get("dev_tasks", ident)
        self.answer(
            req,
            json.dumps(
                {
                    "base_digest": digest(t["original"]),
                    "replacement": "def solve(value):\n    return value\n",
                }
            ),
        )
        with patch.object(dev, "cases", return_value=([4, 5], [4, 5])):
            if os.name == "nt":
                # Git/approval integration independent of optional WSL. OS refusal is tested separately.
                with patch.object(dev, "sandbox", return_value=([4, 5], 1)):
                    result = dev.step(self.p, ident)
            else:
                result = dev.step(self.p, ident)
        self.assertEqual(result["phase"], "review", result)
        self.assertEqual(dev.git(self.repo, "rev-parse", "HEAD"), self.base)
        for allow in (False, True):
            req = dev.commit_proposal(self.p, ident)
            with self.p.transaction():
                result = decide(self.p, req["id"], req["digest"], allow)
            self.assertEqual(
                result["state"], "completed" if allow else "denied", result
            )
        sha = self.p.get("dev_tasks", ident)["proposed_commit"]
        self.assertEqual(
            dev.git(self.repo, "diff", "--name-only", self.base.decode().strip(), sha)
            .decode()
            .strip(),
            "development_targets/fixture.py",
        )
        self.assertEqual(dev.cancel(self.p, ident, True)["phase"], "rolled_back")
        self.assertEqual(dev.git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            dev.git(self.repo, "for-each-ref", "refs/heads/proposals/"), b""
        )
