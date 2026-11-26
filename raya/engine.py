"""Approval-scoped AI routing, accounting and bounded HTTP generation."""

import contextlib
import http.client
import io
import json
import socket
import threading
import time

from . import ai_protocol as wire
from . import vault
from .core import Profile, digest, encode, identifier, propose, require, text_field

DEFAULT = {
    "offline": True,
    "routes": {},
    "preferred": "auto",
    "max_output_tokens": 256,
    "budget": 65536,
    "retries": 0,
}
FEATURES = {"stream", "structured", "tools"}


def integer(value, low, high, field):
    require(type(value) is int and low <= value <= high, "invalid " + field)
    return value


def config(profile):
    return profile.settings().get("ai", DEFAULT)


def configure(profile, patch):
    require(
        isinstance(patch, dict) and patch.keys() <= DEFAULT.keys(), "unknown AI setting"
    )
    cfg = dict(config(profile), **patch)
    require(type(cfg["offline"]) is bool, "invalid offline mode")
    integer(cfg["max_output_tokens"], 1, 32768, "output limit")
    integer(cfg["budget"], 1, 2000000, "budget")
    integer(cfg["retries"], 0, 2, "retries")
    routes = cfg["routes"]
    require(isinstance(routes, dict) and len(routes) <= 32, "invalid routes")
    for alias, route in routes.items():
        identifier(alias, "route alias")
        require(
            alias != "auto"
            and isinstance(route, dict)
            and route.keys()
            <= {"provider", "model", "features", "context_tokens", "port"},
            "invalid route",
        )
        provider = route.get("provider")
        require(provider in wire.HOSTS, "unknown provider")
        model = text_field(route.get("model"), "model", 256)
        require(model.isprintable(), "invalid model")
        features = route.get("features", [])
        require(
            isinstance(features, list) and set(features) <= FEATURES, "invalid features"
        )
        integer(route.get("context_tokens", 32768), 1, 2000000, "model context limit")
        if "port" in route:
            require(provider in wire.LOCAL, "cloud destination is fixed")
            integer(route["port"], 1, 65535, "local port")
    require(
        cfg["preferred"] == "auto" or cfg["preferred"] in routes,
        "unknown preferred route",
    )
    profile.put(
        "profile", "preferences", dict(profile.settings(), provider="engine", ai=cfg)
    )
    return cfg


def port(route):
    return route.get(
        "port", {"ollama": 11434, "llama.cpp": 8080}.get(route["provider"], 443)
    )


def endpoint(route):
    host, path = wire.HOSTS[route["provider"]]
    if route["provider"] in wire.LOCAL:
        return f"http://{host}:{port(route)}{path}"
    return f"https://{host}{path}"


def credential(profile, provider):
    return (
        vault.read_secret(profile, "ai-" + provider)
        if provider not in wire.LOCAL
        else None
    )


def status(profile):
    cfg = config(profile)
    observed = profile.all("ai_health")
    routes = {}
    for name, route in cfg["routes"].items():
        provider = route["provider"]
        state = "configured"
        if provider not in wire.LOCAL:
            if cfg["offline"]:
                state = "offline"
            elif not credential(profile, provider):
                state = "missing_key"
        routes[name] = {
            "model": route["model"],
            "provider": provider,
            "destination": endpoint(route),
            "state": state,
            "last_attempt": observed.get(digest(route)),
        }
    return {"providers": list(wire.HOSTS), "offline": cfg["offline"], "routes": routes}


def headers_for(profile, provider):
    headers = {"Content-Type": "application/json"}
    if provider not in wire.LOCAL:
        key = credential(profile, provider)
        require(
            key and key.isascii() and key.isprintable(),
            "provider credential unavailable",
        )
        if provider == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = "Bearer " + key
    return headers


def probe(profile, alias):
    cfg = config(profile)
    require(alias in cfg["routes"], "unknown route")
    route = cfg["routes"][alias]
    provider = route["provider"]
    require(
        provider in wire.LOCAL or not cfg["offline"],
        "offline mode blocks cloud health probes",
    )
    headers = headers_for(profile, provider)
    host, path = wire.HOSTS[provider]
    # Use the same fixed origin and credential as inference. A model-list probe
    # does not generate tokens; successful access does not establish quota.
    path = (
        path.rsplit("/chat/completions", 1)[0] + "/models"
        if provider != "anthropic"
        else "/v1/models"
    )
    factory = (
        http.client.HTTPConnection
        if provider in wire.LOCAL
        else http.client.HTTPSConnection
    )
    conn = factory(host, port(route), timeout=3)
    state = "failed"
    models = None
    try:
        conn.request("GET", path, headers=headers)
        with conn.getresponse() as response:
            if response.status == 200:
                raw = response.read(1048577)
                require(len(raw) <= 1048576, "model listing too large")
                data = json.loads(raw)["data"]
                if isinstance(data, list):
                    models = any(
                        isinstance(m, dict) and m.get("id") == route["model"]
                        for m in data
                    )
                    state = "ready"
    except (OSError, http.client.HTTPException, TypeError, KeyError, ValueError):
        pass
    finally:
        conn.close()
    record = {"state": state, "time": int(time.time()), "model_listed": models}
    profile.put("ai_health", digest(route), record)
    return record


