"""Read-only progress dashboard for a local client or an SSH tunnel."""

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import Profile, encode


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(*args)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(4)
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def serve(directory, port):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            credentials = self.headers.get_all("Authorization", [])
            if len(credentials) != 1 or self.headers.get("Origin") is not None:
                self.send_error(403)
                return
            supplied = credentials[0].encode()
            if not hmac.compare_digest(supplied, ("Bearer " + token).encode()):
                self.send_error(403)
                return
            if self.path != "/progress":
                self.send_error(404)
                return
            p = Profile(directory, read_only=True)
            try:
                data = {
                    key: {
                        "title": g["title"],
                        "state": g["state"],
                        "tasks": {k: t["state"] for k, t in g["tasks"].items()},
                    }
                    for key, g in p.all("goals").items()
                }
            finally:
                p.db.close()
            body = encode(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with LocalServer(("127.0.0.1", port), Handler) as server:
        print(json.dumps({"port": server.server_port, "bearer": token}), flush=True)
        server.serve_forever()
