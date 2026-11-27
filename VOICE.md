# Voice sessions

Open **Voice** in `raya hud`. **Settings** selects a language, wake phrase, input/output devices and backends. **Enable session** asks for five minutes of local microphone and speaker access. The default is push-to-talk. Hold the button, speak, release, inspect or correct the transcript and click **Review turn**. The approval dialog shows the exact operation and destination. **Interrupt** stops the current reply. **End session** releases the microphone and clears the window's conversation history.

## Install and configure

Use Python 3.11 or 3.12, Tk and `python -m pip install ".[voice]"`. On Linux install the distribution's PortAudio runtime (for example, `libportaudio2`). Select an input that supports mono 16 kHz capture. The optional Windows sounddevice wheel provides PortAudio. Install eSpeak NG on PATH for local English and Turkish synthesis. For neural synthesis, install the **OHF-Voice Piper CLI** on PATH and supply a trusted local `.onnx` voice with its adjacent `.onnx.json` file. Select a voice trained for the spoken language.

| Component | English | Turkish |
| --- | --- | --- |
| Recognition | PocketSphinx includes an English model. Vosk also accepts local English models. | Choose Vosk and an unpacked Turkish model directory. The upstream catalog lists `vosk-model-small-tr-0.3` under Apache-2.0. |
| Synthesis | eSpeak NG `en` or an English Piper voice | eSpeak NG `tr` or a Turkish Piper voice |
| Wake phrase | Defaults to “Hey RAYA” | Enter the phrase as recognized by the selected Turkish model |

The application loads local models by path. Model downloads are operator-managed. Use commercially cleared weights and retain the model's license and notices when redistributing assets. Piper models have individual licenses. The application source archive contains no models, provider SDKs or voice recordings. The SBOM lists direct optional integrations. [Vosk model licenses](https://github.com/alphacep/vosk-space/blob/master/models.md) · [Piper](https://github.com/OHF-Voice/piper1-gpl) · [PocketSphinx](https://github.com/cmusphinx/pocketsphinx)

From the CLI, `raya voice-devices` lists PortAudio device indices and channel counts. `raya voice-config` displays saved settings. Examples use POSIX quoting.

```sh
raya voice-config '{"language":"en","recognizer":"pocketsphinx","synthesis":"espeak"}'
raya voice-config '{"language":"tr","recognizer":"vosk","asr_model":"/models/vosk-model-small-tr-0.3","wake":"merhaba raya","hands_free":true}'
raya voice-config '{"synthesis":"piper","tts_model":"/models/trusted-voice.onnx","microphone":2,"speaker":3}'
```

Use `null` for the system-default device. Reopen the session after changing preferences. The RMS threshold defaults to 350 in signed 16-bit PCM units. Adjust `threshold` and `silence_ms` for the room. Capture uses 20 ms blocks, up to 160 ms of pre-roll, a 600 ms default silence endpoint and a 15-second maximum utterance. An overrun or missing device reports an error. Restart the session after correcting the cause.

## Conversation and privacy

Configure a DAY_05 model route before sending conversational turns. The proposal includes the transcript and up to four earlier approved question/answer pairs from this window. Review of a turn creates a local receipt even if subsequently denied. Raw audio, background transcripts and partial hypotheses have no application recording path. The waveform shows recent microphone samples. Headsets reduce acoustic feedback. There is no acoustic echo canceller.

Hands-free keeps local input open after consent. A recognized wake phrase can prefix a command or arm the next utterance for five seconds. A partial wake phrase during a reply interrupts generation or playback. In push-to-talk mode pressing the button interrupts immediately. Recognition errors can miss wakes or cause false activations. Transcribed text cannot approve a request. An exact `open https://…` / `adres aç https://…` or `copy …` / `panoya kopyala …` creates a separate browser or clipboard approval. Other speech can produce a reviewed text-generation proposal. Synthesis consumes a completed approved reply and streams PCM blocks to the selected speaker.

Local recognition always processes microphone audio on the device. Local synthesis uses a separate eSpeak NG or Piper process. Optional OpenAI or ElevenLabs synthesis sends the specific **reply text** to the provider after a second approval. Cloud speech requires an unlocked profile with explicitly selected legacy billing. Enable cloud access in DAY_05 settings, select the synthesizer and a valid cloud voice ID, then store a key with `raya secret-set ai-openai` or `raya secret-set voice-elevenlabs`. Cloud quotas, plan limits and charges depend on the account. Offline mode blocks cloud speech. Cancelling playback can leave processing already accepted by a remote service.

## Verification

`python -m unittest discover -s tests -v` includes deterministic PCM, mocked Vosk/Piper and cloud transports, Tk consent, interruption, device lifecycle and approval checks. CI runs on Windows and Linux with Python 3.11 and 3.12. Each job also exercises the real English decoder with a pinned, checksummed public PocketSphinx recording. The acceptance check requires the recognized prefix “go forward ten”. The benchmark prints the complete hypothesis for inspection.

With the local backends installed, run `python tests/voice_inference.py` for English synthesis into in-memory recognition. Use `--language tr --text "Merhaba dünya" --asr-model PATH` for Turkish, or `--piper PATH` for neural synthesis. `--reference-sample` downloads the pinned English test sample for ASR only. Times distinguish model loading, first PCM, first partial hypothesis and compute time. The benchmark feeds file audio faster than playback. Physical device latency, recognition accuracy across speakers, wake-word error rates and perceived naturalness require separate measurements.

### Observed Linux results

On one CPU-only Linux run, eSpeak NG produced first English PCM in 7.8 ms and Turkish PCM in 6.9 ms. These times end at the first generated bytes. PocketSphinx processed the prerecorded English utterance in 310.5 ms after a 235.9 ms model load. It returned “go forward ten years” for “go forward ten meters”. On synthetic speech it returned “why do we” for “one two three”. The English file benchmark used up to 117 MiB RSS across the measured runs.

In hands-free English mode a second, local PocketSphinx keyphrase search checks the start of the utterance. With synthesized audio, “Hey Raya” triggered the detector and “unrelated speech” did not. Reproduce with `--wake "Hey RAYA" --text "Hey Raya"`. Vosk mode matches the configured phrase against the local recognizer's hypotheses. A virtual ALSA output received real synthesized PCM and stopped on interruption. Physical input and output, Turkish model accuracy, neural voice quality and end-to-end conversational latency have yet to be measured. Cloud speech tests used loopback fixtures.
