"""Optional text generation using a user-started local llama.cpp server."""

import http.client
import json

from .core import digest, encode, propose, require, text_field


def destination(settings):
    return f"http://127.0.0.1:{settings.get('provider_port', 8080)}/completion"


def request(profile, args):
    if profile.settings().get("provider") == "engine":
        from .engine import prepare

        return prepare(profile, args)
    prompt = text_field(args["prompt"], "prompt", 8192)
    require(
        profile.settings().get("provider") == "local-llama", "local provider disabled"
    )
    return propose(
        profile,
        "generate",
        destination(profile.settings()),
        {"prompt": prompt, "n_predict": 256, "stream": False},
        digest(profile.settings()),
        ["loopback.http"],
    )


def generate(payload, port=8080):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request(
            "POST",
            "/completion",
            encode(payload).encode(),
            {"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        require(response.status == 200, f"provider returned HTTP {response.status}")
        data = response.read(262145)
        require(len(data) <= 262144, "provider output too large")
        output = json.loads(data)
        require(isinstance(output, dict), "invalid provider output")
        return text_field(output.get("content"), "provider output", 65536)
    except http.client.HTTPException as exc:
        raise ValueError("local provider protocol error") from exc
    finally:
        conn.close()


def available(profile):
    if profile.settings().get("provider") == "engine":
        from .engine import status

        return (
            "configured"
            if any(
                r["state"] == "configured" for r in status(profile)["routes"].values()
            )
            else "unavailable"
        )
    if profile.settings().get("provider") != "local-llama":
        return "unconfigured"
    conn = http.client.HTTPConnection(
        "127.0.0.1", profile.settings().get("provider_port", 8080), timeout=2
    )
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return "unavailable"
        body = response.read(4097)
        if len(body) > 4096:
            return "unavailable"
        status = json.loads(body)
        return (
            "ready"
            if isinstance(status, dict) and status.get("status") == "ok"
            else "unavailable"
        )
    except (ValueError, OSError, http.client.HTTPException):
        return "unavailable"
    finally:
        conn.close()
