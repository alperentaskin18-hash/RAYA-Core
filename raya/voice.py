"""Local speech segmentation, wake gating, preferences and reviewed voice turns."""

import json
import math
import re
from array import array
from collections import deque

from . import desktop, engine, vault
from .core import digest, propose, require, text_field

RATE = 16000
FRAME = 320  # 20 ms of mono signed 16-bit PCM
CLOUD = {"openai": "api.openai.com", "elevenlabs": "api.elevenlabs.io"}
DEFAULT = dict(
    language="en",
    wake="Hey RAYA",
    hands_free=False,
    microphone=None,
    speaker=None,
    recognizer="pocketsphinx",
    asr_model="",
    synthesis="espeak",
    tts_model="",
    voice="alloy",
    threshold=350,
    silence_ms=600,
)


def settings(profile):
    return {**DEFAULT, **profile.get("voice", "preferences", {})}


def configure(profile, patch):
    require(
        isinstance(patch, dict) and patch.keys() <= DEFAULT.keys(),
        "unknown voice option",
    )
    cfg = {**settings(profile), **patch}
    require(cfg["language"] in ("tr", "en"), "unsupported language")
    require(type(cfg["hands_free"]) is bool, "invalid hands-free mode")
    for k in ("speaker", "microphone"):
        require(
            cfg[k] is None or type(cfg[k]) is int and 0 <= cfg[k] < 65536,
            "invalid device index",
        )
    require(cfg["recognizer"] in ("vosk", "pocketsphinx"), "invalid recognizer")
    require(
        cfg["recognizer"] != "pocketsphinx" or cfg["language"] == "en",
        "English PocketSphinx model required",
    )
    require(cfg["synthesis"] in ("piper", "espeak", *CLOUD), "invalid synthesizer")
    for k in ("asr_model", "tts_model"):
        require(
            isinstance(cfg[k], str) and len(cfg[k]) <= 4096 and "\x00" not in cfg[k],
            "invalid model path",
        )
    require(
        re.fullmatch(r"[\w -]{1,64}", cfg["wake"]) and cfg["wake"].split(),
        "invalid wake phrase",
    )
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cfg["voice"]), "invalid voice ID")
    require(
        type(cfg["threshold"]) is int and 1 <= cfg["threshold"] <= 32767,
        "invalid RMS threshold",
    )
    require(
        type(cfg["silence_ms"]) is int and 200 <= cfg["silence_ms"] <= 2000,
        "invalid silence interval",
    )
    require(
        not any(vault.contains(cfg, v) for v in vault.values(profile)),
        "protected value in preferences",
    )
    profile.put("voice", "preferences", cfg)
    return cfg


def analyze(pcm):
    samples = array("h")
    samples.frombytes(pcm)
    require(len(samples) == FRAME, "expected one 20 ms PCM frame")
    return math.isqrt(sum(s * s for s in samples) // FRAME), list(samples[::10])


class Segmenter:
    """Feed complete audio frames. Only active utterances reach the recognizer."""

    def __init__(self, cfg, decoder, emit):
        self.cfg, self.decoder, self.emit = cfg, decoder, emit
        self.preroll = deque(maxlen=8)
        self.frames = self.quiet = 0
        self.partial = ""

    def feed(self, pcm):
        level, points = analyze(pcm)
        self.emit("wave", points)
        if not self.frames:
            self.preroll.append(pcm)
            if level < self.cfg["threshold"]:
                return
            self.decoder.start()
            self.emit("activity", True)
            for before in list(self.preroll)[:-1]:
                self.decoder.feed(before)
            self.preroll.clear()
        self.frames += 1
        self.quiet = self.quiet + 1 if level < self.cfg["threshold"] else 0
        partial = self.decoder.feed(pcm)
        if partial and partial != self.partial:
            self.partial = partial
            self.emit("partial", partial)
        if self.quiet * 20 >= self.cfg["silence_ms"] or self.frames >= 750:
            self.finish()

    def finish(self, discard=False):
        self.preroll.clear()
        if self.frames:
            result = self.decoder.finish().strip()
            if result and not discard:
                self.emit("final", result[:4096])
            self.emit("activity", False)
        self.frames = self.quiet = 0
        self.partial = ""


class WakeGate:
    def __init__(self, phrase, language):
        self.language = language
        self.words = self.normalize(phrase.split())
        self.armed_until = 0

    def normalize(self, words):
        return [
            w.translate(str.maketrans("Iİ", "ıi")).casefold()
            if self.language == "tr"
            else w.casefold()
            for w in words
        ]

    def split(self, text):
        words = text.split()
        if self.normalize(words[: len(self.words)]) == self.words:
            return " ".join(words[len(self.words) :])
        return None

    def accept(self, text, now):
        rest = self.split(text)
        if rest == "":
            self.armed_until = now + 5
            return None
        armed = now < self.armed_until
        self.armed_until = 0
        return rest if rest is not None else text if armed else None


def propose_turn(profile, transcript, history):
    text_field(transcript, "transcript", 4096)
    require(
        not any(vault.contains(transcript, v) for v in vault.values(profile)),
        "protected value in transcript",
    )
    for prefix in ("open ", "adres aç "):
        if transcript.casefold().startswith(prefix) and transcript[
            len(prefix) :
        ].startswith("https://"):
            return desktop.plan(profile, "browse", {"url": transcript[len(prefix) :]})
    for prefix in ("copy ", "panoya kopyala "):
        if transcript.casefold().startswith(prefix):
            return desktop.plan(profile, "copy", {"text": transcript[len(prefix) :]})
    prompt = json.dumps(
        {"conversation": list(history)[-4:], "user": transcript}, ensure_ascii=False
    )
    require(
        not any(vault.contains(prompt, v) for v in vault.values(profile)),
        "protected value in conversation",
    )
    return engine.prepare(profile, {"prompt": prompt})


def cloud_baseline(profile):
    return digest([settings(profile), engine.config(profile)["offline"]])


def propose_cloud(profile, text):
    cfg = settings(profile)
    from .cloud import strict

    require(not strict(profile), "cloud speech has no verified zero-price contract")
    provider = cfg["synthesis"]
    require(
        provider in CLOUD and not engine.config(profile)["offline"],
        "cloud speech is disabled",
    )
    text_field(text, "speech", 4096)
    require(
        not any(v in text for v in vault.values(profile)), "protected value in speech"
    )
    return propose(
        profile,
        "voice-cloud",
        CLOUD[provider],
        {
            "provider": provider,
            "voice": cfg["voice"],
            "text": text,
            "speaker": cfg["speaker"],
        },
        cloud_baseline(profile),
        ["external.speech." + provider, "audio.output"],
    )
