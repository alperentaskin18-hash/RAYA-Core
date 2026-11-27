"""Owner-registered public OAuth client. No Codex client ID or shared sessions."""

import base64
import hashlib
import http.client
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from . import vault
from .core import identifier, require, text_field
from .storage import child

ISSUER = "https://auth.openai.com"
# Only an origin/path explicitly granted to this independent client can be used.
RESPONSES = {
    "https://api.openai.com/v1/responses",
    "https://chatgpt.com/backend-api/codex/responses",
}


def official(url):
    u = urlsplit(url)
    require(
        u.scheme == "https"
        and u.netloc == "auth.openai.com"
        and not u.fragment
        and not u.query,
        "unsupported OAuth endpoint",
    )
    return u


def oauth_request(url, form=None):
    u = official(url)
    conn = http.client.HTTPSConnection(u.hostname, timeout=10)
    try:
        conn.request(
            "GET" if form is None else "POST",
            u.path,
            None if form is None else urlencode(form).encode(),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        with conn.getresponse() as res:
            require(
                res.status == 200, f"authorization service returned HTTP {res.status}"
            )
            raw = res.read(65537)
            require(len(raw) <= 65536, "oversized authorization response")
            return json.loads(raw) if raw else {}
    finally:
        conn.close()


def registration(p, args):
    require(
        args.get("distribution") == "open-source" and args.get("paid_product") is False,
        "ChatGPT integration requires open-source distribution",
    )
    require(
        args.get("owner_confirms_registration") is True,
        "independent client registration required",
    )
    client = text_field(args.get("client_id"), "assigned public client ID", 200)
    require(client.isascii() and client.isprintable(), "invalid client ID")
    audience = args.get("responses_url")
    require(audience in RESPONSES, "unsupported registered Responses destination")
    scopes = args.get("scopes")
    require(
        isinstance(scopes, list)
        and scopes
        and all(
            isinstance(s, str)
            and s.isascii()
            and s.isprintable()
            and not any(c.isspace() for c in s)
            for s in scopes
        ),
        "registered scopes required",
    )
    port = args.get("port")
    require(
        type(port) is int and 1024 <= port <= 65535,
        "registered loopback redirect port required",
    )
    cfg = dict(
        client_id=client,
        scopes=sorted(set(scopes)),
        port=port,
        responses_url=audience,
        distribution="open-source",
        paid_product=False,
        registration_reference=text_field(
            args.get("reference"), "registration reference", 256
        ),
    )
    p.put("oauth", "registration", cfg)
    return cfg


def metadata():
    data = oauth_request(ISSUER + "/.well-known/openid-configuration")
    require(
        data.get("issuer", "").rstrip("/") == ISSUER
        and "S256" in data.get("code_challenge_methods_supported", []),
        "issuer or PKCE support unverified",
    )
    for field in ("authorization_endpoint", "token_endpoint"):
        official(data[field])
    return data


def slot(alias, kind):
    return "chatgpt-" + kind + "-" + identifier(alias, "account alias")


def accept(p, alias, cfg, data, prior_scopes=None):
    from .core import digest

    scopes = (
        data["scope"].split()
        if isinstance(data.get("scope"), str)
        else prior_scopes or []
    )
    require(
        set(cfg["scopes"]) <= set(scopes)
        and str(data.get("token_type", "")).lower() == "bearer",
        "required bearer permissions unverified",
    )
    require(
        data.get("resource", cfg["responses_url"]) == cfg["responses_url"],
        "unexpected access token resource",
    )
    token = text_field(data.get("access_token"), "access token", 8192)
    ttl = data.get("expires_in")
    require(type(ttl) is int and ttl > 0, "missing token expiration")
    if data.get("refresh_token"):
        vault.store(p, slot(alias, "refresh"), data["refresh_token"])
    vault.store(p, slot(alias, "access"), token)
    record = {
        "alias": alias,
        "scope": scopes,
        "expires": int(time.time()) + ttl,
        "registration": digest(cfg),
        "entitlement": "UNKNOWN",
    }
    p.put("oauth_accounts", alias, record)
    p.put("oauth", "active", alias)
    return record


def login(p, alias):
    identifier(alias, "account alias")
    cfg = p.get("oauth", "registration")
    require(
        cfg and cfg.get("paid_product") is False,
        "independent non-paid-product ChatGPT registration required",
    )
    require(
        not p.get("oauth_accounts", alias),
        "log out this account alias before replacing it",
    )
    meta = metadata()
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_urlsafe(32)
    redirect = f"http://127.0.0.1:{cfg['port']}/callback"
    received = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            ok = (
                self.headers.get("Host") == f"127.0.0.1:{cfg['port']}"
                and u.path == "/callback"
                and q.get("state") == [state]
                and len(q.get("code", [])) == 1
                and 0 < len(q["code"][0]) <= 8192
            )
            self.send_response(200 if ok else 400)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if ok:
                received.append(q["code"][0])
            self.wfile.write(b"Return to RAYA Core.")

        def setup(self):
            self.request.settimeout(2)
            super().setup()

    with HTTPServer(("127.0.0.1", cfg["port"]), Callback) as srv:
        srv.timeout = 0.5
        url = (
            meta["authorization_endpoint"]
            + "?"
            + urlencode(
                dict(
                    response_type="code",
                    client_id=cfg["client_id"],
                    redirect_uri=redirect,
                    resource=cfg["responses_url"],
                    scope=" ".join(cfg["scopes"]),
                    state=state,
                    code_challenge=challenge,
                    code_challenge_method="S256",
                )
            )
        )
        require(webbrowser.open(url), "unable to open authorization browser")
        deadline = time.monotonic() + 180
        while not received and time.monotonic() < deadline:
            srv.handle_request()
    require(received, "sign-in timed out")
    data = oauth_request(
        meta["token_endpoint"],
        dict(
            grant_type="authorization_code",
            code=received[0],
            client_id=cfg["client_id"],
            resource=cfg["responses_url"],
            redirect_uri=redirect,
            code_verifier=verifier,
        ),
    )
    return accept(p, alias, cfg, data)


def account(p, action, alias):
    from .core import digest

    identifier(alias, "account alias")
    cfg = p.get("oauth", "registration")
    record = p.get("oauth_accounts", alias)
    require(record, "unknown account alias")
    if action == "logout":
        remote = False
        try:
            endpoint = metadata().get("revocation_endpoint")
            if endpoint and cfg and record["registration"] == digest(cfg):
                token = vault.read_secret(
                    p, slot(alias, "refresh")
                ) or vault.read_secret(p, slot(alias, "access"))
                if token:
                    oauth_request(
                        endpoint, {"client_id": cfg["client_id"], "token": token}
                    )
                    remote = True
        except (ValueError, OSError, KeyError, http.client.HTTPException):
            pass
        for kind in ("access", "refresh"):
            child(vault.files(p), slot(alias, kind) + ".secret").unlink(missing_ok=True)
        p.drop("oauth_accounts", alias)
        if p.get("oauth", "active") == alias:
            p.drop("oauth", "active")
        return {"removed_locally": alias, "remote_revocation_confirmed": remote}
    require(
        cfg and record["registration"] == digest(cfg),
        "registration changed; sign in again",
    )
    if action == "switch":
        p.put("oauth", "active", alias)
        return record
    require(action == "refresh", "unknown account operation")
    refresh = vault.read_secret(p, slot(alias, "refresh"))
    require(refresh, "fresh interactive login required")
    data = oauth_request(
        metadata()["token_endpoint"],
        dict(
            grant_type="refresh_token",
            client_id=cfg["client_id"],
            resource=cfg["responses_url"],
            refresh_token=refresh,
        ),
    )
    return accept(p, alias, cfg, data, record["scope"])