def prepare(profile, args):
    cfg = config(profile)
    require(
        profile.settings().get("provider") == "engine",
        "unified provider engine disabled",
    )
    require(
        args.keys()
        <= {
            "prompt",
            "route",
            "fallback",
            "stream",
            "schema",
            "tools",
            "tool_results",
            "max_output_tokens",
            "budget",
        },
        "unknown generation option",
    )
    prompt = text_field(args.get("prompt"), "prompt", 65536)
    stream = args.get("stream", False)
    require(type(stream) is bool, "invalid stream")
    spec = args.get("schema")
    if spec is not None:
        wire.schema(spec)
        require(spec["type"] == "object", "structured output must use an object schema")
    tools = args.get("tools", [])
    contracts = wire.tool_map(tools)
    require(not (contracts and spec), "request tools or structured output per turn")
    previous = args.get("tool_results", [])
    require(isinstance(previous, list) and len(previous) <= 16, "invalid tool results")
    ids = set()
    for item in previous:
        require(
            isinstance(item, dict)
            and item.keys() == {"id", "name", "arguments", "result"}
            and item["name"] in contracts,
            "invalid tool result",
        )
        text_field(item["id"], "tool call id", 256)
        require(item["id"] not in ids, "duplicate tool result")
        ids.add(item["id"])
        wire.validate(item["arguments"], contracts[item["name"]]["parameters"])
        text_field(item["result"], "tool result", 16384)
    limit = integer(
        args.get("max_output_tokens", cfg["max_output_tokens"]),
        1,
        cfg["max_output_tokens"],
        "output limit",
    )
    budget = integer(args.get("budget", cfg["budget"]), 1, cfg["budget"], "budget")
    required = {
        k for k, v in (("stream", stream), ("structured", spec), ("tools", tools)) if v
    }
    content = dict(
        prompt=prompt,
        stream=stream,
        schema=spec,
        tools=tools,
        tool_results=previous,
        max_output_tokens=limit,
        budget=budget,
        retries=cfg["retries"],
    )
    states = status(profile)["routes"]
    named = args.get("route", cfg["preferred"])
    fallbacks = args.get("fallback", [])
    require(isinstance(named, str), "invalid primary route")
    require(
        isinstance(fallbacks, list)
        and len(fallbacks) <= 3
        and all(isinstance(n, str) for n in fallbacks)
        and len(set(fallbacks)) == len(fallbacks),
        "invalid fallback list",
    )

    def details(name):
        route = cfg["routes"].get(name)
        if (
            not route
            or states[name]["state"] != "configured"
            or not required <= set(route.get("features", []))
        ):
            return None
        reservation = limit + len(encode(wire.payload(route, content)).encode()) + 32
        if reservation > min(route.get("context_tokens", 32768), budget):
            return None
        return dict(
            alias=name,
            provider=route["provider"],
            model=route["model"],
            endpoint=endpoint(route),
            port=port(route),
            reservation=reservation,
        )

    if named == "auto":

        def rank(name):
            route = cfg["routes"][name]
            last = states[name]["last_attempt"] or {}
            return (
                last.get("state") == "failed"
                and time.time() - last.get("time", 0) < 60,
                route["provider"] not in wire.LOCAL,
                route.get("context_tokens", 32768),
                name,
            )

        named = next(
            (n for n in sorted(states, key=rank) if n not in fallbacks and details(n)),
            None,
        )
    require(named and named not in fallbacks, "no eligible primary route")
    plans = [details(n) for n in [named, *fallbacks]]
    require(all(plans), "route unavailable, incompatible, or over budget")
    content["routes"] = plans
    permissions = [
        "loopback.http"
        if p["provider"] in wire.LOCAL
        else "external.ai." + p["provider"]
        for p in plans
    ]
    return propose(
        profile,
        "ai-generate",
        plans[0]["endpoint"],
        content,
        digest(profile.settings()),
        list(dict.fromkeys(permissions)),
    )


