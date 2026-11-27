"""Native profile editor for provider routes, budgets and credentials."""

import tkinter as tk
from tkinter import simpledialog, ttk, messagebox

from . import engine, vault
from .ai_protocol import HOSTS, LOCAL


def open_settings(root, profile):
    window = tk.Toplevel(root)
    window.title("AI providers")
    box = ttk.Frame(window, padding=12)
    box.pack(fill="both", expand=True)
    box.columnconfigure(1, weight=1)
    cfg = engine.config(profile)
    routes = dict(cfg["routes"])
    alias = tk.StringVar(value=next(iter(routes), "local"))
    provider = tk.StringVar()
    model = tk.StringVar()
    port = tk.StringVar()
    context = tk.StringVar()
    features = {name: tk.BooleanVar() for name in sorted(engine.FEATURES)}
    offline = tk.BooleanVar(value=cfg["offline"])
    preferred = tk.StringVar(value=cfg["preferred"])
    limits = {
        key: tk.StringVar(value=str(cfg[key]))
        for key in ("max_output_tokens", "budget", "retries")
    }

    def field(row, label, var, options=None, editable=False):
        ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
        widget = (
            ttk.Entry(box, textvariable=var)
            if options is None
            else ttk.Combobox(
                box,
                textvariable=var,
                values=options,
                state="normal" if editable else "readonly",
            )
        )
        widget.grid(row=row, column=1, sticky="ew")
        return widget

    picker = field(0, "Route name", alias, list(routes), True)
    service = field(1, "Provider", provider, list(HOSTS))
    field(2, "Model ID", model)
    port_field = field(3, "Loopback port", port)
    field(4, "Context token limit", context)
    options = ttk.Frame(box)
    options.grid(row=5, columnspan=2)
    for name, var in features.items():
        ttk.Checkbutton(options, text=name.title(), variable=var).pack(side="left")
    default = field(7, "Preferred route", preferred, ["auto", *routes], True)
    field(8, "Output token limit", limits["max_output_tokens"])
    field(9, "Request token budget", limits["budget"])
    field(10, "Retries per route", limits["retries"])
    ttk.Checkbutton(box, text="Offline (local providers only)", variable=offline).grid(
        row=11, columnspan=2, sticky="w"
    )

    extra = {}
    for i, key in enumerate(
        ("account", "project", "pool", "credential", "upstream", "responses_url"), 13
    ):
        extra[key] = tk.StringVar(window)
        field(
            i,
            {
                "credential": "Credential suffix",
                "upstream": "OmniRoute upstream",
                "responses_url": "Registered Responses URL",
            }.get(key, key.title()),
            extra[key],
        )

    def update_port(*_):
        port_field.configure(
            state="normal" if provider.get() in (*LOCAL, "omniroute") else "disabled"
        )
        port.set(
            str(engine.port({"provider": provider.get()}))
            if provider.get() in (*LOCAL, "omniroute")
            else ""
        )

    def load(*_):
        route = routes.get(alias.get(), {"provider": "llama.cpp", "model": ""})
        for key, var in extra.items():
            var.set(route.get(key, ""))
        provider.set(route["provider"])
        model.set(route["model"])
        update_port()
        if "port" in route:
            port.set(str(route["port"]))
        context.set(str(route.get("context_tokens", 32768)))
        for name, var in features.items():
            var.set(name in route.get("features", []))

    def route_fields():
        r = {
            "provider": provider.get(),
            "model": model.get(),
            "context_tokens": int(context.get()),
            "features": [name for name, var in features.items() if var.get()],
        }
        r.update({key: var.get() for key, var in extra.items() if var.get()})
        if r["provider"] in (*LOCAL, "omniroute"):
            r["port"] = int(port.get())
        return r

    def save():
        try:
            updated = dict(routes)
            if alias.get():
                updated[alias.get()] = route_fields()
            data = dict(
                routes=updated,
                preferred=preferred.get(),
                offline=offline.get(),
                **{k: int(v.get()) for k, v in limits.items()},
            )
            with profile.transaction():
                if engine.config(profile) != cfg:
                    raise ValueError("Settings changed. Reopen the editor to refresh.")
                if any(vault.contains(data, v) for v in vault.values(profile)):
                    raise ValueError("protected value in settings")
                engine.configure(profile, data)
            window.destroy()
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("Settings", str(exc), parent=window)

    def credential():
        name = provider.get()
        if name in LOCAL:
            messagebox.showinfo(
                "Provider key", "Loopback providers do not use API keys.", parent=window
            )
            return
        value = simpledialog.askstring(
            "Store credential", name + " API key", show="*", parent=window
        )
        if value:
            try:
                with profile.transaction():
                    vault.store(
                        profile,
                        "ai-"
                        + name
                        + (
                            "-" + extra["credential"].get()
                            if extra["credential"].get()
                            else ""
                        ),
                        value,
                    )
            except (ValueError, OSError) as exc:
                messagebox.showerror("Credential", str(exc), parent=window)

    def remove():
        routes.pop(alias.get(), None)
        if preferred.get() == alias.get():
            preferred.set("auto")
        alias.set(next(iter(routes), ""))
        picker.configure(values=list(routes))
        default.configure(values=["auto", *routes])
        load()

    service.bind("<<ComboboxSelected>>", update_port)
    picker.bind("<<ComboboxSelected>>", load)
    load()
    buttons = ttk.Frame(box)
    buttons.grid(row=12, columnspan=2, sticky="ew")
    ttk.Button(buttons, text="Store API key", command=credential).pack(side="left")
    ttk.Button(buttons, text="Remove route", command=remove).pack(side="left")
    ttk.Button(buttons, text="Save settings", command=save).pack(side="right")
