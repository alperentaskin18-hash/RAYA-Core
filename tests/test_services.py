"""Local HTTP fixtures and negative boundary checks. No external service calls."""

import base64
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from raya.core import Profile
from raya import learning, provider


class Services(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_pinned_import_and_restart(self):
        rev, tree = "3" * 40, "a" * 40
        entries = [
            dict(path=p, mode=m)
            for p, m in [
                ("pkg/__main__.py", "100644"),
                ("requirements.txt", "100644"),
                ("escape", "120000"),
            ]
        ]
        raw = b"example-pkg==7.2"
        entries[1]["sha"] = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\x00" + raw
        ).hexdigest()
        responses = [
            {
                "private": False,
                "full_name": "sample/demo",
                "license": {"spdx_id": "MIT"},
            },
            {"sha": rev, "tree": {"sha": tree}},
            {"truncated": False, "sha": tree, "tree": entries},
            {
                "encoding": "base64",
                "size": len(raw),
                "content": base64.b64encode(raw).decode(),
            },
        ]
        with patch.object(learning, "fetch", side_effect=responses) as fetch:
            p = Profile(self.directory)
            result = learning.learn(p, "https://github.com/sample/demo", rev)
            p.db.close()
            p = Profile(self.directory)
            self.assertEqual(
                result, learning.learn(p, "https://github.com/sample/demo", rev)
            )
            self.assertEqual(fetch.call_count, 4)
        self.assertEqual(result["interfaces"], ["pkg/__main__.py"])
        self.assertEqual(result["dependency_files"], ["requirements.txt"])
        self.assertEqual(result["excluded_links"], ["escape"])
        self.assertEqual(
            result["descriptors"]["requirements.txt"]["dependencies"],
            ["example-pkg==7.2"],
        )
        responses[2]["tree"] = [{"mode": "100644", "path": "../outside"}]
        p.drop("imports", "sample/demo@" + rev)
        with (
            patch.object(learning, "fetch", side_effect=responses),
            self.assertRaisesRegex(ValueError, "unsafe"),
        ):
            learning.learn(p, "https://github.com/sample/demo", rev)
        self.assertEqual(p.all("imports"), {})
        p.db.close()

    def test_export_and_stale_destination(self):
        def call(cmd, args):
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "raya",
                    "--profile",
                    str(self.directory),
                    cmd,
                    "-",
                ],
                input=json.dumps(args),
                text=True,
                capture_output=True,
                check=True,
            )
            return json.loads(proc.stdout)

        for name in ["first", "second"]:
            req = call("export", dict(format="document", name=name, text="tulip"))
            target = self.directory / "exports" / (name + ".txt")
            if name == "second":
                target.write_text("keep")
            result = call("approve", dict(id=req["id"], digest=req["digest"]))
            self.assertEqual(
                result["state"], "completed" if name == "first" else "denied"
            )
            self.assertEqual(target.read_text(), "tulip" if name == "first" else "keep")

    def test_live_dashboard_authentication(self):
        p = Profile(self.directory)
        p.put("goals", "a", {"title": "Sample", "state": "active", "tasks": {}})
        p.db.close()
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "raya",
                "--profile",
                str(self.directory),
                "dashboard",
            ],
            stdout=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                info = json.loads(proc.stdout.readline())
                conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=3)
                conn.request("GET", "/progress")
                self.assertEqual(conn.getresponse().status, 403)
                conn.close()
                conn.request(
                    "GET",
                    "/progress",
                    headers={"Authorization": "Bearer " + info["bearer"]},
                )
                self.assertEqual(json.load(conn.getresponse())["a"]["title"], "Sample")
                conn.close()
            finally:
                proc.terminate()
                proc.wait(timeout=4)

    def test_text_provider_transport(self):
        received = []

        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"content":"reply"}')

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Endpoint) as srv:
            thread = threading.Thread(target=srv.handle_request, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", srv.server_port, timeout=3
            )
            with patch.object(
                provider.http.client, "HTTPConnection", return_value=connection
            ):
                self.assertEqual(provider.generate({"prompt": "fixture"}), "reply")
            thread.join(timeout=3)
        self.assertEqual(received, [{"prompt": "fixture"}])