class Cancelled(RuntimeError):
    pass


class Rejected(RuntimeError):
    def __init__(self, status, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"provider returned HTTP {status}")


def cancel(profile, token):
    request = profile.get("requests", token)
    require(request and request["operation"] == "ai-generate", "unknown AI request")
    require(request["state"] in {"pending", "in_progress"}, "request already closed")
    if request["state"] == "pending":
        request["state"] = "cancelled"
        profile.put("requests", token, request)
    else:
        profile.put("ai_cancel", token, True)
    return {
        "id": token,
        "state": "cancel_requested"
        if request["state"] == "in_progress"
        else "cancelled",
    }


class _ResponseReader(io.RawIOBase):
    """Poll cancellation between socket reads, including partial HTTP/SSE lines."""

    def __init__(self, sock, socket_file, is_interrupted):
        self.sock = sock
        # HTTPConnection can close its copy once it sees Connection: close.
        # Keep HTTPResponse's socket file alive until its new buffer closes.
        self.socket_file = socket_file
        self.is_interrupted = is_interrupted
        self.idle_timeout = sock.gettimeout()
        sock.settimeout(0.1)

    def readable(self):
        return True

    def readinto(self, buffer):
        deadline = time.monotonic() + self.idle_timeout
        while not self.is_interrupted():
            try:
                # A timeout on socket.makefile() poisons subsequent reads.
                # Read the socket directly; BufferedReader retains partial lines.
                return self.sock.recv_into(buffer)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise
        raise RuntimeError("provider interrupted")

    def close(self):
        try:
            self.socket_file.close()
        finally:
            super().close()


@contextlib.contextmanager
def connection(profile, token, route):
    host, _ = wire.HOSTS[route["provider"]]
    factory = (
        http.client.HTTPConnection
        if route["provider"] in wire.LOCAL
        else http.client.HTTPSConnection
    )
    conn = factory(host, route["port"], timeout=15)
    done = threading.Event()
    interrupted = threading.Event()
    deadline = time.monotonic() + 60

    def check_cancel(p):
        if p.get("ai_cancel", token) or time.monotonic() >= deadline:
            interrupted.set()

    def response_factory(sock, *args, **kwargs):
        # The sending thread can block in connect/write. Once reading begins,
        # the reader itself can observe cancellation on its own profile handle.
        done.set()
        next_check = 0.0

        def is_interrupted():
            nonlocal next_check
            now = time.monotonic()
            if now >= next_check:
                check_cancel(profile)
                next_check = now + 0.1
            return interrupted.is_set()

        response = http.client.HTTPResponse(sock, *args, **kwargs)
        response.fp = io.BufferedReader(
            _ResponseReader(sock, response.fp, is_interrupted)
        )
        return response

    conn.response_class = response_factory

    def watch():
        p = Profile(profile.directory, read_only=True)
        try:
            while not done.wait(0.1):
                check_cancel(p)
                if interrupted.is_set():
                    sock = conn.sock
                    if sock is not None:
                        with contextlib.suppress(OSError):
                            sock.shutdown(socket.SHUT_RDWR)
                    return
        finally:
            p.db.close()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield conn, interrupted
    finally:
        done.set()
        watcher.join()
        conn.close()
        if profile.get("ai_cancel", token):
            raise Cancelled("generation cancelled")
        if interrupted.is_set():
            raise RuntimeError("generation deadline exceeded")


def frames(response):
    """Bounded SSE parser. A blank line dispatches all data lines in the event."""
    lines = []
    total = 0
    while True:
        raw = response.readline(262145)
        if not raw:
            return
        total += len(raw)
        require(total <= 2**21 and len(raw) <= 262144, "provider stream too large")
        line = raw.decode().rstrip("\r\n")
        if line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))
        elif not line and lines:
            yield "\n".join(lines)
            lines = []


def exchange(profile, token, route, content, reply):
    provider = route["provider"]
    anthropic = provider == "anthropic"
    headers = headers_for(profile, provider)
    headers["Accept"] = "text/event-stream" if content["stream"] else "application/json"
    _, path = wire.HOSTS[provider]
    with connection(profile, token, route) as (conn, interrupted):
        conn.connect()
        require(
            not interrupted.is_set() and not profile.get("ai_cancel", token),
            "provider interrupted",
        )
        conn.request(
            "POST", path, encode(wire.payload(route, content)).encode(), headers
        )
        with conn.getresponse() as response:
            if response.status != 200:
                raise Rejected(response.status, response.getheader("Retry-After"))
            accept = reply.anthropic if anthropic else reply.chat
            if content["stream"]:
                for frame in frames(response):
                    require(not interrupted.is_set(), "provider interrupted")
                    if frame == "[DONE]" and not anthropic:
                        break
                    event = json.loads(frame)
                    require(
                        isinstance(event, dict) and "error" not in event,
                        "provider stream error",
                    )
                    if anthropic and event.get("type") == "message_stop":
                        break
                    accept(event, True)
                else:
                    raise ValueError("provider stream ended early")
            else:
                raw = response.read(2**20 + 1)
                require(len(raw) <= 2**20, "provider response too large")
                accept(json.loads(raw), False)
    return reply.result(content)


