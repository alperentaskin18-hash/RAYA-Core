"""Drive actual Tk controls on a graphical display using a synthetic profile."""

import os
import tempfile
import threading
import weakref
import tkinter as tk
import unittest
from unittest.mock import patch

from raya.core import Profile, plan_install
from raya.hud import open_hud


@unittest.skipUnless(
    os.name == "nt" or os.environ.get("DISPLAY"), "graphical session required"
)
class Hud(unittest.TestCase):
    def test_install_and_run_through_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Profile(temp)
            skill = dict(
                action="repeat",
                provider="gui",
                operation="upper",
                input="string",
                output="string",
                version="1",
                permissions=[],
                license="CC0-1.0",
                runtime="raya-text-1",
            )
            with profile.transaction():
                plan_install(profile, skill)

            def interact(root):
                root.update()
                widgets = [root]
                for widget in widgets:
                    widgets.extend(widget.winfo_children())
                controls = {w.cget("text"): w for w in widgets if "text" in w.keys()}
                controls["Requests"].invoke()
                controls["Run skill / review request"].invoke()
                controls["Skills"].invoke()
                controls["Run skill / review request"].invoke()
                controls["Run skill / review request"].invoke()
                root.update()
                self.assertIn(
                    "NEBULA",
                    [r.get("result") for r in profile.all("requests").values()],
                )
                root.destroy()

            with (
                patch.object(tk.Tk, "mainloop", interact),
                patch("tkinter.simpledialog.askstring", lambda *a, **kw: "nebula"),
                patch("tkinter.messagebox.askyesno", lambda *a, **kw: True),
            ):
                open_hud(profile)
            profile.db.close()

    def test_provider_editor_persists_model_and_limits(self):
        from raya import engine
        from raya.ai_settings import open_settings

        with tempfile.TemporaryDirectory() as temp:
            profile = Profile(temp)
            root = tk.Tk()
            root.withdraw()
            try:
                open_settings(root, profile)
                box = root.winfo_children()[0].winfo_children()[0]

                def edit(row, value):
                    widget = box.grid_slaves(row=row, column=1)[0]
                    widget.delete(0, "end")
                    widget.insert(0, value)

                edit(0, "workstation")
                edit(2, "local-test-model")
                edit(7, "workstation")
                edit(8, "42")
                buttons = box.grid_slaves(row=12)[0]
                next(
                    w
                    for w in buttons.winfo_children()
                    if w.cget("text") == "Save settings"
                ).invoke()
                self.assertEqual(len(root.winfo_children()), 0)
            finally:
                root.destroy()
                profile.db.close()
            fresh = Profile(temp)
            try:
                prefs = engine.config(fresh)
                self.assertTrue(prefs["offline"])
                self.assertEqual(prefs["preferred"], "workstation")
                self.assertEqual(prefs["max_output_tokens"], 42)
                self.assertEqual(
                    prefs["routes"]["workstation"]["model"], "local-test-model"
                )
            finally:
                fresh.db.close()

    def test_voice_shutdown_joins_capture_and_releases_tk_on_owner(self):
        from raya import voice_audio
        from raya.voice_hud import open_voice

        for destroy_parent in (False, True):
            with (
                self.subTest(destroy_parent=destroy_parent),
                tempfile.TemporaryDirectory() as temp,
            ):
                profile = Profile(temp)
                owner = threading.get_ident()
                ready = threading.Event()
                threads = []
                finalized = []
                refs = []

                def track(obj):
                    refs.append(weakref.ref(obj))
                    weakref.finalize(
                        obj, lambda: finalized.append(threading.get_ident())
                    )
                    return obj

                string_var = tk.StringVar

                def variable(*args, **kwargs):
                    self.assertEqual(threading.get_ident(), owner)
                    return track(string_var(*args, **kwargs))

                def capture(cfg, stop, held, emit):
                    threads.append(threading.current_thread())
                    ready.set()
                    if stop.wait(3):
                        # Device cleanup may still be running after the stop signal.
                        threading.Event().wait(0.04)
                        emit("microphone", False)

                root = tk.Tk()
                parent_alive = True
                try:
                    with (
                        patch.object(tk, "StringVar", variable),
                        patch.object(voice_audio, "capture", capture),
                        patch("tkinter.messagebox.askyesno", lambda *a, **kw: True),
                    ):
                        initial_timers = set(root.tk.call("after", "info"))
                        open_voice(root, profile)
                        panel = track(root.children["voice"])
                        polling = set(root.tk.call("after", "info")) - initial_timers
                        self.assertTrue(polling)
                        buttons = {
                            w.cget("text"): w
                            for w in panel.winfo_children()[-1].winfo_children()
                        }
                        buttons["Enable session"].invoke()
                        self.assertTrue(ready.wait(2))
                        if destroy_parent:
                            root.destroy()
                            parent_alive = False
                        else:
                            buttons["End session"].invoke()
                        self.assertFalse(threads[0].is_alive())
                        self.assertTrue(
                            polling.isdisjoint(root.tk.call("after", "info"))
                        )
                        del buttons, panel
                        self.assertTrue(all(ref() is None for ref in refs))
                        self.assertEqual(finalized, [owner] * len(refs))
                finally:
                    if parent_alive:
                        root.destroy()
                    profile.db.close()
