"""Create an original-source-only ZIP with an explicit reviewed file allowlist."""

import argparse
import hashlib
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = """
LICENSE NOTICE .gitattributes .github/workflows/ci.yml .gitignore CONTRIBUTING.md PROVIDERS.md CLOUD.md VOICE.md PROVENANCE.md README.md
RELEASE-NOTES.md SECURITY.md pyproject.toml raya/__init__.py
development_targets/percent.py development_targets/distinct.py development_targets/nonempty_lines.py
raya/cloud.py raya/control.py raya/cloud_hud.py raya/chatgpt_auth.py raya/dev_runner.py raya/development.py
raya/__main__.py raya/ai_settings.py raya/ai_protocol.py raya/engine.py raya/core.py raya/dashboard.py raya/desktop.py raya/exports.py raya/goals.py
raya/hud.py raya/index.py raya/learning.py raya/local.py raya/provider.py raya/runtime.py
raya/speech.ps1 raya/speech.py raya/storage.py raya/vault.py raya/visual.py raya/worker.py raya/voice.py raya/voice_audio.py raya/voice_hud.py
sbom.cdx.json tests/local_inference.py tests/test_boundaries.py tests/test_engine.py tests/test_hud.py
tests/test_interface.py tests/test_mvp.py tests/test_outputs.py tests/test_services.py tests/test_voice.py tests/voice_inference.py
tests/test_cloud.py tests/test_development.py tools/day07_acceptance.py tools/package.py tools/verify_package.py
""".split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    destination = parser.parse_args().destination.resolve()
    if destination.is_relative_to(ROOT):
        parser.error("choose a destination outside the checkout")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        temp.close()
        path = Path(temp.name)
        try:
            with zipfile.ZipFile(path, "w") as output:
                for name in sorted(FILES):
                    source = ROOT / name
                    if source.resolve() != source or not source.is_file():
                        raise ValueError("invalid allowlisted file: " + name)
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    output.writestr(
                        info, source.read_bytes(), compress_type=zipfile.ZIP_STORED
                    )
            if path.stat().st_size >= 200 * 1024**2:
                raise ValueError("source ZIP exceeds budget")
            # Publish only a complete archive, preserving any existing destination.
            destination.hardlink_to(path)
        finally:
            path.unlink(missing_ok=True)
    print(
        destination.stat().st_size, hashlib.sha256(destination.read_bytes()).hexdigest()
    )


if __name__ == "__main__":
    main()
