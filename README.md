# R.A.Y.A Core

Run `python -m raya` from this directory with Python 3.11 or newer. A clean profile works offline. Use `python -m raya --profile PATH COMMAND JSON` to choose a profile. Pass `-` in place of JSON to read one object from stdin. Each command prints JSON. Errors use stderr and a nonzero exit code. Review the returned state after any approval. An approval can be consumed with a denied or failed outcome.

## Install the CLI

From the source checkout or extracted source archive, create a virtual environment with Python 3.11 or newer.

```sh
python -m venv .venv
# Linux
.venv/bin/python -m pip install .
.venv/bin/raya capabilities
# Windows PowerShell
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\raya.exe capabilities
```

Activate the environment before using the shorter `raya` command. The basic CLI uses the standard library. For optional image features install `python -m pip install ".[images]"`. Tk must be provided by your Python distribution for the HUD.

## AI providers

Use the CLI or the HUD’s **Provider settings** to choose models, set token limits and keep inference offline or enable a cloud provider. See the [provider guide](PROVIDERS.md).

## Use a local model

Start a compatible llama.cpp server using a locally available, appropriately licensed GGUF. The example listens on loopback. Choose a free port and keep that server under your account.

```sh
llama-server -m /path/to/model.gguf --host 127.0.0.1 --port 8177
```

In another terminal, run the following from the same profile. JSON examples use POSIX shell quoting. PowerShell users can pipe JSON into `raya set -`.

```sh
raya set '{"field":"provider_port","value":8177}'
raya set '{"field":"provider","value":"local-llama"}'
raya provider-status
raya ask "Suggest one test for a settings file that fails to save."
```

Review the displayed endpoint and prompt, then type `yes` to send one request. The receipt contains the generated text or a failure state. For automation, use `generate` and `approve` as separate commands. If health reports unavailable, check the server's model-loading output and configured port. If a completion fails, the receipt is consumed; inspect it before requesting another. A fresh default profile starts with the provider disabled.

To reproduce the real-engine smoke test, use a CPU llama-server and the matching llama.cpp gguf-py package plus NumPy on the Python import path. From the repository root run `python tests/local_inference.py /path/to/llama-server`. The script creates its own small synthetic weights and temporary profile. See RELEASE-NOTES.md for the evidence boundaries.

## Review a skill

For a manifest you have permission to use, pass its JSON to `python -m raya discover -` on standard input. Use `install -` with the same input to propose installation. Read the returned operation and destination, then use `approve` with its ID and digest. A subsequent `run` request has its own approval. The Tk HUD exposes the same review and consent workflow.

## Commands

| Commands | Inputs and behavior |
|---|---|
| capabilities, settings, set | Read prerequisites and preferences. Set e.g. `{"field":"language","value":"tr"}`. Preferences persist in SQLite. |
| secret-set NAME | Read a credential from the terminal prompt or stdin. Values go to a separate user-only vault. Windows uses current-user DPAPI. On Unix use an encrypted home directory when encryption at rest is required. Use a private user-owned profile directory. |
| discover, install, skills, run, requests, approve, deny | Discover validated declarative metadata, install one version per provider/action and inspect single-use receipts. Text operations are upper, lower, count, reverse and sha256. Third-party executable manifests are unsupported. |
| history, remove, rollback | Propose removal with `{"skill":"core/length"}`. Use a history key as `{"record":"..."}` to propose a previous version. Runtime and history survive deregistration. |
| learn, imports | Supply `{"url":"https://github.com/OWNER/REPO","revision":"40-lowercase-hex-digits"}`. Cache public Git tree metadata and supported root dependency descriptors. Script strings are review data. Assertions about licensing require checking the pinned license files. |
| index, context | Index `{"root":"PATH"}`; query `{"root":"PATH","symbol":"name"}`. Reports include line ranges, transitive importers, impacted tests and all recognized mandatory tests. Reindex after edits. Dynamic calls and syntax errors flag uncertain files. |
| goal-create, task-add, goal-state | Create with `{"title":"Inspect export"}`, then add tasks with `{"goal":"ID","title":"Compare files","depends":[]}`. Use returned latest_task IDs as dependencies. Goal states are active, paused and cancelled. |
| handoff, collect, review | Issue with goal/task IDs. Return that exact handoff plus evidence text and its digest to collect. Use `digest {"value":"evidence text"}` for the SHA-256 of canonical UTF-8 JSON. Send goal, task, digest and an independent_check description to review. The operator performs the independent check. |
| goals, plan | Inspect checkpoints. Supply scope and acceptance strings to generate a bounded manual development plan. |
| note, notes, search | Save text, list notes, or search with a query string. |
| remind, due, reminders | Save text and a timezone-aware ISO 8601 timestamp in at. Poll due for elapsed reminders. |
| export | Set format, name, and content fields. document accepts text, message accepts to/subject/text and produces an unsent EML, goal-report takes a goal ID, and audio takes frequency and milliseconds. Approve to write a new file in the profile's exports directory. |
| dashboard | Read goal progress as JSON on loopback. Startup prints a random bearer and port to the controlling terminal. Use `Authorization: Bearer TOKEN` on `GET /progress`. Treat the token as a secret. For remote viewing, use an authenticated SSH tunnel. Restart changes the bearer. |
| inventory, monitor | Read OS/runtime details, available disk space and current profile database size. |
| browse, copy, tone | Propose opening an HTTPS url, writing text to the clipboard, or playing a bounded Windows tone. The Linux clipboard needs wl-copy or xclip. |
| capture, inspect-image | With Pillow installed, approve capture to a new name such as desktop.png. Inspect an image already in exports with name and optional frame. Returns dimensions, sampled color and frame timing. Supports PNG, JPEG, GIF and WebP. |
| generate | The unified engine supports named routes and returns an approval proposal. In legacy local mode, enable with set field=provider, value=local-llama and start your own llama.cpp server on the configured loopback port (default 8080). A prompt creates one proposal for at most 256 output tokens. The adapter never follows redirects. |
| devices, speak, listen | On Windows, enumerate local SAPI voices/devices. Propose synthesis with text and optional voice or recognition with device. Missing hardware and timeout surface as failed receipts. |
| conversation | Propose a bounded Windows offline session. Optional seconds (5–90), voice and wake word. The default microphone recognizes goal count, skill count, help and stop. Speech detection interrupts synthesis. Each session has its own process. A new session after device loss requires fresh consent. |

The local profile contains notes, prompts, receipts and transcripts. Use synthetic inputs for acceptance. The vault blocks exact stored secret values in command data. The profile owner and the OS account are trusted. Give profiles independent, user-only directories. An in_progress receipt after an interrupted external action requires checking the destination before proposing another operation.

## Validation and packaging

Run `python -m unittest discover -s tests -v`. Graphical checks use a real Tk display. Image checks additionally require Pillow. Run `python tools/package.py /path/outside/checkout/raya.zip` for a deterministic, allowlisted source archive. Extract it into an empty directory to run the same commands there. See RELEASE-NOTES.md for validation boundaries and PROVENANCE.md for source and dependency details.
