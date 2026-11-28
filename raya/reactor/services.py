"""Read Core state and route operator actions through the existing approval engine."""

import re

from .. import cloud, engine, vault
from ..core import decide, plan_run, require
from ..control import action
from ..goals import change

DEFAULTS = dict(fps=60, fullscreen=True, paused=False, city="Isparta", weather=False)
SENSITIVE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:sk-|AIza|ghp_|github_pat_)[A-Za-z0-9_-]{12,}"
    r"|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)[\s'\"]*[:=]\s*\S+)"
)


def protected(p, data):
    def sensitive(item):
        if isinstance(item, str):
            return bool(SENSITIVE.search(item))
        if isinstance(item, dict):
            return any(
                k.lower()
                in {
                    "password",
                    "api_key",
                    "access_token",
                    "refresh_token",
                    "client_secret",
                }
                or sensitive(k)
                or sensitive(v)
                for k, v in item.items()
            )
        return isinstance(item, list) and any(sensitive(v) for v in item)

    return sensitive(data) or any(
        vault.contains(data, value) for value in vault.values(p)
    )


def visible(p, data):
    return "[gizlendi]" if protected(p, data) else data


def prefs(p, **patch):
    current = {**DEFAULTS, **p.get("hud", "reactor", {})}
    if patch:
        require(
            patch.keys() <= DEFAULTS.keys() and not protected(p, patch),
            "Geçersiz HUD ayarı",
        )
        for k, v in patch.items():
            require(
                (
                    type(v) is bool
                    if k in {"fullscreen", "paused", "weather"}
                    else v in (30, 60)
                    if k == "fps"
                    else isinstance(v, str) and v.isprintable() and 0 < len(v) <= 60
                ),
                "Geçersiz HUD ayarı",
            )
        with p.transaction():
            current = {**DEFAULTS, **p.get("hud", "reactor", {}), **patch}
            p.put("hud", "reactor", current)
    return current


