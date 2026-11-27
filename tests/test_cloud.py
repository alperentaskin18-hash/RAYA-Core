"""Synthetic pricing evidence and loopback transports; no hosted inference."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from raya import ai_protocol as wire, chatgpt_auth, cloud, engine, vault, voice
from raya.core import Profile, decide


class CloudTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.p = Profile(tmp.name)
        self.addCleanup(self.p.db.close)
        self.route = {
            "provider": "openrouter",
            "model": "fixture:free",
            "account": "test",
            "project": "test",
            "pool": "shared",
        }
        self.omni = {
            "provider": "omniroute",
            "model": "free",
            "upstream": "openrouter",
            "account": "test",
            "project": "test",
            "pool": "shared",
        }
        vault.store(self.p, "ai-openrouter", "fixture-router-key")
        vault.store(self.p, "ai-omniroute", "fixture-gateway-key")
        self.routes = {"direct": self.route, "gateway": self.omni}
        engine.configure(self.p, {"offline": False, "routes": self.routes})
        self.models = [
            {
                "id": "fixture:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 8192,
            },
            {"id": "paid", "pricing": {"prompt": "0.002", "completion": "0.1"}},
        ]
        self.tier = True

    def get(self, p, r, path):
        if path.endswith("/key"):
            return {"data": {"is_free_tier": self.tier}}, None
        return {"data": self.models}, {"version": "3.8.48"} if r[
            "provider"
        ] == "omniroute" else {}

    def refresh(self):
        with patch.object(cloud, "get_json", side_effect=self.get):
            return cloud.discover(self.p, "direct")

    def test_unknown_tier_paid_fallback_and_billing_changes(self):
        self.assertTrue(cloud.strict(self.p))
        self.refresh()
        with self.assertRaises(ValueError):
            engine.prepare(
                self.p, {"prompt": "x", "route": "direct", "fallback": ["gateway"]}
            )
        with self.assertRaises(ValueError):
            engine.prepare(
                self.p,
                {
                    "prompt": "x",
                    "tools": [
                        {"name": "hosted_search", "parameters": {"type": "object"}}
                    ],
                },
            )
        self.tier = False
        self.refresh()
        with self.assertRaises(ValueError):
            engine.prepare(self.p, {"prompt": "x"})
        self.tier = True
        self.refresh()
        req = engine.prepare(self.p, {"prompt": "x"})
        self.models[0]["pricing"]["request"] = "1"
        self.refresh()
        with patch.object(engine, "exchange") as transport, self.p.transaction():
            result = decide(self.p, req["id"], req["digest"], True)
            self.assertEqual(result["state"], "failed")
            transport.assert_not_called()
        cloud.annotate(
            self.p,
            {
                "pool": "shared",
                "classification": "grant",
                "reference": "owner",
                "counters": {"credits_remaining": 10},
            },
        )
        with self.assertRaises(ValueError):
            engine.prepare(self.p, {"prompt": "x"})
        voice.configure(self.p, {"synthesis": "openai"})
        with self.assertRaises(ValueError):
            voice.propose_cloud(self.p, "x")

    def test_concurrent_holds_shared_pool_restart_and_headers(self):
        self.refresh()
        cloud.limit(
            self.p,
            {"pool": "shared", "limits": {"requests_day": 1, "tokens_month": 10000}},
        )
        gate = threading.Barrier(2)
        won = []

        def attempt(i):
            p = Profile(self.p.directory)
            try:
                gate.wait(3)
                cloud.reserve(
                    p, "direct", self.route, {"max_output_tokens": 2}, 100, str(i)
                )
                won.append(str(i))
            except ValueError:
                pass
            finally:
                p.db.close()

        workers = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(4)
        self.assertEqual(len(won), 1)
        p = Profile(self.p.directory)
        try:
            self.assertFalse(cloud.capacity(p, self.omni, 1))
            cloud.settle(p, won[0], {"state": "failed", "usage": {}, "latency_ms": 9})
            self.assertEqual(p.get("cloud_reservations", won[0])["tokens"], 100)
            self.assertEqual(
                cloud.report(p)["by_dimension"]["pool"]["shared"]["unknown_cost"], 1
            )
            cloud.limit(p, {"pool": "shared", "limits": {}})
            response = type(
                "Response",
                (),
                {
                    "status": 429,
                    "getheader": lambda _, k: {
                        "Retry-After": "30",
                        "x-ratelimit-remaining-tokens": "0",
                        "x-ratelimit-reset-tokens": "1m",
                    }.get(k),
                },
            )()
            cloud.observe(p, self.route, response)
            self.assertFalse(cloud.capacity(p, self.omni, 1))
        finally:
            p.db.close()
        with self.assertRaises(ValueError):
            engine.configure(
                self.p,
                {"routes": dict(self.routes, gateway=dict(self.omni, pool="double"))},
            )

    def test_outstanding_hold_and_partial_header_updates(self):
        self.refresh()
        cloud.reserve(
            self.p, "direct", self.route, {"max_output_tokens": 2}, 10, "flight"
        )

        def response(values):
            return type(
                "Response", (), {"status": 200, "getheader": lambda _, k: values.get(k)}
            )()

        cloud.observe(
            self.p,
            self.route,
            response(
                {
                    "x-ratelimit-remaining-requests": "1",
                    "x-ratelimit-reset-requests": "5m",
                }
            ),
        )
        cloud.settle(
            self.p, "flight", {"state": "completed", "usage": {}, "latency_ms": 4}
        )
        cloud.observe(self.p, self.route, response({"x-ratelimit-limit-tokens": "500"}))
        self.assertFalse(cloud.capacity(self.p, self.omni, 1))

    def test_quota_reads_keep_source_and_do_not_authorize_gateway(self):
        def get(p, r, path):
            if path == "/api/v1/credits":
                return {"data": {"total_credits": 3, "total_usage": 2}}, None
            if path == "/api/usage/token-limits?apiKeyId=test-id":
                return {
                    "limits": [{"remaining": 42, "nextResetAt": "2030-01-01T00:00:00Z"}]
                }, {"version": "3.8.48"}
            return self.get(p, r, path)

        with patch.object(cloud, "get_json", side_effect=get):
            cloud.discover(self.p, "gateway")
            direct = cloud.quota_snapshot(self.p, {"route": "direct"})
            gateway = cloud.quota_snapshot(
                self.p, {"route": "gateway", "key_id": "test-id"}
            )
        self.assertEqual(direct["derived_remaining_usd"], "1")
        self.assertEqual(gateway["windows"][0]["remaining"], 42)
        self.assertEqual(
            cloud.report(self.p)["quota_sources"]["gateway"]["source"],
            "/api/usage/token-limits",
        )
        self.assertFalse(
            cloud.eligible(self.p, "gateway", self.omni, {"max_output_tokens": 1})
        )

    def test_zero_price_request_and_usage_settlement(self):
        self.refresh()
        req = engine.prepare(self.p, {"prompt": "hello"})
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(200)
                self.send_header("x-ratelimit-remaining-requests", "15")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"content": "17"}, "finish_reason": "stop"}
                            ],
                            "usage": {
                                "prompt_tokens": 4,
                                "completion_tokens": 3,
                                "completion_tokens_details": {"reasoning_tokens": 1},
                                "cost": 0,
                            },
                        }
                    ).encode()
                )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=server.serve_forever)
        t.start()
        try:

            def local(host, port, timeout):
                self.assertEqual(host, "openrouter.ai")
                return http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=timeout
                )

            with (
                patch.object(http.client, "HTTPSConnection", side_effect=local),
                self.p.transaction(),
            ):
                receipt = decide(self.p, req["id"], req["digest"], True)
            self.assertEqual(receipt["state"], "completed", receipt)
            self.assertEqual(
                received[0]["provider"],
                self.p.get("cloud_catalog", "direct")["direct_request_policy"],
            )
            self.assertFalse(received[0]["provider"]["allow_fallbacks"])
            self.assertEqual(received[0]["provider"]["max_price"]["prompt"], 0)
            history = next(iter(cloud.report(self.p)["history"].values()))
            self.assertEqual(
                (
                    history["tokens"],
                    history["usage"]["reasoning_tokens"],
                    history["reported_cost_usd"],
                ),
                (7, 1, 0),
            )
        finally:
            server.shutdown()
            t.join()
            server.server_close()

    def test_provider_paths_omni_version_and_catalog(self):
        expected = {
            "sambanova": "/v1/models",
            "mistral": "/v1/models",
            "nim": "/v1/models",
            "cohere": "/v1/models",
            "cloudflare": "/client/v4/accounts/" + "a" * 32 + "/ai/models/search",
            "omniroute": "/v1/models",
            "gemini": "/v1beta/openai/models",
            "groq": "/openai/v1/models",
        }
        for provider, path in expected.items():
            with self.subTest(provider=provider):
                r = {"provider": provider, "model": "test", "account": "a" * 32}
                engine.configure(self.p, {"routes": {"selected": r}})
                key = "ai-" + provider
                if not vault.read_secret(self.p, key):
                    vault.store(self.p, key, "fixture-key-" + provider)
                collection = {"cohere": "models", "cloudflare": "result"}.get(
                    provider, "data"
                )

                def get(p, route, uri):
                    self.assertEqual(path, uri)
                    self.assertIn(
                        "Authorization", engine.headers_for(p, provider, route)
                    )
                    return {collection: [{"id": "test", "name": "test"}]}, {
                        "version": "3.8.48"
                    }

                with patch.object(cloud, "get_json", side_effect=get):
                    self.assertEqual(
                        cloud.discover(self.p, "selected")["models"][0]["id"], "test"
                    )
                self.assertIn("chat/completions", engine.endpoint(r))
        engine.configure(self.p, {"routes": self.routes})
        with patch.object(cloud, "get_json", return_value=({"data": []}, None)):
            self.assertEqual(cloud.discover(self.p, "gateway")["state"], "failed")

    def test_owner_omniroute_catalog_over_http(self):
        # Only five owner-observed IDs are known; pad to the observed size with synthetic IDs.
        aliases = [
            "auto/best-" + name
            for name in ("coding", "reasoning", "fast", "vision", "chat")
        ]
        ids = aliases + [f"fixture/model-{n}" for n in range(160)]
        rows = [
            {
                "id": name,
                "object": "model",
                "created": 1,
                "owned_by": "combo",
                "root": name,
                "parent": None,
                "permission": [],
            }
            for name in ids
        ]
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-omniroute-route-class", "CLIENT_API")
                self.send_header("x-model-catalog-version", "model-metadata-v1:static")
                self.end_headers()
                self.wfile.write(json.dumps({"object": "list", "data": rows}).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        route = dict(self.omni, model=aliases[0], port=server.server_port)
        engine.configure(self.p, {"routes": {"gateway": route}})
        catalog = cloud.discover(self.p, "gateway")
        self.assertEqual(requests, [("/v1/models", "Bearer fixture-gateway-key")])
        self.assertEqual(catalog["state"], "ready")
        self.assertIsNone(catalog["version"])
        self.assertEqual(catalog["route_class"], "CLIENT_API")
        self.assertEqual(catalog["catalog_version"], "model-metadata-v1:static")
        self.assertEqual([m["id"] for m in catalog["models"]], ids)
        self.assertEqual(catalog["spending_class"], "unknown")
        self.assertEqual(catalog["upstream_identity"], "unknown")
        self.assertEqual(catalog["paid_fallback_disabled"], "unverified")
        self.assertEqual(catalog["gateway_key_validation"], "unverified")
        with self.assertRaises(ValueError):
            engine.prepare(self.p, {"route": "gateway", "prompt": "x"})
        self.assertEqual(self.p.all("cloud_reservations"), {})
        # Versionless catalog access cannot establish management quota compatibility.
        with patch.object(cloud, "get_json", return_value=({"limits": []}, {})):
            snap = cloud.quota_snapshot(
                self.p, {"route": "gateway", "key_id": "fixture"}
            )
        self.assertEqual(snap["state"], "UNKNOWN")
        self.assertIn("management response", snap["reason"])

    def test_discovery_diagnostics_do_not_echo_provider_content(self):
        for status, hint in (
            (401, "credential"),
            (403, "permission"),
            (404, "endpoint"),
            (429, "rate limit"),
            (200, "JSON"),
        ):
            with (
                self.subTest(status=status),
                patch.object(http.client, "HTTPConnection") as connect,
            ):
                response = (
                    connect.return_value.getresponse.return_value.__enter__.return_value
                )
                response.status = status
                response.getheader.return_value = None
                response.read.return_value = b"<html>fixture-gateway-key</html>"
                result = engine.probe(self.p, "gateway")
                self.assertEqual(result["state"], "failed")
                self.assertIn(hint, result["reason"])
                self.assertNotIn("fixture-gateway-key", json.dumps(result))
        for body in (
            [],
            {"object": "list", "data": "invalid"},
            {"object": "list", "data": [{}]},
        ):
            with (
                self.subTest(body=body),
                patch.object(cloud, "get_json", return_value=(body, {})),
            ):
                result = cloud.discover(self.p, "gateway")
                self.assertEqual(result["state"], "failed")
                self.assertTrue(result["reason"])

    def test_oauth_permission_rotation_switch_and_logout(self):
        cfg = chatgpt_auth.registration(
            self.p,
            {
                "client_id": "independent-fixture",
                "scopes": ["response"],
                "reference": "fixture",
                "owner_confirms_registration": True,
                "distribution": "open-source",
                "paid_product": False,
                "port": 17814,
                "responses_url": "https://api.openai.com/v1/responses",
            },
        )
        with self.assertRaises(ValueError):
            chatgpt_auth.accept(
                self.p, "one", cfg, {"access_token": "x", "token_type": "Bearer"}
            )
        for alias in ("one", "two"):
            chatgpt_auth.accept(
                self.p,
                alias,
                cfg,
                {
                    "access_token": "private-access-" + alias,
                    "refresh_token": "private-refresh-" + alias,
                    "scope": "response",
                    "token_type": "Bearer",
                    "expires_in": 10,
                },
            )
        chatgpt_auth.account(self.p, "switch", "one")
        with (
            patch.object(
                chatgpt_auth,
                "metadata",
                return_value={"token_endpoint": "https://auth.openai.com/oauth/token"},
            ),
            patch.object(
                chatgpt_auth,
                "oauth_request",
                return_value={
                    "access_token": "rotated-secret",
                    "token_type": "Bearer",
                    "expires_in": 600,
                },
            ),
        ):
            chatgpt_auth.account(self.p, "refresh", "one")
            self.assertEqual(
                vault.read_secret(self.p, "chatgpt-access-one"), "rotated-secret"
            )
            chatgpt_auth.account(self.p, "logout", "one")
        self.assertIsNone(vault.read_secret(self.p, "chatgpt-access-one"))
        self.assertNotIn("private-", json.dumps(self.p.all("oauth_accounts")))
        reply = wire.Reply(lambda _: None)
        reply.responses(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {"input_tokens": 2},
            },
            False,
        )
        self.assertEqual(reply.text, "ok")

    def test_gemini_billing_is_bound_and_rechecked_before_send(self):
        r = {"provider": "gemini", "model": "fixture", "project": "123456"}
        engine.configure(self.p, {"routes": {"google": r}})
        vault.store(self.p, "ai-gemini", "fixture-gemini-key")
        with (
            patch.object(
                cloud, "get_json", return_value=({"data": [{"id": "fixture"}]}, None)
            ),
            patch.object(cloud, "google_unbilled", return_value={"unbilled": True}),
        ):
            cloud.discover(self.p, "google")
            req = engine.prepare(self.p, {"prompt": "x"})
        with (
            patch.object(cloud, "google_unbilled", return_value={"unbilled": False}),
            patch.object(engine, "exchange") as transport,
            self.p.transaction(),
        ):
            self.assertEqual(
                decide(self.p, req["id"], req["digest"], True)["state"], "failed"
            )
            transport.assert_not_called()
        with self.assertRaises(ValueError):
            self.p.configure("billing_policy", "legacy")

    def test_pkce_callback_rejects_unmatched_state(self):
        import base64, hashlib, socket
        from urllib.parse import parse_qs, urlsplit, urlencode

        local = socket.socket()
        local.bind(("127.0.0.1", 0))
        port = local.getsockname()[1]
        local.close()
        cfg = chatgpt_auth.registration(
            self.p,
            {
                "client_id": "assigned-fixture",
                "scopes": ["response"],
                "reference": "fixture",
                "owner_confirms_registration": True,
                "distribution": "open-source",
                "paid_product": False,
                "port": port,
                "responses_url": "https://api.openai.com/v1/responses",
            },
        )
        seen = {}
        statuses = []

        def visit(url):
            seen.update(parse_qs(urlsplit(url).query))

            def callback():
                for state in ("wrong", seen["state"][0]):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    try:
                        conn.request(
                            "GET",
                            "/callback?"
                            + urlencode({"state": state, "code": "fixture"}),
                        )
                        with conn.getresponse() as res:
                            statuses.append(res.status)
                    finally:
                        conn.close()

            t = threading.Thread(target=callback)
            t.start()
            self.addCleanup(t.join, 3)
            return True

        def token(endpoint, form):
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"].encode()).digest()
                )
                .decode()
                .rstrip("=")
            )
            self.assertEqual(challenge, seen["code_challenge"][0])
            self.assertEqual(form["resource"], cfg["responses_url"])
            return {
                "scope": "response",
                "access_token": "test-pkce-access",
                "expires_in": 600,
                "token_type": "Bearer",
            }

        with (
            patch.object(
                chatgpt_auth,
                "metadata",
                return_value={
                    "authorization_endpoint": "https://auth.openai.com/authorize",
                    "token_endpoint": "https://auth.openai.com/token",
                },
            ),
            patch.object(chatgpt_auth, "oauth_request", side_effect=token),
            patch("webbrowser.open", side_effect=visit),
        ):
            self.assertEqual(
                chatgpt_auth.login(self.p, "account")["entitlement"], "UNKNOWN"
            )
        self.assertEqual(statuses, [400, 200])

    def test_scoring_requires_real_receipt_and_is_single_use(self):
        self.refresh()
        req = cloud.eval_request(self.p, {"kind": "reasoning", "route": "direct"})
        with self.assertRaises(ValueError):
            cloud.eval_score(self.p, req["id"])
        with (
            patch.object(
                engine,
                "exchange",
                return_value={
                    "text": "17",
                    "usage": {"input_tokens": 7, "output_tokens": 1},
                },
            ),
            self.p.transaction(),
        ):
            self.assertEqual(
                decide(self.p, req["id"], req["digest"], True)["state"], "completed"
            )
        self.assertTrue(cloud.eval_score(self.p, req["id"])["passed"])
        self.assertEqual(
            cloud.report(self.p)["rankings"]["reasoning"][0]["evidence"][
                "tokens_per_pass"
            ],
            8,
        )
        with self.assertRaises(ValueError):
            cloud.eval_score(self.p, req["id"])
