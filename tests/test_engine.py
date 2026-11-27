"""Deterministic wire-level provider acceptance; every socket terminates on loopback."""

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from raya import engine, vault
from raya.core import Profile, decide

PATHS = {
    "openai": "/v1/chat/completions",
    "anthropic": "/v1/messages",
    "gemini": "/v1beta/openai/chat/completions",
    "xai": "/v1/chat/completions",
    "deepseek": "/chat/completions",
    "groq": "/openai/v1/chat/completions",
    "openrouter": "/api/v1/chat/completions",
    "ollama": "/v1/chat/completions",
    "llama.cpp": "/v1/chat/completions",
}
OBJECT = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "required": ["n"],
    "additionalProperties": False,
}
TOOL = {"name": "count", "description": "Return a count", "parameters": OBJECT}


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        if status == 429 and self.server.retry_after:
            self.send_header("Retry-After", self.server.retry_after)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.paths.append(self.path)
        self.send_json({"data": [{"id": "fixture"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append((self.path, dict(self.headers), body))
        if self.server.errors:
            self.send_json({"error": "synthetic"}, self.server.errors.pop(0))
            return
        mode = self.server.mode
        is_claude = self.path == "/v1/messages"
        text = (
            '{"n":"bad"}'
            if mode == "invalid"
            else '{"n":4}'
            if mode == "schema"
            else "ready"
        )
        tool = mode == "tool"
        if not body["stream"]:
            if is_claude:
                block = (
                    {"type": "tool_use", "id": "t1", "name": "count", "input": {"n": 4}}
                    if tool
                    else {"type": "text", "text": text}
                )
                self.send_json(
                    {
                        "content": [block],
                        "stop_reason": "tool_use" if tool else "end_turn",
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    }
                )
            else:
                msg = (
                    {"content": text}
                    if not tool
                    else {
                        "tool_calls": [
                            {
                                "id": "t1",
                                "function": {"name": "count", "arguments": '{"n":4}'},
                            }
                        ]
                    }
                )
                self.send_json(
                    {
                        "choices": [
                            {
                                "message": msg,
                                "finish_reason": "tool_calls" if tool else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                    }
                )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(data):
            self.wfile.write(b"data: " + json.dumps(data).encode() + b"\r\n\r\n")
            self.wfile.flush()

        if mode == "paused_stream":
            # Split an SSE line across multiple cancellation polling intervals.
            self.wfile.write(b'data: {"choices":')
            self.wfile.flush()
            time.sleep(0.35)
            self.wfile.write(b'[{"delta":{"content":"partial "}}]}\r\n\r\n')

        if mode == "slow_stream":
            event({"choices": [{"index": 0, "delta": {"content": "started"}}]})
            self.server.started.set()
            self.server.release.wait(5)
            return
        if is_claude:
            event(
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 4, "output_tokens": 0}},
                }
            )
            if tool:
                event(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "name": "count",
                            "id": "t1",
                            "input": {},
                        },
                    }
                )
                for part in ['{"n":', "4}"]:
                    event(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "input_json_delta", "partial_json": part},
                        }
                    )
            else:
                for part in (text[:2], text[2:]):
                    event(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": part},
                        }
                    )
            event(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
                    "usage": {"output_tokens": 2},
                }
            )
            event({"type": "message_stop"})
        else:

            def delta(data, reason=None):
                event(
                    {"choices": [{"index": 0, "delta": data, "finish_reason": reason}]}
                )

            if tool:
                delta(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "t1",
                                "function": {"name": "count", "arguments": '{"n":'},
                            }
                        ]
                    }
                )
                delta({"tool_calls": [{"index": 0, "function": {"arguments": "4}"}}]})
            else:
                for part in (text[:2], text[2:]):
                    delta({"content": part})
            delta({}, "tool_calls" if tool else "stop")
            event(
                {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
            )
            if mode != "truncated":
                self.wfile.write(b"data: [DONE]\n\n")


class EngineIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.p = Profile(self.dir)
        self.addCleanup(self.p.db.close)
        srv = self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        srv.calls = []
        srv.errors = []
        srv.paths = []
        srv.mode = "text"
        srv.retry_after = None
        srv.started = threading.Event()
        srv.release = threading.Event()
        worker = threading.Thread(
            target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        worker.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.release.set)
        self.hosts = []

        def loopback(host, port, timeout):
            self.assertEqual(port, 443)
            self.hosts.append(host)
            return http.client.HTTPConnection(
                "127.0.0.1", srv.server_port, timeout=timeout
            )

        intercept = patch.object(http.client, "HTTPSConnection", side_effect=loopback)
        intercept.start()
        self.addCleanup(intercept.stop)
        self.routes = {}
        for provider in PATHS:
            name = provider.replace(".", "-")
            spec = {
                "provider": provider,
                "model": "fixture",
                "features": ["stream", "tools", "structured"],
            }
            if provider in ("ollama", "llama.cpp"):
                spec["port"] = srv.server_port
            else:
                vault.store(self.p, "ai-" + provider, "fixture-key-" + provider)
            self.routes[name] = spec
        engine.configure(self.p, {"routes": self.routes, "offline": False})

    def prepare(self, **args):
        with self.p.transaction():
            return engine.prepare(self.p, dict(prompt="sample", **args))

    def approve(self, req, emit=None):
        with self.p.transaction():
            return decide(self.p, req["id"], req["digest"], True, emit)

    def test_provider_matrix(self):
        for alias, r in self.routes.items():
            p = r["provider"]
            with self.subTest(p=p):
                self.assertTrue(engine.probe(self.p, alias)["model_listed"])
                for stream in (False, True):
                    for mode in ("text", "schema", "tool"):
                        self.srv.mode = mode
                        options = (
                            {"tools": [TOOL]}
                            if mode == "tool"
                            else {"schema": OBJECT}
                            if mode == "schema"
                            else {}
                        )
                        req = self.prepare(route=alias, stream=stream, **options)
                        deltas = []
                        done = self.approve(req, deltas.append)
                        self.assertEqual(done["state"], "completed", done)
                        result = done["result"]
                        self.assertEqual(
                            result["usage"], {"input_tokens": 4, "output_tokens": 2}
                        )
                        path, headers, body = self.srv.calls[-1]
                        self.assertEqual(path, PATHS[p])
                        self.assertEqual(body["model"], "fixture")
                        self.assertEqual(
                            req["content"]["routes"][0]["model"], "fixture"
                        )
                        self.assertEqual(body["stream"], stream)
                        if p == "anthropic":
                            self.assertEqual(
                                headers["x-api-key"], "fixture-key-anthropic"
                            )
                            self.assertEqual(headers["anthropic-version"], "2023-06-01")
                        elif p not in ("ollama", "llama.cpp"):
                            self.assertEqual(
                                headers["Authorization"], "Bearer fixture-key-" + p
                            )
                        else:
                            self.assertNotIn("Authorization", headers)
                        if mode == "tool":
                            self.assertEqual(
                                result["tool_calls"],
                                [dict(id="t1", name="count", arguments={"n": 4})],
                            )
                            declaration = body["tools"][0]
                            self.assertEqual(
                                declaration["input_schema"]
                                if p == "anthropic"
                                else declaration["function"]["parameters"],
                                OBJECT,
                            )
                        elif mode == "schema":
                            self.assertEqual(result["structured"], {"n": 4})
                            if p == "anthropic":
                                self.assertEqual(
                                    body["output_config"]["format"]["schema"], OBJECT
                                )
                            elif p == "deepseek":
                                self.assertEqual(
                                    body["response_format"]["type"], "json_object"
                                )
                            else:
                                self.assertEqual(
                                    body["response_format"]["json_schema"]["schema"],
                                    OBJECT,
                                )
                        if stream:
                            self.assertEqual("".join(deltas), result["text"])
                        self.assertNotIn("fixture-key", json.dumps(done))
                totals = engine.usage(self.p)[p]
                self.assertEqual(totals["attempts"], 6)
                self.assertEqual(
                    totals["reported_tokens"], {"input_tokens": 24, "output_tokens": 12}
                )

    def test_route_authority_and_budgets(self):
        self.assertIn(
            self.prepare()["content"]["routes"][0]["provider"], {"ollama", "llama.cpp"}
        )
        req = self.prepare(route="ollama", fallback=["openai"])
        engine.configure(self.p, {"offline": True})
        self.assertEqual(self.approve(req)["state"], "denied")
        with self.assertRaises(ValueError):
            self.prepare(route="openai")
        with self.assertRaises(ValueError):
            engine.probe(self.p, "openai")
        with self.assertRaises(ValueError):
            self.prepare(budget=1)
        self.assertFalse(self.srv.calls)
        self.assertFalse(self.hosts)
        engine.configure(self.p, {"offline": False, "retries": 1})
        req = self.prepare(route="ollama", fallback=["openai"])
        self.srv.errors = [503, 429]
        result = self.approve(req)
        self.assertEqual(result["result"]["route"], "openai")
        self.assertEqual(result["result"]["attempts"], 3)
        self.assertEqual(engine.usage(self.p)["ollama"]["unreported_attempts"], 2)
        self.srv.errors = [401]
        req = self.prepare(route="ollama", fallback=["openai"])
        self.assertEqual(self.approve(req)["state"], "failed")
        self.assertEqual(len(self.srv.calls), 4)
        req = self.prepare(route="ollama")
        reservation = req["content"]["routes"][0]["reservation"]
        self.srv.errors = [503]
        req = self.prepare(route="ollama", budget=reservation)
        self.assertEqual(
            self.approve(req)["error"], "approved token reservation exhausted"
        )
        self.assertEqual(len(self.srv.calls), 5)

    def test_capability_selection_and_retry_after(self):
        self.routes["small"] = {
            "provider": "llama.cpp",
            "model": "fixture",
            "context_tokens": 4,
        }
        self.routes["copy"] = self.routes["ollama"]
        self.routes["text"] = {"provider": "llama.cpp", "model": "text"}
        engine.configure(self.p, {"routes": self.routes, "retries": 2})
        for route, options in [("small", {}), ("text", {"tools": [TOOL]})]:
            with self.assertRaises(ValueError):
                self.prepare(route=route, **options)
        vault.remove_provider_key(self.p, "gemini")
        self.assertEqual(
            engine.status(self.p)["routes"]["gemini"]["state"], "missing_key"
        )
        self.srv.errors = [429]
        self.srv.retry_after = "120"
        req = self.prepare(route="ollama", fallback=["copy", "openai"])
        result = self.approve(req)["result"]
        self.assertEqual((result["attempts"], result["route"]), (2, "openai"))
        self.assertEqual(self.hosts, ["api.openai.com"])

    def test_response_errors_and_tool_round_trip(self):
        self.srv.mode = "invalid"
        req = self.prepare(route="ollama", schema=OBJECT, fallback=["openai"])
        self.assertEqual(self.approve(req)["state"], "failed")
        self.srv.mode = "truncated"
        req = self.prepare(route="ollama", stream=True, fallback=["openai"])
        self.assertEqual(self.approve(req)["state"], "failed")
        self.assertFalse(self.hosts)
        self.srv.mode = "text"
        for alias in ("openai", "anthropic"):
            req = self.prepare(
                route=alias,
                tools=[TOOL],
                tool_results=[
                    dict(id="t1", name="count", arguments={"n": 4}, result="verified")
                ],
            )
            self.assertEqual(self.approve(req)["state"], "completed")
            msgs = self.srv.calls[-1][2]["messages"]
            self.assertEqual(
                msgs[-1]["role"], "user" if alias == "anthropic" else "tool"
            )
            self.assertIn("verified", json.dumps(msgs[-1]))

    def test_partial_stream_line_survives_idle_polls(self):
        self.srv.mode = "paused_stream"
        req = self.prepare(route="ollama", stream=True)
        deltas = []
        receipt = self.approve(req, deltas.append)
        self.assertEqual(receipt["state"], "completed", receipt)
        self.assertEqual(receipt["result"]["text"], "partial ready")
        self.assertEqual("".join(deltas), "partial ready")

    def test_cancellation_in_flight_and_pending(self):
        req = self.prepare(route="ollama", fallback=["openai"])
        engine.cancel(self.p, req["id"])
        with self.assertRaises(ValueError):
            self.approve(req)
        self.assertFalse(self.srv.calls)
        self.srv.mode = "slow_stream"
        req = self.prepare(route="ollama", stream=True, fallback=["openai"])
        receipt = []

        def approve_elsewhere():
            p = Profile(self.dir)
            try:
                with p.transaction():
                    receipt.append(decide(p, req["id"], req["digest"], True))
            finally:
                p.db.close()

        worker = threading.Thread(target=approve_elsewhere)
        worker.start()
        try:
            self.assertTrue(self.srv.started.wait(2))
            with self.p.transaction():
                self.assertEqual(
                    engine.cancel(self.p, req["id"])["state"], "cancel_requested"
                )
            worker.join(2)
            self.assertFalse(
                worker.is_alive(), "cancel must interrupt a stalled request"
            )
            self.assertEqual(receipt[0]["state"], "cancelled")
            self.assertFalse(self.hosts)
        finally:
            self.srv.release.set()
            worker.join(6)

    def test_stream_redacts_split_vault_values(self):
        vault.store(self.p, "private", "ready")
        req = self.prepare(route="ollama", stream=True)
        deltas = []
        done = self.approve(req, deltas.append)
        self.assertEqual("".join(deltas), "[redacted]")
        self.assertEqual(done["result"], "[redacted]")
