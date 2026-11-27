"""Authenticated discovery, charge classification and shared admission control."""

import calendar
import contextlib
import datetime as dt
import hashlib
import http.client
import json
import re
import time
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation

from . import ai_protocol as wire, vault
from .core import digest, identifier, require

NEW = {"chatgpt", "omniroute", "sambanova", "nim", "cloudflare", "mistral", "cohere"}
WINDOWS = {"minute": 60, "day": 86400, "month": None}
PRICE_CLASSES = {"verified_free", "subscription", "grant", "paid", "unknown"}
FRESH = 300


def enable(p):
    if p.settings().get("zero_cost") is not True:
        p.put("profile", "preferences", dict(p.settings(), zero_cost=True))
    return {"strict_zero_cost": True}


def strict(p):
    settings = p.settings()
    return (
        settings.get("zero_cost") is True or settings.get("billing_policy") != "legacy"
    )


def binding(route):
    from .engine import port

    return digest(
        dict(
            port=port(route),
            **{
                k: route.get(k)
                for k in (
                    "provider",
                    "account",
                    "project",
                    "model",
                    "pool",
                    "upstream",
                    "responses_url",
                    "credential",
                )
            },
        )
    )


def identity(route):
    provider = route["provider"]
    account = route.get("account", "default")
    project = route.get("project", "default")
    return dict(
        provider=provider,
        account=account,
        project=project,
        model=route["model"],
        pool=route.get("pool")
        or f"{route.get('upstream', provider)}/{account}/{project}",
    )


def address(route):
    provider = route["provider"]
    host, path = wire.HOSTS[provider]
    if provider == "chatgpt":
        from .chatgpt_auth import RESPONSES
        from urllib.parse import urlsplit

        endpoint = route.get("responses_url")
        require(
            endpoint in RESPONSES,
            "independently registered Responses endpoint required",
        )
        parsed = urlsplit(endpoint)
        return parsed.netloc, parsed.path
    if provider == "cloudflare":
        account = route.get("account", "")
        require(re.fullmatch("[0-9a-f]{32}", account), "Cloudflare account ID required")
        path = path.replace("{account}", account)
    return host, path


def key_name(route):
    provider = route["provider"]
    return (
        "ai-"
        + provider
        + ("-" + route["credential"] if route.get("credential") else "")
    )


def credential_stamp(p, route):
    key = vault.read_secret(p, key_name(route))
    return hashlib.sha256(key.encode()).hexdigest() if key else None


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n >= 0 else None
    except InvalidOperation:
        return None


def get_json(p, route, path):
    from . import engine

    provider = route["provider"]
    require(
        provider in wire.LOCAL or not engine.config(p)["offline"],
        "offline mode blocks discovery",
    )
    host, _ = address(route)
    local = provider in wire.LOCAL or provider == "omniroute"
    factory = http.client.HTTPConnection if local else http.client.HTTPSConnection
    conn = factory(host, engine.port(route), timeout=8)
    try:
        conn.request("GET", path, headers=engine.headers_for(p, provider, route))
        with conn.getresponse() as response:
            observe(p, route, response)
            require(
                response.status == 200,
                f"catalog service returned HTTP {response.status}",
            )
            raw = response.read(4 * 1024 * 1024 + 1)
            require(len(raw) <= 4 * 1024 * 1024, "catalog exceeds size limit")
            return json.loads(raw), response.getheader("X-OmniRoute-Version")
    finally:
        conn.close()


