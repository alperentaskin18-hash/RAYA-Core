"""Shared operator actions for the cloud CLI and HUD."""

from . import chatgpt_auth as auth, cloud, development as dev
from .core import require


def action(p, cmd, a):
    if cmd == "cloud-init":
        return cloud.enable(p)
    if cmd == "cloud-report":
        return cloud.report(p)
    if cmd == "cloud-catalog":
        return {
            alias: cloud.public_catalog(row)
            for alias, row in p.all("cloud_catalog").items()
        }
    if cmd == "cloud-accounts":
        return {
            "accounts": p.all("oauth_accounts"),
            "active": p.get("oauth", "active"),
            "registered": bool(p.get("oauth", "registration")),
        }
    if cmd == "dev-status":
        return {k: dev.summary(t) for k, t in p.all("dev_tasks").items()}
    require(
        cloud.strict(p) or cmd in ("dev-start", "cloud-eval"),
        "enable strict mode with cloud-init",
    )
    if cmd == "cloud-discover":
        return cloud.discover(p, a["route"])
    if cmd == "cloud-quota":
        return cloud.quota_snapshot(p, a)
    if cmd == "cloud-limits":
        return cloud.limit(p, a)
    if cmd == "cloud-observation":
        return cloud.annotate(p, a)
    if cmd == "cloud-register":
        return auth.registration(p, a)
    if cmd == "cloud-login":
        return auth.login(p, a["account"])
    if cmd in ("cloud-refresh", "cloud-switch", "cloud-logout"):
        return auth.account(p, cmd.removeprefix("cloud-"), a["account"])
    if cmd == "cloud-eval":
        return cloud.eval_request(p, a)
    if cmd == "cloud-score":
        return cloud.eval_score(p, a["id"])
    if cmd == "dev-start":
        return dev.start(p, a)
    if cmd == "dev-step":
        return dev.step(p, a["id"])
    if cmd == "dev-review":
        return dev.commit_proposal(p, a["id"])
    if cmd in ("dev-cancel", "dev-rollback"):
        return dev.cancel(p, a["id"], cmd == "dev-rollback")
    raise ValueError("unknown cloud control command")
