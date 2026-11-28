"""Local operator view; network operations run only on explicit request."""

import json
import time
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from . import cloud, engine, vault
from .ai_settings import open_settings
from .control import action


def open_center(root, p, *, background=None):
    if "cloud" in root.children:
        root.children["cloud"].lift()
        return
    panel = tk.Toplevel(root, name="cloud")
    panel.title("AI control center")
    box = ttk.Frame(panel, padding=8)
    box.pack(fill="both", expand=True)
    state = tk.StringVar(panel)
    ttk.Label(box, textvariable=state).pack(anchor="w")
    row = ttk.Frame(box)
    row.pack(fill="x")
    alias = tk.StringVar(panel)
    routes = ttk.Combobox(row, textvariable=alias, state="readonly")
    routes.pack(side="left")
    view = tk.StringVar(panel, value="Overview")
    sections = ttk.Combobox(
        box,
        textvariable=view,
        state="readonly",
        values=(
            "Overview",
            "Catalog",
            "Quotas",
            "History",
            "Rankings",
            "Tasks",
            "Accounts",
        ),
    )
    sections.pack(anchor="w")
    metric = tk.StringVar(panel)
    ttk.Label(box, textvariable=metric).pack(anchor="w")
    bar = ttk.Progressbar(box, maximum=100)
    bar.pack(fill="x")
    output = tk.Text(box, width=92, height=24, wrap="word")
    output.pack(fill="both", expand=True)
    deadline = 0
    timer = None

    def show(value):
        output.configure(state="normal")
        output.delete("1.0", "end")
        if any(vault.contains(value, secret) for secret in vault.values(p)):
            value = "[redacted]"
        output.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2)[:131072])
        output.configure(state="disabled")

    def refresh(*unused):
        nonlocal deadline
        data = cloud.report(p)
        state.set(
            "Strict zero-cost mode enabled"
            if data["strict"]
            else "Legacy profile · enable strict mode for development"
        )
        names = list(data["routes"])
        routes.configure(values=names)
        if names and alias.get() not in names:
            alias.set(names[0])
        route = data["routes"].get(alias.get(), {})
        pool = data["pools"].get(route.get("pool"), {})
        windows = pool.get("manual_windows", {})
        value = next(iter(windows.items()), None)
        bar["value"] = (
            100 * min(1, value[1]["used"] / max(1, value[1]["limit"])) if value else 0
        )
        sample = pool.get("provider_snapshot") or {}
        observed = sample.get("time", time.time())
        headers = sample.get("headers", {})
        resets = [
            cloud.reset_time(headers.get("x-ratelimit-reset-" + k), observed)
            for k in ("requests", "tokens")
        ]
        deadline = max(
            [sample.get("cooldown_until", 0)] + [r for r in resets if r is not None]
        )
        metric.set(
            f"{value[0]}: {value[1]['used']} / {value[1]['limit']} (local ceiling)"
            if value
            else "Shared quota remaining: UNKNOWN"
        )
        if not value:
            for unit in ("tokens", "requests"):
                cap = headers.get("x-ratelimit-limit-" + unit, "")
                left = headers.get("x-ratelimit-remaining-" + unit, "")
                if cap.isdecimal() and int(cap) > 0 and left.isdecimal():
                    metric.set(f"{left} / {cap} {unit} remaining · provider snapshot")
                    bar["value"] = 100 * max(0, int(cap) - int(left)) / int(cap)
                    break
        display = {
            "Overview": data["routes"],
            "Quotas": {"pools": data["pools"], "sources": data["quota_sources"]},
            "History": data["history"],
            "Rankings": data["rankings"],
            "Tasks": data["tasks"],
        }
        if view.get() == "Catalog":
            show(action(p, "cloud-catalog", {}))
        elif view.get() == "Accounts":
            show(action(p, "cloud-accounts", {}))
        else:
            show(display[view.get()])

    def execute(cmd, params=None):
        try:
            if params is None:
                raw = simpledialog.askstring(
                    cmd, "Arguments (JSON)", initialvalue="{}", parent=panel
                )
                if raw is None:
                    return
                params = json.loads(raw)
            if background:
                def complete(value):
                    if panel.winfo_exists():
                        refresh()
                        show(value)
                background(cmd, params, complete)
                return
            with p.transaction():
                if any(vault.contains(params, v) for v in vault.values(p)):
                    raise ValueError("protected value in command")
                result = action(p, cmd, params)
            refresh()
            show(result)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
            messagebox.showerror("Cloud control", str(e), parent=panel)

    def credential():
        r = engine.config(p)["routes"].get(alias.get())
        if not r:
            return
        name = cloud.key_name(r)
        secret = simpledialog.askstring(
            "Provider credential", name, show="*", parent=panel
        )
        if secret:
            try:
                with p.transaction():
                    vault.store(p, name, secret)
            except (ValueError, OSError) as e:
                messagebox.showerror("Credential", str(e), parent=panel)

    ttk.Button(row, text="Routes", command=lambda: open_settings(panel, p)).pack(
        side="left"
    )
    ttk.Button(row, text="Store key", command=credential).pack(side="left")
    ttk.Button(
        row,
        text="Test & discover",
        command=lambda: execute("cloud-discover", {"route": alias.get()}),
    ).pack(side="left")
    ttk.Button(row, text="Refresh usage", command=refresh).pack(side="left")
    ttk.Button(
        row, text="Enable strict", command=lambda: execute("cloud-init", {})
    ).pack(side="left")
    actions = (
        "cloud-catalog",
        "cloud-accounts",
        "cloud-register",
        "cloud-login",
        "cloud-refresh",
        "cloud-switch",
        "cloud-logout",
        "cloud-quota",
        "cloud-limits",
        "cloud-observation",
        "cloud-eval",
        "cloud-score",
        "dev-start",
        "dev-step",
        "dev-review",
        "dev-cancel",
        "dev-rollback",
        "dev-status",
    )
    selected = tk.StringVar(panel, value="cloud-catalog")
    controls = ttk.Frame(box)
    controls.pack(fill="x")
    ttk.Combobox(
        controls, textvariable=selected, values=actions, state="readonly"
    ).pack(side="left")
    ttk.Button(controls, text="Run", command=lambda: execute(selected.get())).pack(
        side="left"
    )
    countdown = ttk.Label(controls)
    countdown.pack(side="right")

    def tick():
        nonlocal timer
        countdown.configure(
            text=f"Reset / cooldown: {max(0, int(deadline - time.time()))} s"
            if deadline
            else "Provider reset: UNKNOWN"
        )
        timer = panel.tk.call("after", 1000, tick_name)

    def destroyed(event):
        if event.widget is panel and timer is not None:
            panel.tk.call("after", "cancel", timer)

    tick_name = panel.register(tick)
    panel.bind("<Destroy>", destroyed)
    routes.bind("<<ComboboxSelected>>", refresh)
    sections.bind("<<ComboboxSelected>>", refresh)
    refresh()
    tick()