def google_unbilled(p, route):
    """Read provider-controlled key ownership and current project billing status."""
    admin = vault.read_secret(p, "google-billing-read")
    if not admin:
        return None
    key = vault.read_secret(p, key_name(route))
    require(
        key and admin.isascii() and admin.isprintable(),
        "invalid Google read credential",
    )

    def read(host, path):
        conn = http.client.HTTPSConnection(host, timeout=8)
        try:
            conn.request("GET", path, headers={"Authorization": "Bearer " + admin})
            with conn.getresponse() as res:
                require(
                    res.status == 200, "Google account billing evidence unavailable"
                )
                raw = res.read(65537)
                require(len(raw) <= 65536, "billing response too large")
                return json.loads(raw)
        finally:
            conn.close()

    lookup = read(
        "apikeys.googleapis.com", "/v2/keys:lookupKey?" + urlencode({"keyString": key})
    )
    match = re.fullmatch(
        r"projects/([0-9]+)/locations/global", str(lookup.get("parent", ""))
    )
    require(match and route.get("project") == match[1], "Gemini key/project mismatch")
    billing = read(
        "cloudbilling.googleapis.com", "/v1/projects/" + match[1] + "/billingInfo"
    )
    return {
        "unbilled": billing.get("billingEnabled") is False
        and not billing.get("billingAccountName"),
        "source": "Cloud Billing projects.getBillingInfo + API Keys lookupKey",
        "project": match[1],
    }


def discover(p, alias):
    try:
        return _discover(p, alias)
    except (ValueError, OSError, KeyError, TypeError, http.client.HTTPException):
        record = {
            "state": "failed",
            "time": int(time.time()),
            "models": [],
            "reason": "account, endpoint or protocol evidence unavailable",
        }
        p.put("cloud_catalog", alias, record)
        return record


def _discover(p, alias):
    from . import engine

    route = engine.config(p)["routes"].get(alias)
    require(route, "unknown route")
    provider = route["provider"]
    _, chat = address(route)
    path = chat.removesuffix("/chat/completions") + "/models"
    require(
        provider != "chatgpt", "no verified independent account model-list contract"
    )
    if provider == "anthropic":
        path = "/v1/models"
    if provider == "cloudflare":
        path = f"/client/v4/accounts/{route['account']}/ai/models/search"
    if provider == "cohere":
        path = "/v1/models"
    now = int(time.time())
    # Invalidate earlier eligibility even when this refresh fails.
    p.put("cloud_catalog", alias, {"time": now, "state": "failed", "models": []})
    response, version = get_json(p, route, path)
    if provider == "omniroute":
        require(
            isinstance(version, str) and re.fullmatch(r"3\.8\.\d+", version),
            "unverified OmniRoute protocol version",
        )
    items = response.get(
        "result"
        if provider == "cloudflare"
        else "models"
        if provider == "cohere"
        else "data"
    )
    require(isinstance(items, list), "invalid model catalog")
    models = []
    protected = tuple(vault.values(p))
    for item in items[:4096]:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("id")
            if provider not in ("cohere", "cloudflare")
            else item.get("name")
        )
        if not isinstance(name, str) or not name.isprintable() or len(name) > 256:
            continue
        if any(secret in name for secret in protected):
            continue
        prices = item.get("pricing")
        free = (
            provider == "openrouter"
            and name.endswith(":free")
            and isinstance(prices, dict)
            and {"prompt", "completion"} <= prices.keys()
            and all(number(v) == 0 for v in prices.values())
        )
        model = {"id": name, "verified_zero_prices": free}
        if provider == "openrouter" and isinstance(prices, dict):
            model["prices_usd"] = {
                k: str(number(v))
                for k, v in prices.items()
                if k
                in (
                    "prompt",
                    "completion",
                    "request",
                    "internal_reasoning",
                    "web_search",
                )
                and number(v) is not None
            }
        for key in ("context_length", "max_model_len", "max_tokens"):
            if type(item.get(key)) is int:
                model[key] = item[key]
        top = item.get("top_provider", {})
        if isinstance(top, dict) and type(top.get("max_completion_tokens")) is int:
            model["max_output_tokens"] = top["max_completion_tokens"]
        params = item.get("supported_parameters")
        if isinstance(params, list):
            model["parameters"] = [
                x
                for x in params
                if x
                in (
                    "tools",
                    "response_format",
                    "structured_outputs",
                    "reasoning",
                    "max_tokens",
                )
            ]
        models.append(model)
    record = {
        "state": "ready",
        "time": now,
        "path": path,
        "version": version,
        "models": models,
        "route": binding(route),
        "key_binding": credential_stamp(p, route),
    }
    if not any(m["id"] == route["model"] for m in models) and provider != "omniroute":
        record["configured_model_missing"] = True
    if provider == "gemini":
        record["billing"] = google_unbilled(p, route)
    if provider == "openrouter":
        payload, _ = get_json(p, route, "/api/v1/key")
        data = payload.get("data") or {}
        record["key_source"] = "/api/v1/key"
        record["key"] = {
            k: data[k]
            for k in ("is_free_tier", "limit", "limit_remaining", "usage")
            if k in data and (type(data[k]) is bool or number(data[k]) is not None)
        }
    p.put("cloud_catalog", alias, record)
    p.put(
        "ai_health",
        digest(route),
        {
            "state": "ready",
            "time": now,
            "model_listed": any(m["id"] == route["model"] for m in models),
        },
    )
    return public_catalog(record)


