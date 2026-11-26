"""Drive actual Tk controls on a graphical display using a synthetic profile."""

import os
import tempfile
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
                widgets = []

                def visit(widget):
                    widgets.append(widget)
                    for child in widget.winfo_children():
                        visit(child)

                root.update()
                visit(root)
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
                patch("tkinter.simpledialog.askstring", return_value="nebula"),
                patch("tkinter.messagebox.askyesno", return_value=True),
            ):
                open_hud(profile)
            profile.db.close()
