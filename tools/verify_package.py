"""Verify source coverage, reproducibility and cold startup after extraction."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from package import FILES, ROOT


def main():
    if (ROOT / ".git").exists():
        tracked = (
            subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
            .decode()
            .split("\0")
        )
        if set(tracked[:-1]) != set(FILES):
            raise SystemExit("Source allowlist differs from the Git index")
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        archives = [directory / f"{i}.zip" for i in range(2)]
        for path in archives:
            subprocess.run(
                [sys.executable, str(ROOT / "tools/package.py"), str(path)], check=True
            )
        data = archives[0].read_bytes()
        if data != archives[1].read_bytes():
            raise SystemExit("Non-reproducible source archive")
        clean = directory / "clean"
        with zipfile.ZipFile(archives[0]) as package:
            if package.namelist() != sorted(FILES) or package.testzip() is not None:
                raise SystemExit("Unexpected archive contents")
            package.extractall(clean)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.check_output(
            [
                sys.executable,
                "-S",
                "-m",
                "raya",
                "--profile",
                str(directory / "profile"),
                "capabilities",
            ],
            cwd=clean,
            env=env,
        )
        if json.loads(result)["coding_provider"] != "unconfigured":
            raise SystemExit("Cold startup failed")
        print(
            f"{len(FILES)} files, {len(data)} bytes, SHA-256 {hashlib.sha256(data).hexdigest()}"
        )


if __name__ == "__main__":
    main()