def public_catalog(record):
    return {k: v for k, v in record.items() if k != "key_binding"}


def classification(p, alias, route):
    if p.get("cloud_quarantine", binding(route)):
        return "paid"
    if route["provider"] in wire.LOCAL:
        return "local"
    record = p.get("cloud_catalog", alias, {})
    if (
        record.get("state") == "ready"
        and 0 <= time.time() - record.get("time", 0) <= FRESH
        and record.get("route") == binding(route)
        and record.get("key_binding") == credential_stamp(p, route)
    ):
        model = next((m for m in record["models"] if m["id"] == route["model"]), {})
        if route["provider"] == "openrouter" and any(
            number(v) > 0 for v in model.get("prices_usd", {}).values()
        ):
            return "paid"
        if (
            route["provider"] == "gemini"
            and model
            and (record.get("billing") or {}).get("unbilled") is True
        ):
            return "verified_free"
        if (
            route["provider"] == "openrouter"
            and model.get("verified_zero_prices")
            and record.get("key", {}).get("is_free_tier") is True
        ):
            return "verified_free"
    return "unknown"


def eligible(p, alias, route, content):
    if content.get("tools") not in (None, [], [TOOL]) or content.get("tool_results"):
        return False
    if classification(p, alias, route) != "verified_free":
        return False
    last = p.get("ai_health", digest(route), {})
    if last.get("state") not in ("ready", "completed"):
        return False
    record = p.get("cloud_catalog", alias, {})
    model = next((m for m in record.get("models", []) if m["id"] == route["model"]), {})
    return content["max_output_tokens"] <= model.get("max_output_tokens", 2**31)


def limit(p, args):
    pool = args.get("pool")
    scope = args.get("scope", "pool")
    require(
        scope in ("provider", "account", "project", "model", "pool"),
        "invalid quota scope",
    )
    require(
        isinstance(pool, str) and re.fullmatch(r"[\w./:-]{1,320}", pool),
        "invalid quota group",
    )
    if scope != "pool":
        pool = "@" + scope + ":" + pool
    limits = args.get("limits")
    valid = {a + "_" + b for a in ("requests", "tokens") for b in WINDOWS}
    require(
        isinstance(limits, dict)
        and limits.keys() <= valid
        and all(type(v) is int and 0 <= v <= 10**12 for v in limits.values()),
        "invalid manual ceilings",
    )
    record = {"limits": limits, "source": "operator ceiling", "time": int(time.time())}
    p.put("cloud_limits", pool, record)
    return record


def window_start(name, now):
    if name == "month":
        date = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        return calendar.timegm((date.year, date.month, 1, 0, 0, 0))
    return now - WINDOWS[name]


def group_keys(d):
    return (
        d["pool"],
        "@provider:" + d["provider"],
        "@account:" + d["provider"] + "/" + d["account"],
        "@project:" + d["provider"] + "/" + d["account"] + "/" + d["project"],
        "@model:" + d["provider"] + "/" + d["model"],
    )


