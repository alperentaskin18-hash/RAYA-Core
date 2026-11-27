"""Native Tk profile inspector and action controls."""

import json
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .ai_settings import open_settings
from .core import decide, plan_run


def open_hud(profile):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError("HUD requires a graphical session") from exc
    root.title("R.A.Y.A Core")
    root.geometry("900x540")
    avatar = tk.Canvas(root, width=64, height=46, highlightthickness=0, bg="#203344")
    avatar.pack(fill="x")
    avatar.create_polygon(14, 30, 20, 9, 34, 16, 46, 9, 51, 30, 34, 39, fill="#96d9ca")
    avatar.create_line(21, 25, 27, 26, width=2)
    avatar.create_line(39, 26, 45, 25, width=2)
    avatar.create_text(68, 23, text="R.A.Y.A", anchor="w", fill="white")
    groups = ("goals", "skills", "requests")
    category = tk.StringVar(value="goals")
    bar = ttk.Frame(root, padding=8)
    bar.pack(fill="x")
    pane = ttk.Panedwindow(root, orient="horizontal")
    pane.pack(fill="both", expand=True)
    rows = tk.Listbox(pane, width=35, exportselection=False)
    details = tk.Text(pane, wrap="word", padx=12, pady=10)
    pane.add(rows)
    pane.add(details)
    previous = {}

    def selection():
        return rows.get(rows.curselection()[0]) if rows.curselection() else None

    def display(*_):
        key = selection()
        if key:
            previous[category.get()] = key
        item = profile.get(category.get(), key) if key else None
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert(
            "end", json.dumps(item, indent=2, ensure_ascii=False) if item else ""
        )
        details.configure(state="disabled")

    def refresh():
        keys = list(profile.all(category.get()))
        rows.delete(0, "end")
        for key in keys:
            rows.insert("end", key)
        key = previous.get(category.get())
        if keys:
            rows.selection_set(keys.index(key) if key in keys else 0)
        display()

    def action():
        key = selection()
        if not key:
            return
        try:
            if category.get() == "skills":
                value = simpledialog.askstring("Run skill", "Input text", parent=root)
                if value is None:
                    return
                with profile.transaction():
                    result = plan_run(profile, key, value)
                category.set("requests")
                previous["requests"] = result["id"]
            elif category.get() == "requests":
                request = profile.get("requests", key)
                if request["state"] != "pending":
                    return
                answer = messagebox.askyesno(
                    "Approve this operation?",
                    json.dumps(request, ensure_ascii=True, indent=2),
                )
                with profile.transaction():
                    decide(profile, key, request["digest"], answer)
            refresh()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Operation failed", str(exc))

    ttk.Button(
        bar, text="Provider settings", command=lambda: open_settings(root, profile)
    ).pack(side="right")

    from .cloud_hud import open_center

    ttk.Button(
        bar, text="AI control center", command=lambda: open_center(root, profile)
    ).pack(side="right")

    from .voice_hud import open_voice

    ttk.Button(bar, text="Voice", command=lambda: open_voice(root, profile)).pack(
        side="right"
    )

    for group in groups:
        ttk.Radiobutton(
            bar, text=group.title(), variable=category, value=group, command=refresh
        ).pack(side="left")
    ttk.Button(bar, text="Refresh", command=refresh).pack(side="right")
    ttk.Button(bar, text="Run skill / review request", command=action).pack(
        side="right"
    )
    rows.bind("<<ListboxSelect>>", display)
    refresh()
    root.mainloop()
