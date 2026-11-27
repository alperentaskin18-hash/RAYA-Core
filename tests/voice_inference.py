"""Offline PCM bench. Requires local optional speech backends. No microphone is opened."""

import argparse
import hashlib
import json
import sys
import time
from array import array
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raya import voice, voice_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "tr"), default="en")
    parser.add_argument("--text", default="one two three")
    parser.add_argument("--asr-model", help="local Vosk model directory")
    parser.add_argument("--piper", help="local Piper ONNX model")
    parser.add_argument(
        "--reference-sample",
        action="store_true",
        help="fetch pinned English PCM for ASR acceptance",
    )
    parser.add_argument(
        "--wake", help="exercise the hands-free wake detector on the sample"
    )
    args = parser.parse_args()
    cfg = dict(
        voice.DEFAULT,
        language=args.language,
        hands_free=bool(args.wake),
        wake=args.wake or voice.DEFAULT["wake"],
        recognizer="vosk" if args.asr_model else "pocketsphinx",
        asr_model=args.asr_model,
        synthesis="piper" if args.piper else "espeak",
        tts_model=args.piper,
    )
    report = {"language": args.language, "reference": args.text}
    if args.reference_sample:
        # Official PocketSphinx regression recording; retrieved only when explicitly selected.
        import urllib.request

        url = (
            "https://raw.githubusercontent.com/cmusphinx/pocketsphinx/"
            "f20ff1b7a5db64c5e892a798479e20df86e79a35/test/data/goforward.raw"
        )
        with urllib.request.urlopen(url, timeout=20) as response:
            pcm = response.read(89161)
        if (
            hashlib.sha256(pcm).hexdigest()
            != "f15c60ec54059d8b66e410d0064945a0b0a04ea56e1ddca1958e493c0cf70e71"
        ):
            raise ValueError("reference recording digest mismatch")
        report["reference"] = "go forward ten meters"
    else:
        start = time.perf_counter()
        samples = array("h")
        first = None
        for rate, chunk in voice_audio.local_pcm(cfg, args.text, Event()):
            if first is None:
                first = time.perf_counter() - start
            samples.frombytes(chunk)
        if not samples:
            raise RuntimeError("empty synthesis")
        report.update(
            synth_first_pcm_ms=round(first * 1000, 1),
            synth_total_ms=round((time.perf_counter() - start) * 1000, 1),
            duration_s=round(len(samples) / rate, 3),
            pcm_bytes=len(samples) * 2,
        )
        # Bench resampling only. Device capture runs at 16 kHz.
        pcm = array(
            "h",
            (
                samples[i * rate // voice.RATE]
                for i in range(len(samples) * voice.RATE // rate)
            ),
        ).tobytes()
    if args.language == "tr" and not args.asr_model:
        report["asr"] = "Turkish model not supplied"
    else:
        if args.wake:
            pcm = bytes(voice.FRAME * 2 * 8) + pcm
        pcm += bytes(voice.RATE * 2)

        def emit(kind, value):
            if kind == "partial":
                report.setdefault(
                    "first_partial_compute_ms",
                    round((time.perf_counter() - start) * 1000, 1),
                )
                report.setdefault(
                    "first_partial_audio_ms",
                    (offset + 2 * voice.FRAME) * 1000 // (2 * voice.RATE),
                )
            if kind == "final":
                report["asr"] = value

        start = time.perf_counter()
        segment = voice.Segmenter(cfg, voice_audio.Decoder(cfg), emit)
        report["decoder_load_ms"] = round((time.perf_counter() - start) * 1000, 1)
        start = time.perf_counter()
        for offset in range(0, len(pcm) - 2 * voice.FRAME + 1, 2 * voice.FRAME):
            segment.feed(pcm[offset : offset + 2 * voice.FRAME])
        segment.finish()
        report["decode_compute_ms"] = round((time.perf_counter() - start) * 1000, 1)
        if args.wake:
            report["wake_detected"] = (
                voice.WakeGate(args.wake, args.language).split(report.get("asr", ""))
                is not None
            )
        if not report.get("asr"):
            raise RuntimeError("no transcript")
        if args.reference_sample and not report["asr"].startswith("go forward ten"):
            raise RuntimeError("reference utterance not recognized: " + report["asr"])
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
