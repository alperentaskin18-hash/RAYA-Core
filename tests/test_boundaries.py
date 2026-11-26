import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class Boundaries(unittest.TestCase):
    def test_vault_values_excluded_from_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = [sys.executable, "-m", "raya", "--profile", tmp]
            secret = "fixture-rain-1473"
            saved = subprocess.run(
                args + ["secret-set", "demo"],
                input=secret,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertNotIn(secret, saved.stdout + saved.stderr)
            proc = subprocess.run(
                args + ["export", "-"],
                input=json.dumps(dict(name="sample", format="document", text=secret)),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertNotIn(secret, proc.stderr)
            self.assertNotIn(secret.encode(), (Path(tmp) / "core.db").read_bytes())
            self.assertFalse((Path(tmp) / "exports").exists())

    def test_worker_denies_file_and_socket_access(self):
        program = """
import io,json,runpy,socket,sys
sys.stdin=io.StringIO(json.dumps(["upper","sample"]))
worker=sys.argv[1]
sys.argv=[worker,"96"]
runpy.run_path(worker,run_name="__main__")
for attempt in [lambda: open(worker),socket.socket]:
    try:
        attempt()
    except PermissionError:
        continue
    raise SystemExit(9)
try:
    bytearray(256*1024*1024)
except MemoryError:
    pass
else:
    raise SystemExit(10)
"""
        worker = Path(__file__).resolve().parents[1] / "raya" / "worker.py"
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", program, str(worker)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), "SAMPLE")
