"""Optional speech backends. Model weights and device handles stay outside the profile."""

import contextlib
import http.client
import io
import json
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

from . import engine, vault
from .core import require
from .voice import CLOUD, FRAME, RATE, Segmenter


class Decoder:
    def __init__(self, cfg):
        self.spotter = None
        self.phrase = " ".join(cfg["wake"].lower().split())
        self.vosk = cfg["recognizer"] == "vosk"
        if self.vosk:
            from vosk import KaldiRecognizer, Model

            path = Path(cfg["asr_model"])
            require(
                cfg["asr_model"] and path.is_dir(),
                "select a local Vosk model directory",
            )
            self.rec = KaldiRecognizer(Model(str(path.resolve())), RATE)
        else:
            from pocketsphinx import Decoder as Sphinx

            self.rec = Sphinx(samprate=RATE)
            if cfg["hands_free"]:
                require(
                    all(self.rec.lookup_word(w) for w in self.phrase.split()),
                    "wake phrase is outside the English model dictionary",
                )
                self.spotter = Sphinx(
                    keyphrase=self.phrase, kws_threshold=1e-55, kws_delay=5
                )

    def start(self):
        self.parts = []
        if self.vosk:
            self.rec.Reset()
        else:
            self.rec.start_utt()
            if self.spotter is not None:
                self.spotter.start_utt()
            self.wake_end = None

    def feed(self, data):
        if self.vosk:
            if self.rec.AcceptWaveform(data):
                self.parts.append(json.loads(self.rec.Result())["text"])
            return " ".join(
                [*self.parts, json.loads(self.rec.PartialResult()).get("partial", "")]
            ).strip()
        self.rec.process_raw(data, False, False)
        if self.spotter is not None:
            self.spotter.process_raw(data, False, False)
        return self.sphinx_text()

    def sphinx_text(self):
        hyp = self.rec.hyp()
        if self.spotter is not None and self.wake_end is None:
            for segment in self.spotter.seg() or ():
                if segment.start_frame <= 45:
                    self.wake_end = segment.end_frame
                    break
        if self.wake_end is None:
            return hyp.hypstr if hyp else ""
        # Keep words following the spotted phrase from the unrestricted decoder.
        words = [
            s.word.split("(")[0]
            for s in self.rec.seg() or ()
            if s.start_frame > self.wake_end and not s.word.startswith(("<", "["))
        ]
        return " ".join([self.phrase, *words])

    def finish(self):
        if self.vosk:
            return " ".join(
                [*self.parts, json.loads(self.rec.FinalResult())["text"]]
            ).strip()
        self.rec.end_utt()
        if self.spotter is not None:
            self.spotter.end_utt()
        return self.sphinx_text()


def devices():
    import sounddevice as sd

    return [
        {
            "index": i,
            "name": d["name"],
            "inputs": d["max_input_channels"],
            "outputs": d["max_output_channels"],
        }
        for i, d in enumerate(sd.query_devices())
    ]


def capture(cfg, stop, held, emit):
    import sounddevice as sd

    segment = Segmenter(cfg, Decoder(cfg), emit)
    emit("ready", True)
    while not stop.is_set():
        if not cfg["hands_free"] and not held.wait(0.05):
            continue
        if stop.is_set():
            break
        blocks = queue.Queue(maxsize=25)
        overflow = threading.Event()

        def callback(data, frames, timing, status):
            if status.input_overflow or frames != FRAME:
                overflow.set()
                return
            try:
                blocks.put_nowait(bytes(data))
            except queue.Full:
                overflow.set()

        with sd.RawInputStream(
            device=cfg["microphone"],
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=callback,
        ):
            emit("microphone", True)
            while not stop.is_set() and (cfg["hands_free"] or held.is_set()):
                require(not overflow.is_set(), "audio overrun; restart capture")
                try:
                    pcm = blocks.get(timeout=0.05)
                except queue.Empty:
                    continue
                if not stop.is_set():
                    segment.feed(pcm)
        segment.finish(discard=stop.is_set())
        emit("microphone", False)


@contextlib.contextmanager
def interruptible(stop, abort):
    finished = threading.Event()

    def watch():
        while not finished.wait(0.04):
            if stop.is_set():
                with contextlib.suppress(OSError):
                    abort()
                return

    waiter = threading.Thread(target=watch, daemon=True)
    waiter.start()
    try:
        yield
    finally:
        finished.set()
        waiter.join()