def pool_view(p, pool, now):
    rows = [
        r
        for r in p.all("cloud_reservations").values()
        if pool in group_keys(r)
        and r["time"] >= min(window_start(k, now) for k in WINDOWS)
    ]
    result = {}
    for key, ceiling in p.get("cloud_limits", pool, {}).get("limits", {}).items():
        unit, w = key.split("_")
        active = [r for r in rows if r["time"] >= window_start(w, now)]
        used = sum(1 if unit == "requests" else r["tokens"] for r in active)
        result[key] = {
            "limit": ceiling,
            "used": used,
            "remaining_local": max(0, ceiling - used),
        }
    return result


def reserve(p, alias, route, content, amount, attempt):
    # Caller has already durably consumed the approval. Serialize quota admission
    # separately so competing processes see the hold before either sends bytes.
    with p.transaction():
        require(
            eligible(p, alias, route, content),
            "zero-cost eligibility expired or unavailable",
        )
        if route["provider"] == "gemini":
            require(
                (google_unbilled(p, route) or {}).get("unbilled") is True,
                "Gemini billing changed",
            )
        require(capacity(p, route, amount), "shared quota exhausted")
        dims = identity(route)
        pool = dims["pool"]
        now = time.time()
        sample = p.get("cloud_headers", pool, {})
        require(now >= sample.get("cooldown_until", 0), "shared pool in cooldown")
        require(not p.get("cloud_reservations", attempt), "duplicate attempt")
        p.put(
            "cloud_reservations",
            attempt,
            dict(
                dims,
                binding=binding(route),
                time=now,
                tokens=amount,
                reserved=amount,
                state="reserved",
            ),
        )