def snapshot(p):
    cfg = engine.config(p)
    quotas = p.all("cloud_quota")
    statuses = engine.status(p)["routes"]
    routes = {}
    for alias, r in cfg["routes"].items():
        catalog = p.get("cloud_catalog", alias, {})
        ready = (
            statuses[alias]["state"] == "configured"
            and not cfg["offline"]
            and cloud.strict(p)
            and cloud.eligible(
                p, alias, r, {"max_output_tokens": cfg["max_output_tokens"]}
            )
            and cloud.capacity(p, r, cfg["max_output_tokens"])
        )
        routes[alias] = dict(
            model=r["model"],
            provider=r["provider"],
            price=cloud.classification(p, alias, r),
            state="Onay gerekli"
            if ready
            else "Katalog hazır"
            if catalog.get("state") == "ready"
            else catalog.get("reason")
            or {"offline": "Çevrimdışı", "missing_key": "Anahtar eksik"}.get(
                statuses[alias]["state"], "Keşif bekleniyor"
            ),
            model_count=len(catalog.get("models", [])),
            access=statuses[alias]["state"],
        )
    # Use the existing SQLite connection for read-only projections. Rendering a
    # timeline must not load prompts, audio receipts or generated patches.
    requests = {
        ident: dict(operation=op, state=state)
        for ident, op, state in p.db.execute(
            "SELECT name,json_extract(value,'$.operation'),json_extract(value,'$.state') "
            "FROM objects WHERE kind='requests' ORDER BY rowid DESC LIMIT 200"
        )
    }
    counts = dict(
        p.db.execute(
            "SELECT json_extract(value,'$.state'),count(*) "
            "FROM objects WHERE kind='requests' GROUP BY 1"
        )
    )
    tasks = {
        i: dict(kind=k, phase=phase)
        for i, k, phase in p.db.execute(
            "SELECT name,json_extract(value,'$.kind'),json_extract(value,'$.phase') "
            "FROM objects WHERE kind='dev_tasks' ORDER BY rowid DESC"
        )
    }
    skills = {i: r["manifest"] for i, r in p.all("skills").items()}
    goals = {
        i: dict(title=g["title"], state=g["state"]) for i, g in p.all("goals").items()
    }
    quota = "Bilinmiyor"
    for sample in sorted(
        p.all("cloud_headers").values(),
        key=lambda sample: sample.get("time", 0),
        reverse=True,
    ):
        headers = sample.get("headers", {})
        for unit, tr in (("tokens", "token"), ("requests", "istek")):
            remaining = headers.get("x-ratelimit-remaining-" + unit)
            if remaining is not None:
                quota = f"{remaining} {tr} (gözlem)"
                break
        if quota != "Bilinmiyor":
            break
    if quota == "Bilinmiyor":
        for observed in quotas.values():
            if observed.get("derived_remaining_usd") is not None:
                quota = str(observed["derived_remaining_usd"]) + " USD (gözlem)"
                break
    tested = p.db.execute(
        "SELECT count(*) FROM objects WHERE kind='dev_tasks' AND json_extract(value,'$.metrics.passed_cases')>0"
    ).fetchone()[0]
    data = dict(
        quota=quota,
        sandbox=f"{tested} test kaydı" if tested else "Test kaydı yok",
        routes=routes,
        skills=skills,
        goals=goals,
        requests=requests,
        tasks=tasks,
        counts=counts,
        strict=cloud.strict(p),
        offline=cfg["offline"],
        quotas=quotas,
        usage=engine.usage(p),
    )
    # Redact individual dynamic fields, preserving the shape of each view.
    secrets = tuple(vault.values(p))

    def clean(v):
        if isinstance(v, str):
            return (
                "[gizlendi]"
                if any(s in v for s in secrets) or SENSITIVE.search(v)
                else v
            )
        if isinstance(v, dict):
            return {clean(k): clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    return clean(data)


def command(p, text):
    require(
        isinstance(text, str) and 0 < len(text) <= 8192 and not protected(p, text),
        "Boş, uzun veya korunan komut",
    )
    cmd, _, args = text.strip().partition(" ")
    if cmd in ("/yardım", "/help"):
        return "/görevler · /yetenekler · /çalıştır sağlayıcı/yetenek girdi · /akış istem · düz metin: onaylı AI"
    if cmd in ("/görevler", "/yetenekler"):
        data = snapshot(p)
        return (
            data["skills"]
            if cmd == "/yetenekler"
            else {k: data[k] for k in ("goals", "tasks", "requests")}
        )
    if cmd == "/çalıştır":
        key, _, argument = args.partition(" ")
        return plan_run(p, key, argument)
    require(
        cmd == "/akış" or not cmd.startswith("/"), "Bilinmeyen komut. /yardım yazın."
    )
    require(
        cloud.strict(p),
        "AI Merkezi üzerinden sıfır maliyet politikasını etkinleştirin.",
    )
    try:
        return engine.prepare(
            p, {"prompt": args if cmd == "/akış" else text, "stream": cmd == "/akış"}
        )
    except ValueError as exc:
        raise ValueError(
            f"AI Merkezi: rota, keşif, kota ve ücretsiz kullanım kanıtını kontrol edin. {exc}"
        ) from exc


def review(p, ident, fingerprint, allow, emit=None):
    req = p.get("requests", ident)
    require(req and req["digest"] == fingerprint, "İstek değişti")
    if allow:
        require(not protected(p, req), "Korunan istek")
        require(
            req["operation"]
            in {"run", "install", "remove", "dev-commit", "ai-generate"},
            "Bu işlem için özel Core onay arayüzünü kullanın.",
        )
        if req["operation"] == "ai-generate":
            require(
                cloud.strict(p) and req["content"].get("zero_cost"),
                "Sıfır maliyet onayı gerekli",
            )
    return decide(p, ident, fingerprint, allow, emit)


def cancel(p, ident):
    req = p.get("requests", ident)
    if req and req["operation"] == "ai-generate":
        return engine.cancel(p, ident)
    if req:
        return review(p, ident, req["digest"], False)
    if p.get("dev_tasks", ident):
        return action(p, "dev-cancel", {"id": ident})
    return change(p, "goal-state", {"goal": ident, "state": "cancelled"})
