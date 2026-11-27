"""Deterministic audio, consent and interruption checks without model downloads."""

import http.client
import json
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from array import array
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from raya import engine, vault, voice, voice_audio
from raya.core import Profile, decide


class TextDecoder:
    def start(self):
        self.count = 0

    def feed(self, block):
        self.count += 1
        return "hey" if self.count == 1 else "hey raya"

    def finish(self):
        return "hey raya hello"


class VoiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.p = Profile(directory.name)
        self.addCleanup(self.p.db.close)
        self.loud = array("h", [1500, -1500] * (voice.FRAME // 2)).tobytes()
        self.quiet = bytes(voice.FRAME * 2)

    def test_vad_preroll_silence_and_bilingual_wake(self):
        events = []
        decoder = TextDecoder()
        seg = voice.Segmenter(
            dict(voice.DEFAULT, silence_ms=200),
            decoder,
            lambda k, v: events.append((k, v)),
        )
        for _ in range(12):
            seg.feed(self.quiet)
        self.assertFalse(any(k == "partial" for k, v in events))
        seg.feed(self.loud)
        self.assertEqual(decoder.count, 8)
        for _ in range(10):
            seg.feed(self.quiet)
        self.assertEqual([v for k, v in events if k == "final"], ["hey raya hello"])
        self.assertEqual([v for k, v in events if k == "activity"], [True, False])
        self.assertTrue(any(k == "wave" and any(v) for k, v in events))
        gate = voice.WakeGate("IŞIK RAYA", "tr")
        self.assertIsNone(gate.accept("someone speaking", 1))
        self.assertEqual(gate.accept("ışık raya Nasılsın", 2), "Nasılsın")
        self.assertIsNone(gate.accept("IŞIK RAYA", 3))
        self.assertEqual(gate.accept("merhaba", 7), "merhaba")
        self.assertIsNone(gate.accept("IŞIK RAYA", 8))
        self.assertIsNone(gate.accept("yine", 14))

    def test_ptt_closes_input_on_release_and_discards_on_stop(self):
        held, stop, opened, partial, finished = [threading.Event() for _ in range(5)]
        seen = []
        outer = self

        class Input:
            def __init__(self, **kw):
                outer.assertEqual(kw["device"], 3)
                self.callback = kw["callback"]

            def __enter__(self):
                opened.set()
                self.callback(
                    outer.loud, voice.FRAME, None, SimpleNamespace(input_overflow=False)
                )
                return self

            def __exit__(self, *args):
                opened.clear()

        def emit(k, v):
            seen.append((k, v))
            if k == "partial":
                partial.set()
            if k == "microphone" and v is False:
                finished.set()

        with (
            patch.dict(
                sys.modules, {"sounddevice": SimpleNamespace(RawInputStream=Input)}
            ),
            patch.object(voice_audio, "Decoder", return_value=TextDecoder()),
        ):
            worker = threading.Thread(
                target=voice_audio.capture,
                args=(dict(voice.DEFAULT, microphone=3), stop, held, emit),
            )
            worker.start()
            try:
                self.assertFalse(opened.wait(0.1))
                held.set()
                self.assertTrue(partial.wait(2))
                held.clear()
                self.assertTrue(finished.wait(2))
                self.assertFalse(opened.is_set())
                partial.clear()
                held.set()
                self.assertTrue(partial.wait(2))
            finally:
                stop.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual([v for k, v in seen if k == "final"], ["hey raya hello"])

    def test_turkish_model_adapters_stream_locally(self):
        cfg = dict(
            voice.DEFAULT,
            language="tr",
            recognizer="vosk",
            asr_model=str(self.p.directory),
        )
        rec = MagicMock()
        rec.AcceptWaveform.side_effect = [False, True]
        rec.PartialResult.side_effect = ['{"partial":"mer"}', '{"partial":""}']
        rec.Result.return_value = '{"text":"merhaba"}'
        rec.FinalResult.return_value = '{"text":"dünya"}'
        backend = SimpleNamespace(
            Model=MagicMock(), KaldiRecognizer=MagicMock(return_value=rec)
        )
        with patch.dict(sys.modules, {"vosk": backend}):
            decoder = voice_audio.Decoder(cfg)
            decoder.start()
            self.assertEqual(decoder.feed(self.loud), "mer")
            self.assertEqual(decoder.feed(self.loud), "merhaba")
            self.assertEqual(decoder.finish(), "merhaba dünya")
            backend.Model.assert_called_once_with(str(self.p.directory.resolve()))
            rec.Reset.assert_called_once()
        model = Path(self.p.directory) / "fixture.onnx"
        model.touch()
        Path(str(model) + ".json").write_text('{"audio":{"sample_rate":16000}}')
        child = MagicMock()
        child.__enter__.return_value = child
        child.stdout = io.BytesIO(self.loud * 3)
        child.wait.return_value = 0
        with (
            patch("shutil.which", return_value="piper"),
            patch("subprocess.Popen", return_value=child) as spawn,
        ):
            chunks = list(
                voice_audio.local_pcm(
                    dict(cfg, synthesis="piper", tts_model=str(model)),
                    "merhaba",
                    threading.Event(),
                )
            )
            self.assertEqual(chunks, [(16000, self.loud)] * 3)
            self.assertIn("--output-raw", spawn.call_args.args[0])
            child.stdin.write.assert_called_once_with(b"merhaba\n")

    def test_context_preferences_and_consequential_approval(self):
        cfg = voice.configure(
            self.p,
            {
                "language": "tr",
                "recognizer": "vosk",
                "wake": "Merhaba RAYA",
                "microphone": 2,
            },
        )
        reopened = Profile(self.p.directory)
        try:
            self.assertEqual(voice.settings(reopened), cfg)
        finally:
            reopened.db.close()
        engine.configure(
            self.p, {"routes": {"cpu": {"provider": "llama.cpp", "model": "fixture"}}}
        )
        history = deque(({"user": str(i), "assistant": "a"} for i in range(6)))
        req = voice.propose_turn(self.p, "delete the project", history)
        self.assertEqual(req["operation"], "ai-generate")
        self.assertEqual(
            json.loads(req["content"]["prompt"])["conversation"], list(history)[-4:]
        )
        with patch("raya.desktop.perform", return_value={"completed": True}) as perform:
            for allow in (False, True):
                req = voice.propose_turn(self.p, "adres aç https://example.org", [])
                self.assertEqual(req["state"], "pending")
                with self.p.transaction():
                    result = decide(self.p, req["id"], req["digest"], allow)
                self.assertEqual(result["state"], "completed" if allow else "denied")
            perform.assert_called_once()
        vault.store(self.p, "private", "fixture-password")
        with self.assertRaises(ValueError):
            voice.propose_turn(self.p, "share fixture-password", [])

    def test_optional_cloud_pcm_requires_fresh_approval(self):
        calls = []

        class Service(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(
                    (
                        self.path,
                        json.loads(
                            self.rfile.read(int(self.headers["Content-Length"]))
                        ),
                        dict(self.headers),
                    )
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(bytes(1920))

        server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
        server.daemon_threads = False
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        hosts = []

        def connection(host, timeout):
            hosts.append(host)
            return http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=timeout
            )

        played = []

        def output(chunks, device, stop):
            played.extend(list(chunks))

        with (
            patch.object(http.client, "HTTPSConnection", side_effect=connection),
            patch.object(voice_audio, "playback", side_effect=output),
        ):
            for service in voice.CLOUD:
                voice.configure(self.p, {"synthesis": service})
                vault.store(
                    self.p,
                    "ai-openai" if service == "openai" else "voice-elevenlabs",
                    "secret-" + service,
                )
                engine.configure(self.p, {"offline": False})
                stale = voice.propose_cloud(self.p, "hello")
                engine.configure(self.p, {"offline": True})
                with self.p.transaction():
                    self.assertEqual(
                        decide(self.p, stale["id"], stale["digest"], True)["state"],
                        "denied",
                    )
                with self.assertRaises(ValueError):
                    voice.propose_cloud(self.p, "hello")
                engine.configure(self.p, {"offline": False})
                req = voice.propose_cloud(self.p, "hello")
                with self.p.transaction():
                    result = decide(self.p, req["id"], req["digest"], True)
                self.assertEqual(result["state"], "completed", result)
                self.assertNotIn("secret-", json.dumps(result))
        self.assertEqual(hosts, list(voice.CLOUD.values()))
        self.assertEqual(
            [c[1].get("input", c[1].get("text")) for c in calls], ["hello", "hello"]
        )
        self.assertEqual(
            [len(block) for rate, block in played if rate == 24000], [960] * 4
        )

    @unittest.skipUnless(
        os.name == "nt" or os.environ.get("DISPLAY"), "Tk display required"
    )
    def test_hud_consent_wake_review_and_barge_in(self):
        import tkinter as tk
        from raya.voice_hud import open_voice

        root = tk.Tk()
        self.addCleanup(root.destroy)
        voice.configure(self.p, {"hands_free": True})
        engine.configure(
            self.p, {"routes": {"cpu": {"provider": "llama.cpp", "model": "fixture"}}}
        )
        record = []
        playing, interrupted = threading.Event(), threading.Event()

        def capture(cfg, closed, held, emit):
            record.append(emit)
            emit("ready", True)
            emit("microphone", True)
            closed.wait(5)

        def playback(chunks, device, stop):
            playing.set()
            if stop.wait(4):
                interrupted.set()
            chunks.close()

        def until(f):
            end = time.monotonic() + 3
            while time.monotonic() < end:
                root.update()
                if f():
                    return
                time.sleep(0.01)
            self.fail("voice HUD transition timed out")

        answers = iter((False, True, True, True))
        with (
            patch.object(voice_audio, "capture", capture),
            patch.object(voice_audio, "playback", playback),
            patch("tkinter.messagebox.askyesno", lambda *a, **kw: next(answers)),
            patch.object(
                engine, "exchange", return_value={"text": "answer", "usage": {}}
            ),
        ):
            open_voice(root, self.p)
            panel = root.children["voice"]
            widgets = list(panel.winfo_children())
            buttons = {w.cget("text"): w for w in widgets[-1].winfo_children()}
            entry = next(w for w in widgets if w.winfo_class() == "TEntry")
            buttons["Enable session"].invoke()
            self.assertFalse(record)
            buttons["Enable session"].invoke()
            until(lambda: bool(record))
            record[0]("final", "background words")
            record[0]("final", "hey raya hello")
            until(lambda: entry.get() == "hello")
            self.assertFalse(self.p.all("requests"))
            buttons["Review turn"].invoke()
            until(playing.is_set)
            record[0]("partial", "hey raya")
            until(interrupted.is_set)
            copies = []

            def copy(command, data):
                copies.append((threading.get_ident(), command, data))
                return {"completed": True}

            with (
                patch("raya.voice_hud.sys.platform", "win32"),
                patch("raya.desktop.shutil.which", return_value="fixture"),
                patch("raya.desktop.perform", copy),
            ):
                record[0]("final", "hey raya copy hello")
                until(lambda: entry.get() == "copy hello")
                buttons["Review turn"].invoke()
                until(lambda: bool(copies))
                self.assertEqual(
                    copies, [(threading.get_ident(), "copy", {"text": "hello"})]
                )
            buttons["End session"].invoke()
