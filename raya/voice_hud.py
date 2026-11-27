"""Consent-scoped voice session. Tk owns UI state; workers never call Tk."""

import json
import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk

from . import engine, voice, voice_audio
from .core import Profile, decide


def open_voice(root, profile):
    # Only one voice session may own this HUD's devices.
    if "voice" in root.children:
        root.children["voice"].lift()
        return
    panel = tk.Toplevel(root, name="voice")
    panel.title("Voice")
    cfg = voice.settings(profile)
    gate = voice.WakeGate(cfg["wake"], cfg["language"])
    status = tk.StringVar(value="Idle")
    detail = tk.StringVar()
    partial = tk.StringVar()
    draft = tk.StringVar()
    ttk.Label(panel, textvariable=status).pack()
    canvas = tk.Canvas(panel, width=400, height=64, bg="#203344", highlightthickness=0)
    canvas.pack(fill="x")
    ttk.Label(panel, textvariable=partial, wraplength=460).pack()
    ttk.Entry(panel, textvariable=draft, width=60).pack(fill="x")
    ttk.Label(panel, textvariable=detail, wraplength=460).pack()
    row = ttk.Frame(panel)
    row.pack()
    consent = False
    listening = False
    busy = ""
    held = threading.Event()
    closed = threading.Event()
    turn = threading.Event()
    events = queue.SimpleQueue()
    history = deque(maxlen=4)
    current = None
    pending = None
    born = 0
    last_wave = deque(maxlen=1)

    def state():
        status.set(busy or ("Listening" if listening else "Idle"))

    def interrupt():
        nonlocal turn, busy, current, pending
        turn.set()
        if current:
            req = profile.get("requests", current)
            if (
                req
                and req["operation"] == "ai-generate"
                and req["state"] in ("pending", "in_progress")
            ):
                with profile.transaction():
                    try:
                        engine.cancel(profile, current)
                    except ValueError:
                        pass  # Approval completed between inspection and cancellation.
        if pending:
            with profile.transaction():
                if profile.get("requests", pending["id"])["state"] == "pending":
                    decide(profile, pending["id"], pending["digest"], False)
        turn = threading.Event()
        pending = current = None
        busy = ""
        state()

    def run(fn, tag):
        def worker():
            try:
                fn()
            except Exception as exc:
                if not closed.is_set() and not tag.is_set():
                    events.put((tag, "error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def receive(kind, value):
        if closed.is_set():
            return
        if kind == "wave":
            last_wave.append(value)
        else:
            events.put((closed, kind, value))

    def preferences():
        nonlocal gate
        dialog = tk.Toplevel(panel)
        dialog.title("Voice settings")
        dialog.transient(panel)
        dialog.grab_set()
        try:
            available = voice_audio.devices()
        except (ImportError, OSError, RuntimeError):
            available = []
        fields = {}
        choices = {
            "language": ["en", "tr"],
            "recognizer": ["pocketsphinx", "vosk"],
            "synthesis": ["espeak", "piper", *voice.CLOUD],
        }
        labels = {
            "language": "Language",
            "wake": "Wake phrase",
            "recognizer": "Recognizer",
            "asr_model": "Vosk directory",
            "synthesis": "Synthesizer",
            "tts_model": "Piper model",
            "voice": "Cloud voice ID",
            "microphone": "Microphone",
            "speaker": "Speaker",
        }
        for row, (name, label) in enumerate(labels.items()):
            value = cfg[name]
            options = choices.get(name)
            if name in ("microphone", "speaker"):
                channel = "inputs" if name == "microphone" else "outputs"
                options = ["Default"] + [
                    f"{d['index']} | {d['name']}" for d in available if d[channel]
                ]
                value = next(
                    (d for d in options if d.split(" | ")[0] == str(value)),
                    "Default" if value is None else str(value),
                )
            fields[name] = tk.StringVar(value=value)
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w")
            widget = (
                ttk.Combobox(
                    dialog, values=options, textvariable=fields[name], state="readonly"
                )
                if options
                else ttk.Entry(dialog, textvariable=fields[name])
            )
            widget.grid(row=row, column=1, sticky="ew")
            if name in ("asr_model", "tts_model"):

                def browse(key=name):
                    choose = (
                        filedialog.askdirectory
                        if key == "asr_model"
                        else filedialog.askopenfilename
                    )
                    path = choose(parent=dialog)
                    if path:
                        fields[key].set(path)

                ttk.Button(dialog, text="Browse", command=browse).grid(
                    row=row, column=2
                )
        hands = tk.BooleanVar(value=cfg["hands_free"])
        ttk.Checkbutton(dialog, text="Hands-free wake detection", variable=hands).grid(
            row=len(labels), columnspan=2
        )

        def save():
            nonlocal gate
            patch = {k: v.get() for k, v in fields.items()}
            try:
                for key in ("microphone", "speaker"):
                    patch[key] = (
                        None
                        if patch[key] == "Default"
                        else int(patch[key].split(" | ")[0])
                    )
                with profile.transaction():
                    updated = voice.configure(
                        profile, dict(patch, hands_free=hands.get())
                    )
                cfg.update(updated)
                gate = voice.WakeGate(cfg["wake"], cfg["language"])
                dialog.destroy()
            except (TypeError, ValueError) as exc:
                messagebox.showerror("Voice settings", str(exc), parent=dialog)

        ttk.Button(dialog, text="Save", command=save).grid(
            row=len(labels) + 1, columnspan=2
        )

    def start():
        nonlocal consent, born
        if consent:
            return
        mode = (
            "continuous hands-free capture"
            if cfg["hands_free"]
            else "capture while Hold to talk is pressed"
        )
        if not messagebox.askyesno(
            "Microphone consent",
            f"Allow {mode} on input {cfg['microphone']} for up to five minutes? Speech recognition is local. Allow reply playback on output {cfg['speaker']}? Review each turn before sending.",
            parent=panel,
        ):
            return
        consent = True
        born = time.monotonic()
        start_button.configure(state="disabled")
        settings_button.configure(state="disabled")
        detail.set("Loading local recognizer…")
        run(lambda: voice_audio.capture(cfg, closed, held, receive), closed)

    def press(_):
        if consent and not cfg["hands_free"] and not held.is_set():
            interrupt()
            held.set()

    def execute_request(req, text, tag):
        p = Profile(profile.directory)
        try:
            with p.transaction():
                if tag.is_set():
                    if p.get("requests", req["id"])["state"] == "pending":
                        decide(p, req["id"], req["digest"], False)
                    return
                result = decide(p, req["id"], req["digest"], True, stop=tag)
            events.put((tag, "receipt", (text, result)))
        finally:
            p.db.close()

    def approve(req, text):
        nonlocal pending, current, busy
        pending = req
        tag = turn
        ok = messagebox.askyesno(
            "Approve voice operation?",
            json.dumps(req, ensure_ascii=True, indent=2),
            parent=panel,
        )
        if tag.is_set() or closed.is_set():
            return
        pending = None
        if not ok:
            with profile.transaction():
                decide(profile, req["id"], req["digest"], False)
            return
        current = req["id"]
        busy = "Speaking" if req["operation"] == "voice-cloud" else "Thinking"
        state()
        run(lambda: execute_request(req, text, tag), tag)

    def review():
        if not consent or not draft.get().strip() or pending:
            return
        interrupt()
        text = draft.get().strip()
        try:
            with profile.transaction():
                req = voice.propose_turn(profile, text, history)
            approve(req, text)
        except (ValueError, RuntimeError, OSError) as exc:
            detail.set(str(exc))

    def receipt(data):
        nonlocal busy, current
        text, result = data
        current = None
        busy = ""
        if result["state"] != "completed" or result["operation"] != "ai-generate":
            detail.set(result.get("error", result["state"]))
        else:
            output = result["result"]
            answer = output.get("text", "") if isinstance(output, dict) else ""
            if not answer:
                detail.set("No speakable reply")
                return
            answer = answer[:4096]
            history.append({"user": text, "assistant": answer})
            detail.set(answer)
            if cfg["synthesis"] in voice.CLOUD:
                with profile.transaction():
                    req = voice.propose_cloud(profile, answer)
                approve(req, answer)
            else:
                busy = "Speaking"
                tag = turn

                def play():
                    voice_audio.playback(
                        voice_audio.local_pcm(cfg, answer, tag), cfg["speaker"], tag
                    )
                    events.put((tag, "played", None))

                run(play, tag)
        state()

    def close():
        if not closed.is_set():
            closed.set()
            held.clear()
            interrupt()
            history.clear()
        if panel.winfo_exists():
            panel.destroy()

    def poll():
        nonlocal listening, busy
        if closed.is_set():
            return
        if consent and time.monotonic() - born >= 300:
            close()
            return
        try:
            while not events.empty():
                tag, kind, value = events.get()
                if tag is not closed and tag is not turn:
                    continue
                if kind == "ready":
                    if not cfg["hands_free"]:
                        talk.configure(state="normal")
                    detail.set(
                        "Listening for “" + cfg["wake"] + "”"
                        if cfg["hands_free"]
                        else "Hold to talk, then release and review."
                    )
                elif kind == "microphone":
                    listening = value
                elif kind == "partial":
                    if (
                        not cfg["hands_free"]
                        or gate.split(value) is not None
                        or time.monotonic() < gate.armed_until
                    ):
                        partial.set(value)
                        if busy and cfg["hands_free"]:
                            interrupt()
                elif kind == "final":
                    text = (
                        gate.accept(value, time.monotonic())
                        if cfg["hands_free"]
                        else value
                    )
                    partial.set("")
                    if text:
                        interrupt()
                        draft.set(text)
                        detail.set("Review the transcript before sending.")
                elif kind == "receipt":
                    receipt(value)
                elif kind == "played":
                    busy = ""
                elif kind == "error":
                    detail.set(value)
                    busy = ""
                    if tag is closed:
                        listening = False
                        talk.configure(state="disabled")
                state()
            if last_wave:
                points = last_wave.pop()
                width = max(1, canvas.winfo_width() - 1)
                coords = [
                    n
                    for i, s in enumerate(points)
                    for n in (width * i / (len(points) - 1), 32 - 30 * s / 32768)
                ]
                canvas.delete("wave")
                canvas.create_line(*coords, fill="#96d9ca", tags="wave")
        except (ValueError, OSError, RuntimeError) as exc:
            detail.set(str(exc))
            busy = ""
            state()
        if not closed.is_set():
            panel.after(30, poll)

    settings_button = ttk.Button(row, text="Settings", command=preferences)
    settings_button.pack(side="left")
    start_button = ttk.Button(row, text="Enable session", command=start)
    start_button.pack(side="left")
    talk = ttk.Button(row, text="Hold to talk", state="disabled")
    talk.pack(side="left")
    talk.bind("<ButtonPress-1>", press)
    talk.bind("<ButtonRelease-1>", lambda _: held.clear())
    panel.bind("<FocusOut>", lambda _: held.clear())
    ttk.Button(row, text="Review turn", command=review).pack(side="left")
    ttk.Button(row, text="Interrupt", command=interrupt).pack(side="left")
    ttk.Button(row, text="End session", command=close).pack(side="left")
    panel.protocol("WM_DELETE_WINDOW", close)

    def destroyed(event):
        if event.widget is panel and not closed.is_set():
            closed.set()
            held.clear()
            interrupt()

    panel.bind("<Destroy>", destroyed)
    poll()
