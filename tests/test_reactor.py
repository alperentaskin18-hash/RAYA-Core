"""Operator-facing REACTOR behavior with real temporary profiles and synthetic inputs."""

import hashlib
import importlib.resources as resources
import json
import math
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from raya import engine, cloud, development, goals, vault
from raya.core import Profile, decide, plan_install
from raya.reactor import services, raya_hud as style
from raya.reactor.live_ui import LiveApp
from raya.reactor.weather import Weather


class ReactorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.p = Profile(tmp.name)
        self.addCleanup(self.p.db.close)

    def install(self):
        m = dict(
            provider="fixture",
            action="reverse",
            version="1",
            license="test",
            operation="reverse",
            runtime="raya-text-1",
            permissions=[],
            input="string",
            output="string",
        )
        with self.p.transaction():
            req = plan_install(self.p, m)
            decide(self.p, req["id"], req["digest"], True)

    def test_approved_skill_and_request_cancellation(self):
        self.install()
        with self.p.transaction():
            req = services.command(self.p, "/çalıştır fixture/reverse abc")
            self.assertEqual(
                services.review(self.p, req["id"], req["digest"], True)["result"], "cba"
            )
            second = services.command(self.p, "/çalıştır fixture/reverse abc")
            self.assertEqual(services.cancel(self.p, second["id"])["state"], "denied")
        self.assertEqual(
            services.snapshot(self.p)["counts"], {"completed": 2, "denied": 1}
        )

    def test_profile_quota_and_strict_admission(self):
        cfg = {
            "routes": {
                "gateway": {"provider": "omniroute", "model": "auto/best-coding"},
                "google": {"provider": "gemini", "model": "configured"},
            },
            "offline": False,
        }
        with self.p.transaction():
            engine.configure(self.p, cfg)
            vault.store(self.p, "ai-omniroute", "fixture-private-value")
            self.p.put(
                "cloud_catalog",
                "gateway",
                {"state": "ready", "models": [{"id": "fixture-private-value"}]},
            )
        before = self.p.settings()
        services.prefs(self.p, paused=True, fps=30)
        view = services.snapshot(self.p)
        self.assertTrue(view["strict"])
        self.assertEqual(view["routes"]["gateway"]["state"], "Katalog hazır")
        self.assertEqual(view["routes"]["google"]["state"], "Anahtar eksik")
        self.assertEqual(view["quota"], "Bilinmiyor")
        self.assertNotIn("fixture-private-value", json.dumps(view))
        with self.assertRaises(ValueError):
            services.command(self.p, "merhaba")
        self.assertFalse(self.p.all("requests"))
        self.assertEqual(before, self.p.settings())
        self.assertEqual(services.prefs(self.p)["fps"], 30)

    def test_streamed_command_and_cancel_share_core_approval(self):
        route = {
            "provider": "openrouter",
            "model": "sample:free",
            "features": ["stream"],
        }
        with self.p.transaction():
            engine.configure(self.p, {"offline": False, "routes": {"free": route}})
            vault.store(self.p, "ai-openrouter", "fixture-api-credential")
            with patch.object(
                cloud,
                "get_json",
                side_effect=[
                    (
                        {
                            "data": [
                                {
                                    "id": "sample:free",
                                    "pricing": {"prompt": "0", "completion": "0"},
                                }
                            ]
                        },
                        {},
                    ),
                    ({"data": {"is_free_tier": True}}, {}),
                ],
            ):
                cloud.discover(self.p, "free")
            req = services.command(self.p, "/akış merhaba")
            self.assertTrue(req["content"]["zero_cost"])
            self.assertEqual(services.cancel(self.p, req["id"])["state"], "cancelled")
            req = services.command(self.p, "/akış merhaba")
        received = []

        def exchange(profile, token, route, content, reply):
            reply.emit("örnek yanıt")
            return {"text": "örnek yanıt", "usage": {}}

        with patch.object(engine, "exchange", exchange), self.p.transaction():
            result = services.review(
                self.p, req["id"], req["digest"], True, received.append
            )
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual("".join(received), "örnek yanıt")

    def test_goal_and_development_controls(self):
        with self.p.transaction():
            goal = goals.change(self.p, "goal-create", {"title": "fixture"})
            services.cancel(self.p, goal["id"])
            # This case uses Git sources. Installed wheels have a separate asset check.
            repo = self.p.directory / "fixture-repo"
            target = repo / "development_targets" / "percent.py"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"def solve(value):\n    return None\n")
            development.git(repo, "init", "-q")
            development.git(repo, "add", ".")
            development.git(repo, "commit", "-qm", "test fixture")
            task = development.start(
                self.p,
                {
                    "repository": str(repo),
                    "kind": "repair",
                },
            )
            services.cancel(self.p, task["id"])
            self.assertEqual(
                development.cancel(self.p, task["id"], True)["phase"], "rolled_back"
            )
        self.assertEqual(
            services.snapshot(self.p)["goals"][goal["id"]]["state"], "cancelled"
        )

    def test_reference_assets_and_geometry(self):
        hashes = {
            "reference.png": "cf7943eb985cbc24d7df17676f5d08e4ef4f43332d8e1b8d36b3c2f7a72f7cf6",
            "reactor_clean.png": "0da401f3e27dd9f316b930be5b96da5c9851fa589e56d7070a6dbfb5fdaf3de3",
            "reactor_art.png": "9ba6262b766a18fbf1175ec7493174ac68cae887765084dbe93803017df68c07",
        }
        for name, sha in hashes.items():
            self.assertEqual(
                hashlib.sha256(
                    resources.files("raya.reactor").joinpath(name).read_bytes()
                ).hexdigest(),
                sha,
            )
        self.assertAlmostEqual(math.degrees(style.ORBIT_TILT), -21)
        self.assertEqual(
            (style.ORBIT_PERIOD_SECONDS, style.CORE_PERIOD_SECONDS), (12, 80)
        )
        self.assertGreater(
            style.orbit_visibility(math.pi / 2, 0, 0, 0, 0),
            style.orbit_visibility(-math.pi / 2, 0, 0, 0, 0),
        )
        self.assertFalse(Weather().enabled)

    @unittest.skipUnless(
        os.name == "nt" or os.environ.get("DISPLAY"), "Tk display required"
    )
    def test_navigation_review_voice_toast_and_shutdown(self):
        self.install()
        app = LiveApp(self.p)
        self.addCleanup(lambda: None if app.closed else app.close())
        app.root.update()
        self.assertTrue(app.full)
        app.set_full(False)
        app.root.geometry("900x600")
        app.root.update()
        app.layout()
        self.assertAlmostEqual(
            app.s,
            min(
                app.canvas.winfo_width() / style.W, app.canvas.winfo_height() / style.H
            ),
        )
        for name, *_ in style.NAV:
            app.navigate(name)
            self.assertEqual(app.selected, name)
            if name != "Ana Sayfa":
                self.assertTrue(app.canvas.find_withtag("page"))
        with patch.object(
            app.core, "render", side_effect=AssertionError("off-home frame")
        ):
            app.root.after(180, app.root.quit)
            app.root.mainloop()
        self.assertFalse(app.weather.enabled)
        app.navigate("Ana Sayfa")
        app.entry.delete(0, "end")
        app.entry.insert(0, "/çalıştır fixture/reverse merhaba")
        app.submit()
        pending = next(
            i for i, r in self.p.all("requests").items() if r["state"] == "pending"
        )
        review = next(
            w
            for w in app.root.winfo_children()
            if w.winfo_class() == "Toplevel" and w.title() == "İstek incelemesi"
        )
        approve = next(
            w
            for w in review.winfo_children()
            if w.winfo_class() == "TButton" and w.cget("text") == "Onayla"
        )
        approve.invoke()
        deadline = time.monotonic() + 4
        while app.operator.thread and time.monotonic() < deadline:
            app.root.update()
            time.sleep(0.005)
        self.assertIn("abahrem", app.operator.log)
        self.assertEqual(self.p.get("requests", pending)["state"], "completed")
        with (
            patch("raya.voice_audio.capture") as capture,
            patch("tkinter.messagebox.askyesno", lambda *a, **kw: False),
        ):
            from types import SimpleNamespace

            x, y = app.xy(1280, 800)
            app.click(SimpleNamespace(x=x, y=y))
            panel = app.root.children["voice"]
            buttons = panel.winfo_children()[-1].winfo_children()
            next(w for w in buttons if w.cget("text") == "Enable session").invoke()
            capture.assert_not_called()
            next(w for w in buttons if w.cget("text") == "End session").invoke()
        app.toast("bildirim")
        self.assertEqual(app.NOTICE_MS, 4000)
        app.root.after(3800, app.root.quit)
        app.root.mainloop()
        self.assertEqual(app.notice, "bildirim")
        app.root.after(400, app.root.quit)
        app.root.mainloop()
        self.assertEqual(app.notice, "")
        app.close()
        self.assertFalse(app.monitor.thread.is_alive())