def settle(p, attempt, record):
    with p.transaction():
        row = p.get("cloud_reservations", attempt)
        if not row:
            return
        usage = record["usage"]
        if {"input_tokens", "output_tokens"} <= usage.keys():
            row["tokens"] = (
                usage["input_tokens"]
                + usage["output_tokens"]
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
        row.update(
            state=record["state"],
            finished_at=time.time(),
            usage=usage,
            latency_ms=record["latency_ms"],
            reported_cost_usd=record.get("reported_cost_usd"),
        )
        p.put("cloud_reservations", attempt, row)
        if record.get("reported_cost_usd") and record["reported_cost_usd"] > 0:
            p.put(
                "cloud_quarantine",
                row["binding"],
                {
                    "reason": "nonzero provider-reported charge",
                    "time": int(time.time()),
                },
            )


def observe(p, route, response):
    pool = identity(route)["pool"]
    now = time.time()
    headers = {}
    for field in ("limit", "remaining", "reset"):
        for unit in ("requests", "tokens"):
            name = "x-ratelimit-" + field + "-" + unit
            v = response.getheader(name)
            if (
                v
                and len(v) <= 48
                and (
                    v.isdecimal() or field == "reset" and reset_time(v, now) is not None
                )
            ):
                headers[name] = v
    delay = response.getheader("Retry-After")
    if headers or response.status == 429:
        with contextlib.nullcontext() if p.db.in_transaction else p.transaction():
            old = p.get("cloud_headers", pool, {})
            cooldown = old.get("cooldown_until", 0)
            if response.status == 429:
                from email.utils import parsedate_to_datetime

                try:
                    reset = (
                        now + int(delay)
                        if delay and delay.isdecimal()
                        else parsedate_to_datetime(delay).timestamp()
                    )
                except (ValueError, TypeError, OverflowError, AttributeError):
                    reset = now + 60
                cooldown = max(cooldown, reset)
            times = dict(old.get("observed_at", {}))
            combined = dict(old.get("headers", {}))
            for name in combined:
                times.setdefault(name, old["time"])
            for name in headers:
                times[name] = now
            # A new remaining count without a reset must not inherit an old expiry.
            for unit in ("tokens", "requests"):
                reset = "x-ratelimit-reset-" + unit
                if "x-ratelimit-remaining-" + unit in headers and reset not in headers:
                    combined.pop(reset, None)
            combined.update(headers)
            p.put(
                "cloud_headers",
                pool,
                {
                    "time": now,
                    "source": "provider response headers",
                    "provider": route["provider"],
                    "headers": combined,
                    "observed_at": times,
                    "cooldown_until": cooldown,
                },
            )


def report(p):
    from . import engine

    routes = engine.config(p)["routes"]
    now = time.time()
    snapshots = p.all("cloud_headers")
    rankings = {}
    for kind in ("feature", "repair", "optimize", "reasoning", "tools"):
        rankings[kind] = [
            {
                "route": a,
                "evidence": p.get(
                    "cloud_evaluations", digest([binding(routes[a]), kind])
                ),
            }
            for a in sorted(routes, key=lambda name: rank(p, routes[name], kind))
            if eligible(p, a, routes[a], {"max_output_tokens": 1})
            and capacity(p, routes[a], 1)
        ]
    return {
        "strict": strict(p),
        "rankings": rankings,
        "quota_sources": p.all("cloud_quota"),
        "time": int(now),
        "routes": {
            a: dict(
                identity(r),
                price_class=classification(p, a, r),
                context_limit=r.get("context_tokens", 32768),
                context_source="configured",
                key_quota=p.get("cloud_catalog", a, {}).get("key"),
                catalog_observed_at=p.get("cloud_catalog", a, {}).get("time"),
                health=p.get("ai_health", digest(r)),
            )
            for a, r in routes.items()
        },
        "pools": {
            pool: {
                "manual_windows": pool_view(p, pool, now),
                "provider_snapshot": snapshots.get(pool),
                "manual_observation": p.get("cloud_annotations", pool),
                "external_remaining": "UNKNOWN",
                "compute_units": None,
                "subscription_window": None,
            }
            for pool in sorted(
                {identity(r)["pool"] for r in routes.values()}
                | p.all("cloud_limits").keys()
            )
        },
        "history": p.all("cloud_reservations"),
        "by_dimension": dimensions(p),
        "evaluations": p.all("cloud_evaluations"),
        "tasks": {
            k: {"phase": v["phase"], "kind": v["kind"]}
            for k, v in p.all("dev_tasks").items()
        },
    }


def reset_time(value, observed):
    if not value:
        return None
    if value.isdecimal():
        n = int(value)
        return n if n >= 1000000000 else observed + n
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if parts and "".join(a + b for a, b in parts) == value:
        return observed + sum(
            float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[u] for n, u in parts
        )
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def capacity(p, route, amount):
    pool = identity(route)["pool"]
    now = time.time()
    snapshot = p.get("cloud_headers", pool, {})
    if now < snapshot.get("cooldown_until", 0):
        return False
    headers = snapshot.get("headers", {})
    for unit in ("requests", "tokens"):
        remaining_name = "x-ratelimit-remaining-" + unit
        reset_name = "x-ratelimit-reset-" + unit
        times = snapshot.get("observed_at", {})
        observed = times.get(remaining_name, snapshot.get("time", now))
        remaining = headers.get(remaining_name)
        reset = reset_time(
            headers.get(reset_name), times.get(reset_name, snapshot.get("time", now))
        )
        if remaining and remaining.isdecimal() and (reset is None or now < reset):
            holds = [
                r
                for r in p.all("cloud_reservations").values()
                if r["pool"] == pool
                and (
                    r.get("finished_at", r["time"]) >= observed
                    or r["state"] == "reserved"
                )
            ]
            used = sum(1 if unit == "requests" else r["tokens"] for r in holds)
            if int(remaining) - used < (1 if unit == "requests" else amount):
                return False
    return all(
        row["remaining_local"] >= (1 if k.startswith("requests_") else amount)
        for group in group_keys(identity(route))
        if p.get("cloud_limits", group)
        for k, row in pool_view(p, group, now).items()
    )


def headroom(p, route):
    windows = pool_view(p, identity(route)["pool"], time.time())
    return min(
        (r["remaining_local"] / max(1, r["limit"]) for r in windows.values()), default=0
    )


def rank(p, route, kind):
    row = p.get("cloud_evaluations", digest([binding(route), kind]), {})
    n = row.get("runs", 0)
    return (
        -row.get("passes", 0) / n if n else 0,
        -headroom(p, route),
        row.get("tokens_per_pass", float("inf")),
        row.get("latency_ms") if row.get("latency_ms") is not None else float("inf"),
    )


def evaluation(p, route, kind, passed, usage, latency):
    key = digest([binding(route), kind])
    row = p.get(
        "cloud_evaluations",
        key,
        {
            "kind": kind,
            "model": route["model"],
            "runs": 0,
            "passes": 0,
            "tokens": 0,
            "unknown_usage": 0,
        },
    )
    row["runs"] += 1
    row["passes"] += int(passed)
    if {"input_tokens", "output_tokens"} <= usage.keys():
        row["tokens"] += usage["input_tokens"] + usage["output_tokens"]
    else:
        row["unknown_usage"] += 1
    if row["passes"] and not row["unknown_usage"]:
        row["tokens_per_pass"] = row["tokens"] / row["passes"]
    row.update(
        latency_ms=latency, time=int(time.time()), source="local acceptance harness"
    )
    p.put("cloud_evaluations", key, row)


def annotate(p, args):
    pool = text_field_safe(args.get("pool"))
    evidence = args.get("classification", "unknown")
    require(evidence in PRICE_CLASSES, "unknown pricing classification")
    counters = args.get("counters", {})
    require(
        isinstance(counters, dict)
        and counters.keys()
        <= {
            "credits_remaining",
            "compute_units_remaining",
            "subscription_remaining",
            "reset_at",
            "spent_usd",
        }
        and all(number(v) is not None for v in counters.values()),
        "invalid manual quota observation",
    )
    record = {
        "source": "manual",
        "reference": text_field_safe(args.get("reference")),
        "time": int(time.time()),
        "billing_class": evidence,
        "counters": counters,
    }
    p.put("cloud_annotations", pool, record)
    return record


def text_field_safe(value):
    require(
        isinstance(value, str) and value.isprintable() and 0 < len(value) <= 256,
        "invalid observation label",
    )
    return value


def dimensions(p):
    groups = {k: {} for k in ("provider", "account", "project", "model", "pool")}
    for r in p.all("cloud_reservations").values():
        for k in groups:
            path = {
                "provider": [r["provider"]],
                "account": [r["provider"], r["account"]],
                "project": [r["provider"], r["account"], r["project"]],
                "model": [r["provider"], r["model"]],
                "pool": [r["pool"]],
            }[k]
            group = groups[k].setdefault(
                "/".join(path),
                {
                    "attempts": 0,
                    "errors": 0,
                    "usage": {},
                    "cost_reported_usd": 0,
                    "unknown_cost": 0,
                },
            )
            group["attempts"] += 1
            group["errors"] += int(r["state"] == "failed")
            for field, value in r.get("usage", {}).items():
                group["usage"][field] = group["usage"].get(field, 0) + value
            if r.get("reported_cost_usd") is None:
                group["unknown_cost"] += 1
            else:
                group["cost_reported_usd"] += r["reported_cost_usd"]
    return groups


def check_pools(routes):
    seen = {}
    for r in routes.values():
        upstream = r.get("upstream", r["provider"])
        key = (upstream, r.get("account", "default"), r.get("project", "default"))
        pool = r.get("pool") or "/".join(key)
        require(
            seen.setdefault(key, pool) == pool,
            "conflicting shared pools for the same upstream account/project",
        )


TOOL = {
    "name": "choose_max",
    "description": "Return the largest supplied integer.",
    "parameters": {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    },
}


def eval_request(p, args):
    from . import engine

    enable(p)
    kind = args.get("kind")
    require(kind in ("reasoning", "tools"), "choose reasoning or tools")
    options = (
        {"tools": [TOOL], "prompt": "Call choose_max with the largest of -4, 17 and 8."}
        if kind == "tools"
        else {
            "prompt": "Return just the integer: starting with 3, double and subtract 1 three times."
        }
    )
    req = engine.prepare(
        p, dict(options, route=args.get("route", "auto"), task_kind=kind)
    )
    p.put("eval_requests", req["id"], kind)
    return req


def eval_score(p, ident):
    kind = p.get("eval_requests", ident)
    req = p.get("requests", ident)
    require(
        kind in ("tools", "reasoning") and req and req["state"] == "completed",
        "completed evaluation required",
    )
    output = req["result"]
    r = next(
        route for route in req["content"]["routes"] if route["alias"] == output["route"]
    )
    calls = output.get("tool_calls", [])
    passed = (
        output["text"].strip() == "17"
        if kind == "reasoning"
        else len(calls) == 1
        and calls[0]["name"] == "choose_max"
        and calls[0]["arguments"] == {"value": 17}
    )
    attempt = p.get("ai_attempts", f"{ident}/{output['attempts']}")
    evaluation(p, r, kind, passed, output["usage"], attempt["latency_ms"])
    p.drop("eval_requests", ident)
    return {"passed": passed, "model": r["model"], "kind": kind, "request": ident}


def quota_snapshot(p, args):
    from . import engine

    alias = args["route"]
    route = engine.config(p)["routes"].get(alias)
    require(route, "unknown quota route")
    provider = route["provider"]
    snapshot = {"time": int(time.time()), "state": "UNKNOWN", "provider": provider}
    try:
        if provider == "openrouter":
            path = "/api/v1/credits"
            data, _ = get_json(p, route, path)
            balance = data["data"]
            total, used = (
                number(balance.get(k)) for k in ("total_credits", "total_usage")
            )
            require(total is not None and used is not None, "unknown balance")
            snapshot.update(
                source=path,
                total_credits_usd=str(total),
                total_usage_usd=str(used),
                derived_remaining_usd=str(total - used),
                state="observed",
            )
        elif provider == "omniroute":
            key_id = identifier(args.get("key_id"), "OmniRoute management key ID")
            record = p.get("cloud_catalog", alias, {})
            require(
                record.get("state") == "ready"
                and record.get("route") == binding(route),
                "discover gateway version first",
            )
            path = "/api/usage/token-limits"
            data, version = get_json(
                p, route, path + "?" + urlencode({"apiKeyId": key_id})
            )
            require(version == record.get("version"), "gateway version changed")
            rows = data.get("limits") if isinstance(data, dict) else data
            require(isinstance(rows, list), "unsupported quota schema")
            safe = []
            for row in rows[:256]:
                entry = {
                    k: row[k]
                    for k in ("tokensUsed", "remaining", "tokenLimit")
                    if number(row.get(k)) is not None
                }
                for k in ("windowStart", "periodStartAt", "nextResetAt"):
                    v = row.get(k)
                    if (
                        isinstance(v, str)
                        and reset_time(v, snapshot["time"]) is not None
                    ):
                        entry[k] = v
                safe.append(entry)
            snapshot.update(source=path, windows=safe, state="observed")
        else:
            headers = p.get("cloud_headers", identity(route)["pool"])
            if headers:
                snapshot.update(
                    state="observed",
                    source="cached response headers",
                    observation=headers,
                )
    except (ValueError, OSError, TypeError, KeyError, http.client.HTTPException):
        snapshot["reason"] = "provider did not supply verifiable quota information"
    p.put("cloud_quota", alias, snapshot)
    return snapshot
