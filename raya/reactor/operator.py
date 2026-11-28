"""Tk-owned review windows. Background workers receive data only."""

import json
import queue
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import engine
from ..core import Profile
from . import services


def execute(directory, ident, fingerprint, allow, out, stopping):
    p = Profile(directory)
    try:

        def emit(delta):
            while not stopping.is_set():
                try:
                    out.put(("delta", delta), timeout=0.1)
                    break
                except queue.Full:
                    continue

        with p.transaction():
            result = services.review(
                p, ident, fingerprint, allow and not stopping.is_set(), emit
            )
        value = services.visible(
            p, {k: result[k] for k in ("state", "result", "error") if k in result}
        )
        if not stopping.is_set():
            out.put(("result", value), timeout=2)
    except Exception as exc:
        if not stopping.is_set():
            out.put(("error", services.visible(p, str(exc))), timeout=2)
    finally:
        p.db.close()


class Operator:
    def __init__(self, app):
        self.a = app
        self.p = app.profile
        self.window = None
        self.thread = None
        self.proc = None
        self.output = queue.Queue(maxsize=64)
        self.stopping = threading.Event()
        self.active = None
        self.owned = set()
        self.log = ""
        self.timer = None
        self.done = None
        self.existing_request = None
        self.control_command = None

    def show(self, value, append=False):
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2)
        )
        self.log = services.visible(
            self.p, (self.log + ("" if append else "\n") + text)[-65536:]
        )
        if self.window is None or not self.window.winfo_exists():
            self.window = tk.Toplevel(self.a.root)
            self.window.title("R.A.Y.A • Konuşma ve işlem sonuçları")
            self.box = tk.Text(self.window, wrap="word", width=80, height=22)
            self.box.pack(fill="both", expand=True)
            ttk.Button(
                self.window, text="İsteği iptal et", command=self.cancel_active
            ).pack()
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", self.log)
        self.box.see("end")
        self.box.configure(state="disabled")

    def error(self, exc):
        self.a.toast(str(services.visible(self.p, str(exc))))

    def submit(self, text):
        try:
            with self.p.transaction():
                result = services.command(self.p, text)
            if isinstance(result, dict) and result.get("state") == "pending":
                self.track(result)
                self.show("Siz: " + text)
                self.inspect(result["id"])
            else:
                self.show(services.visible(self.p, result))
        except (ValueError, OSError, RuntimeError) as e:
            self.error(e)

    def track(self, result):
        if isinstance(result, dict):
            for r in (result, result.get("approval")):
                if isinstance(r, dict) and r.get("id") and r.get("state") == "pending":
                    if r["id"] != self.existing_request:
                        self.owned.add(r["id"])

    def inspect(self, ident):
        req = self.p.get("requests", ident)
        if not req:
            return
        if services.protected(self.p, req):
            self.a.toast("Korunan içerik: inceleme engellendi.")
            return
        dlg = tk.Toplevel(self.a.root)
        dlg.title("İstek incelemesi")
        view = tk.Text(dlg, width=86, height=24, wrap="word")
        view.pack(fill="both", expand=True)
        view.insert("1.0", json.dumps(req, ensure_ascii=False, indent=2))
        view.configure(state="disabled")

        def choose(allow):
            if self.thread or self.proc:
                return self.a.toast("Önce çalışan işlemi tamamlayın.")
            dlg.destroy()
            self.active = ident
            self.thread = threading.Thread(
                target=execute,
                args=(
                    self.p.directory,
                    ident,
                    req["digest"],
                    allow,
                    self.output,
                    self.stopping,
                ),
                name="reactor-approval",
            )
            self.thread.start()
            self.schedule()

        if req["state"] == "pending":
            ttk.Button(dlg, text="Onayla", command=lambda: choose(True)).pack(
                side="left"
            )
            ttk.Button(dlg, text="Reddet", command=lambda: choose(False)).pack(
                side="left"
            )
        if req["state"] == "in_progress" and req["operation"] == "ai-generate":

            def interrupt():
                with self.p.transaction():
                    result = engine.cancel(self.p, ident)
                dlg.destroy()
                self.show(result["state"])

            ttk.Button(dlg, text="Üretimi iptal et", command=interrupt).pack(
                side="left"
            )
        ttk.Button(dlg, text="Kapat", command=dlg.destroy).pack(side="right")

    def cancel_active(self):
        if self.active:
            try:
                with self.p.transaction():
                    result = services.cancel(self.p, self.active)
                self.show(result["state"])
            except ValueError as e:
                self.error(e)

    def control(self, cmd, params, done=None):
        if self.proc or self.thread:
            return self.a.toast("Önce çalışan işlemi tamamlayın.")
        if services.protected(self.p, params):
            return self.a.toast("Korunan veri")
        # Core CLI owns a separate profile connection and never touches this Tk interpreter.
        self.control_command = cmd
        self.existing_request = (
            self.p.get("dev_tasks", params.get("id", ""), {}).get("request")
            if cmd == "dev-step"
            else None
        )
        package_root = Path(__file__).resolve().parents[2]
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "raya",
                "--profile",
                str(self.p.directory),
                cmd,
                "-",
            ],
            cwd=package_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.done = done
        self.thread = threading.Thread(
            target=collect,
            args=(self.proc, params, self.output),
            name="reactor-control",
        )
        self.thread.start()
        self.schedule()

    def schedule(self):
        self.timer = self.a.root.after(30, self.poll)

    def poll(self):
        self.timer = None
        for _ in range(32):
            try:
                kind, value = self.output.get_nowait()
            except queue.Empty:
                break
            if kind == "delta":
                self.show(value, True)
            elif kind == "error":
                self.error(value)
            else:
                self.track(value)
                value = services.visible(self.p, value)
                if self.done:
                    self.done(value)
                else:
                    self.show(value)
        if self.thread and (self.thread.is_alive() or not self.output.empty()):
            self.schedule()
        else:
            if (
                self.active
                and self.p.get("requests", self.active, {}).get("state") != "pending"
            ):
                self.owned.discard(self.active)
            self.thread = self.proc = self.active = self.done = (
                self.existing_request
            ) = None
            self.a.refresh_data(force=True)

    def choose(self, title, entries, callback):
        if not entries:
            return self.a.toast("Kayıt bulunmuyor.")
        dlg = tk.Toplevel(self.a.root)
        dlg.title(title)
        listing = tk.Listbox(dlg, width=84, height=14, exportselection=False)
        listing.pack(fill="both", expand=True)
        for key, label in entries:
            listing.insert("end", label)

        def select(*_):
            picked = listing.curselection()
            if picked:
                key = entries[picked[0]][0]
                dlg.destroy()
                callback(key)

        listing.bind("<Double-1>", select)
        ttk.Button(dlg, text="Seç", command=select).pack()

    def skill(self, key):
        arg = simpledialog.askstring("Yetenek girdisi", key, parent=self.a.root)
        if arg is not None:
            self.submit("/çalıştır " + key + " " + arg)

    def manage(self, kind, ident):
        if kind == "requests":
            return self.inspect(ident)
        dlg = tk.Toplevel(self.a.root)
        dlg.title("Görev • " + ident)
        record = self.p.get(kind, ident)
        ttk.Label(
            dlg,
            text=services.visible(
                self.p, record.get("title") or record.get("reason") or ident
            ),
            wraplength=500,
        ).pack()

        def run(cmd):
            if cmd in (
                "dev-rollback",
                "dev-cancel",
                "goal-state",
            ) and not messagebox.askyesno(
                "İşlem onayı", cmd + " • " + ident, parent=dlg
            ):
                return
            dlg.destroy()
            self.control(
                cmd,
                {"goal": ident, "state": "cancelled"}
                if cmd == "goal-state"
                else {"id": ident},
            )

        buttons = []
        if kind == "goals":
            if record["state"] != "cancelled":
                buttons.append(("İptal", "goal-state"))
        else:
            phase = record["phase"]
            if phase in (
                "planning",
                "paused",
                "repair",
                "plan_approval",
                "patch_approval",
            ):
                buttons.append(("İlerle", "dev-step"))
            if phase == "review":
                buttons.append(("İncele", "dev-review"))
            if phase not in ("rolled_back", "cancelled", "proposed"):
                buttons.append(("İptal", "dev-cancel"))
            if phase != "rolled_back":
                buttons.append(("Geri al", "dev-rollback"))
        for label, cmd in buttons:
            ttk.Button(dlg, text=label, command=lambda c=cmd: run(c)).pack(side="left")

    def close(self):
        self.stopping.set()
        if self.timer:
            self.a.root.after_cancel(self.timer)
        # Let local transactions finish and collect their exact proposal IDs.
        # Interrupt long discovery/authorization waits without touching other requests.
        if (
            self.proc
            and self.proc.poll() is None
            and self.control_command in {"cloud-discover", "cloud-quota", "cloud-login"}
        ):
            self.proc.terminate()
        if self.active:
            with self.p.transaction():
                r = self.p.get("requests", self.active)
                if (
                    r
                    and r["operation"] == "ai-generate"
                    and r["state"] in ("pending", "in_progress")
                ):
                    engine.cancel(self.p, self.active)
        if self.thread:
            self.thread.join()
        while not self.output.empty():
            kind, value = self.output.get_nowait()
            if kind == "result":
                self.track(value)
        with self.p.transaction():
            for i in self.owned:
                r = self.p.get("requests", i)
                if r and r["state"] == "pending":
                    services.cancel(self.p, i)


def collect(proc, params, out):
    try:
        stdout, stderr = proc.communicate(json.dumps(params).encode())
        if proc.returncode:
            out.put(("error", stderr.decode("utf8", "replace")[:1000]))
        else:
            out.put(("result", json.loads(stdout)))
    except (ValueError, OSError) as e:
        out.put(("error", str(e)))
