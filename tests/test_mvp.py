"""CLI integration and boundary regressions with disposable user-owned profiles."""

import concurrent.futures
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


class MVP(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.root / "profile"
        self.cli = [sys.executable, "-m", "raya", "--profile", str(self.profile)]

    def call(self, command, data=None, *, fails=False):
        result = subprocess.run(
            self.cli + [command, "-"],
            input=json.dumps(data or {}),
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, int(fails), result.stderr)
        return json.loads(result.stderr if fails else result.stdout)

    def test_provider_consent_and_receipt(self):
        calls = []

        class Service(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self):
                calls.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"content":"synthetic response"}')

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Service) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.call("generate", {"prompt": "fixture"}, fails=True)
                self.call(
                    "set", {"field": "provider_port", "value": server.server_port}
                )
                self.call("set", {"field": "billing_policy", "value": "legacy"})
                self.call("set", {"field": "provider", "value": "local-llama"})
                self.assertEqual(self.call("provider-status")["state"], "ready")
                first, second, third = [
                    self.call("generate", {"prompt": "fixture"}) for _ in range(3)
                ]
                self.assertEqual(calls, [])
                self.assertNotEqual(first["digest"], second["digest"])
                self.assertEqual(
                    self.call(
                        "approve", {"id": second["id"], "digest": first["digest"]}
                    )["state"],
                    "denied",
                )

                def approve():
                    return subprocess.run(
                        self.cli
                        + [
                            "approve",
                            json.dumps({"id": first["id"], "digest": first["digest"]}),
                        ],
                        text=True,
                        capture_output=True,
                        timeout=10,
                    )

                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    results = [
                        f.result() for f in [pool.submit(approve), pool.submit(approve)]
                    ]
                self.assertEqual(sorted(r.returncode for r in results), [0, 1])
                self.assertEqual(
                    calls, [{"prompt": "fixture", "n_predict": 256, "stream": False}]
                )
                self.assertEqual(
                    self.call("requests")[first["id"]]["result"], "synthetic response"
                )
                self.call("set", {"field": "provider", "value": "off"})
                self.assertEqual(
                    self.call(
                        "approve", {"id": third["id"], "digest": third["digest"]}
                    )["state"],
                    "denied",
                )
                self.assertEqual(len(calls), 1)
                self.call("set", {"field": "provider", "value": "local-llama"})
                for answer, expected in [("no", "denied"), ("yes", "completed")]:
                    attempt = subprocess.run(
                        self.cli + ["ask", "review me"],
                        input=answer + "\n",
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertIn(first["destination"], attempt.stderr)
                    self.assertEqual(json.loads(attempt.stdout)["state"], expected)
                self.assertEqual(len(calls), 2)
            finally:
                server.shutdown()
                thread.join()

    def test_private_profile_and_database_boundary(self):
        self.profile.mkdir(mode=0o700)
        other = self.root / "marker"
        other.write_bytes(b"fixture")
        os.link(other, self.profile / "core.db")
        self.assertIn("unsafe", self.call("settings", fails=True)["error"])
        self.assertEqual(other.read_bytes(), b"fixture")
        if os.name == "posix":
            (self.profile / "core.db").unlink()
            self.profile.chmod(0o755)
            self.assertIn("private", self.call("settings", fails=True)["error"])

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink support")
    def test_runtime_directory_boundary(self):
        spec = dict(provider="boundary", action="normalize", version="1",
                    runtime="raya-text-1", operation="lower", permissions=[],
                    input="string", output="string", license="test-fixture")
        receipt = self.call("install", spec)
        other = self.root / "other"
        other.mkdir()
        (self.profile / "runtimes").symlink_to(other, target_is_directory=True)
        result = self.call(
            "approve", {"id": receipt["id"], "digest": receipt["digest"]}
        )
        self.assertEqual(result["state"], "failed")
        self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(self.call("skills"), {})

    def test_dashboard_rejects_ambiguous_and_browser_credentials(self):
        with subprocess.Popen(
            self.cli + ["dashboard"], stdout=subprocess.PIPE, text=True
        ) as process:
            try:
                info = json.loads(process.stdout.readline())
                for headers in [
                    [
                        ("Authorization", "Bearer " + info["bearer"]),
                        ("Authorization", "Bearer other"),
                    ],
                    [
                        ("Authorization", "Bearer " + info["bearer"]),
                        ("Origin", "https://example.invalid"),
                    ],
                ]:
                    conn = http.client.HTTPConnection(
                        "127.0.0.1", info["port"], timeout=2
                    )
                    conn.putrequest("GET", "/progress")
                    for key, val in headers:
                        conn.putheader(key, val)
                    conn.endheaders()
                    self.assertEqual(conn.getresponse().status, 403)
                    conn.close()
            finally:
                process.terminate()
                process.wait(timeout=4)