def stream_filter(emit, secrets):
    """Retain possible credential prefixes until subsequent text disambiguates them."""
    pending = ""

    def send(text, final=False):
        nonlocal pending
        pending += text
        for secret in secrets:
            pending = pending.replace(secret, "[redacted]")
        hold = 0
        if not final:
            for secret in secrets:
                for length in range(min(len(secret) - 1, len(pending)), hold, -1):
                    if pending.endswith(secret[:length]):
                        hold = length
                        break
        cut = len(pending) - hold
        if cut and emit:
            emit(pending[:cut])
        pending = pending[cut:]

    return send


def execute(profile, token, content, approved_settings, emit=None):
    remaining = content["budget"]
    attempts = 0
    last_error = "no route available"
    deferred = set()
    for route in content["routes"]:
        provider = route["provider"]
        if (provider, route["port"]) in deferred:
            continue
        route_key = digest(approved_settings["ai"]["routes"][route["alias"]])
        for retry in range(content["retries"] + 1):
            if profile.get("ai_cancel", token):
                raise Cancelled("generation cancelled")
            require(
                profile.settings() == approved_settings, "provider settings changed"
            )
            require(
                remaining >= route["reservation"],
                "approved token reservation exhausted",
            )
            remaining -= route["reservation"]
            attempts += 1
            attempt_id = f"{token}/{attempts}"
            record = dict(
                request_id=token,
                route=route["alias"],
                provider=provider,
                model=route["model"],
                started_at=int(time.time()),
                reserved_tokens=route["reservation"],
                state="in_progress",
                usage={},
            )
            profile.put("ai_attempts", attempt_id, record)
            send = stream_filter(
                emit if content["stream"] else None, tuple(vault.values(profile))
            )
            reply = wire.Reply(send)
            retryable = False
            delay = 0.25 * (retry + 1)
            try:
                output = exchange(profile, token, route, content, reply)
                send("", final=True)
                record["state"] = "completed"
                return dict(
                    output,
                    provider=provider,
                    model=route["model"],
                    route=route["alias"],
                    attempts=attempts,
                )
            except Cancelled:
                record["state"] = "cancelled"
                raise
            except KeyboardInterrupt:
                record["state"] = "cancelled"
                raise Cancelled("generation interrupted") from None
            except Rejected as exc:
                last_error = str(exc)
                retryable = exc.status in {429, 503}
                if retryable and exc.retry_after is not None:
                    if (
                        exc.retry_after.isdecimal()
                        and len(exc.retry_after) <= 3
                        and int(exc.retry_after) <= 3
                    ):
                        delay = max(delay, int(exc.retry_after))
                    else:
                        # Defer other aliases of the same endpoint as well.
                        deferred.add((provider, route["port"]))
                        break
            except ConnectionRefusedError:
                last_error = "provider connection refused"
                retryable = True
            except (
                OSError,
                http.client.HTTPException,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OverflowError,
                RecursionError,
                RuntimeError,
            ):
                raise ValueError(
                    "provider response incomplete, invalid, or unavailable"
                ) from None
            finally:
                if record["state"] == "in_progress":
                    record["state"] = "failed"
                record["usage"] = reply.usage
                profile.put("ai_attempts", attempt_id, record)
                profile.put(
                    "ai_health",
                    route_key,
                    {"state": record["state"], "time": int(time.time())},
                )
            if not retryable:
                raise ValueError(last_error)
            if retry < content["retries"]:
                time.sleep(delay)
    raise ValueError(last_error)


def usage(profile):
    totals = {}
    for record in profile.all("ai_attempts").values():
        group = totals.setdefault(
            record["provider"],
            {"attempts": 0, "unreported_attempts": 0, "reported_tokens": {}},
        )
        group["attempts"] += 1
        if not record["usage"]:
            group["unreported_attempts"] += 1
        for key, count in record["usage"].items():
            group["reported_tokens"][key] = group["reported_tokens"].get(key, 0) + count
    return totals