def local_pcm(cfg, text, stop):
    """Yield (sample rate, mono s16le bytes) as the subprocess synthesizes."""
    backend = cfg["synthesis"]
    require(backend in ("espeak", "piper"), "local synthesizer required")
    executable = shutil.which("espeak-ng" if backend == "espeak" else "piper")
    require(executable, "install the selected speech executable on PATH")
    if backend == "piper":
        model = Path(cfg["tts_model"])
        require(
            model.is_file() and model.suffix == ".onnx",
            "select a local Piper ONNX model",
        )
        config = Path(str(model) + ".json")
        rate = json.loads(config.read_text(encoding="utf-8"))["audio"]["sample_rate"]
        require(
            type(rate) is int and 8000 <= rate <= 96000, "invalid model sample rate"
        )
        args = ["--model", str(model.resolve()), "--output-raw"]
    else:
        args = ["--stdout", "--stdin", "-b", "1", "-v", cfg["language"]]
    if stop.is_set():
        return
    with subprocess.Popen(
        [executable, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as child:
        try:
            with interruptible(stop, child.kill):
                child.stdin.write((" ".join(text.splitlines()) + "\n").encode())
                child.stdin.close()
                if backend == "espeak":
                    with wave.open(child.stdout, "rb") as wav:
                        require(
                            wav.getsampwidth() == 2 and wav.getnchannels() == 1,
                            "unsupported speech PCM",
                        )
                        rate = wav.getframerate()
                        while not stop.is_set() and (
                            data := wav.readframes(rate // 50)
                        ):
                            yield rate, data
                else:
                    while not stop.is_set() and (
                        data := child.stdout.read(2 * (rate // 50))
                    ):
                        yield rate, data
                if not stop.is_set():
                    require(child.wait(timeout=10) == 0, "speech synthesis failed")
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


def cloud_pcm(profile, data, stop):
    """Only called from an approved cloud-speech operation or loopback fixtures."""
    from .cloud import strict

    require(not strict(profile), "cloud speech has no verified zero-price contract")
    provider = data["provider"]
    require(not engine.config(profile)["offline"], "offline mode blocks cloud speech")
    key = vault.read_secret(
        profile, "ai-openai" if provider == "openai" else "voice-elevenlabs"
    )
    require(
        key and key.isascii() and key.isprintable(), "missing cloud speech credential"
    )
    if provider == "openai":
        path = "/v1/audio/speech"
        headers = {"Authorization": "Bearer " + key}
        body = {
            "model": "tts-1",
            "input": data["text"],
            "voice": data["voice"],
            "response_format": "pcm",
        }
    else:
        path = "/v1/text-to-speech/" + data["voice"] + "/stream?output_format=pcm_24000"
        headers = {"xi-api-key": key}
        body = {"model_id": "eleven_multilingual_v2", "text": data["text"]}
    headers["Content-Type"] = "application/json"
    conn = http.client.HTTPSConnection(CLOUD[provider], timeout=8)
    deadline = time.monotonic() + 60

    def response(sock, *args, **kwargs):
        resp = http.client.HTTPResponse(sock, *args, **kwargs)
        resp.fp = io.BufferedReader(
            engine._ResponseReader(
                sock, resp.fp, lambda: stop.is_set() or time.monotonic() >= deadline
            )
        )
        return resp

    conn.response_class = response
    try:
        if stop.is_set():
            return
        conn.request("POST", path, json.dumps(body).encode(), headers)
        with conn.getresponse() as reply:
            require(
                reply.status == 200, f"speech provider returned HTTP {reply.status}"
            )
            total = 0
            while not stop.is_set() and (chunk := reply.read(960)):
                total += len(chunk)
                require(
                    total <= 12_000_000 and len(chunk) % 2 == 0, "invalid speech audio"
                )
                yield 24000, chunk
            require(total or stop.is_set(), "speech provider returned empty audio")
    except (http.client.HTTPException, OSError, RuntimeError) as exc:
        if not stop.is_set():
            raise RuntimeError("speech transport interrupted or unavailable") from exc
    finally:
        conn.close()


def playback(chunks, device, stop):
    import sounddevice as sd

    try:
        _playback(sd, chunks, device, stop)
    except sd.PortAudioError as exc:
        if not stop.is_set():
            raise RuntimeError("speaker unavailable or playback interrupted") from exc


def _playback(sd, chunks, device, stop):
    with contextlib.closing(chunks), contextlib.ExitStack() as stack:
        output = None
        for rate, pcm in chunks:
            if stop.is_set():
                break
            if output is None:
                output = sd.RawOutputStream(
                    device=device,
                    samplerate=rate,
                    channels=1,
                    dtype="int16",
                    latency="low",
                )
                stack.callback(output.close)
                output.start()
                stack.enter_context(interruptible(stop, output.abort))
            output.write(pcm)
        if output is not None and not stop.is_set():
            output.stop()
