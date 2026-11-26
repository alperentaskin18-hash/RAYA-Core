import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from .storage import child
from .core import digest, encode, require

WORKER = Path(__file__).with_name("worker.py")


def lock(manifest):
    return {
        "engine": "raya-text-1",
        "worker": hashlib.sha256(WORKER.read_bytes()).hexdigest(),
        "interpreter": sys.implementation.cache_tag,
        "version": sys.version,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        **manifest.get(
            "limits", {"accelerator": "cpu", "memory_mib": 192, "timeout_s": 3}
        ),
    }


def install(profile, manifest):
    runtime = digest(lock(manifest))
    directory = child(
        child(profile.directory, "runtimes", directory=True), runtime, directory=True
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = child(directory, "worker.py")
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            output.write(WORKER.read_bytes())
        os.replace(output.name, path)
    require(
        hashlib.sha256(path.read_bytes()).hexdigest() == lock(manifest)["worker"],
        "damaged runtime",
    )
    return runtime


def execute(profile, record, argument):
    current = lock(record["manifest"])
    require(record["runtime"] == digest(current), "incompatible runtime")
    directory = child(
        child(profile.directory, "runtimes", directory=True),
        record["runtime"],
        directory=True,
    )
    worker = child(directory, "worker.py")
    with worker.open("rb") as source:
        program = source.read(WORKER.stat().st_size + 1)
    require(
        hashlib.sha256(program).hexdigest() == current["worker"],
        "damaged runtime",
    )
    try:
        with tempfile.TemporaryDirectory() as directory:
            env = {k: os.environ[k] for k in ("SystemRoot",) if k in os.environ}
            process = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-X",
                    "utf8",
                    "-c",
                    program.decode("utf-8"),
                    str(current["memory_mib"]),
                ],
                cwd=directory,
                env=env,
                timeout=current["timeout_s"],
                capture_output=True,
                input=encode([record["manifest"]["operation"], argument]),
                encoding="utf-8",
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("runtime timeout") from exc
    require(process.returncode == 0, "runtime failed")
    return json.loads(process.stdout)
