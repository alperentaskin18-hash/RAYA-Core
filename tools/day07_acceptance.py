"""Owner-local acceptance: discover explicitly selected routes and review each disclosure."""

import argparse
import getpass
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raya import cloud, engine, development as dev, vault
from raya.core import Profile, decide


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--repository", type=Path, default=Path.cwd())
    ap.add_argument(
        "--omniroute",
        action="store_true",
        help="probe this computer's authenticated 127.0.0.1:20128",
    )
    ap.add_argument(
        "--store-key",
        action="store_true",
        help="prompt for an OmniRoute key in this profile",
    )
    ap.add_argument("--route", default="auto")
    ap.add_argument(
        "--live",
        action="store_true",
        help="allow reviewed inference on eligible routes",
    )
    a = ap.parse_args()
    p = Profile(a.profile)
    try:
        with p.transaction():
            cloud.enable(p)
        if a.omniroute:
            if a.store_key:
                with p.transaction():
                    vault.store(p, "ai-omniroute", getpass.getpass("OmniRoute key: "))
            if vault.read_secret(p, "ai-omniroute"):
                cfg = engine.config(p)
                routes = dict(cfg["routes"])
                if routes and cfg["offline"]:
                    raise SystemExit(
                        "Existing profile is offline. Enable cloud access explicitly with ai-config."
                    )
                existing = routes.get("owner-omni", {})
                if existing and (
                    existing.get("provider") != "omniroute"
                    or existing.get("port", 20128) != 20128
                    or existing.get("credential")
                ):
                    raise SystemExit(
                        "owner-omni must use the default credential at 127.0.0.1:20128."
                    )
                routes.setdefault(
                    "owner-omni",
                    {"provider": "omniroute", "model": "discovery-only", "port": 20128},
                )
                with p.transaction():
                    engine.configure(p, {"routes": routes, "offline": False})
                    report = cloud.discover(p, "owner-omni")
                print(json.dumps({"omniroute": report}, ensure_ascii=True))
            else:
                print("OmniRoute discovery skipped: no profile credential.")
        if a.live:
            for alias, r in engine.config(p)["routes"].items():
                if alias in (a.route,) or a.route == "auto":
                    if r["provider"] in ("openrouter", "gemini") and engine.credential(
                        p, r["provider"], r
                    ):
                        with p.transaction():
                            cloud.discover(p, alias)
        results = []
        for kind in dev.TARGETS:
            with p.transaction():
                task = dev.start(
                    p, dict(kind=kind, repository=a.repository, route=a.route)
                )
            ident = task["id"]
            for _ in range(12):
                with p.transaction():
                    task = dev.step(p, ident)
                if "approval" in task:
                    req = task["approval"]
                    if not a.live:
                        break
                    print(json.dumps(req, indent=2, ensure_ascii=True))
                    ok = (
                        input(
                            "Approve this exact source disclosure and generation? [yes/no] "
                        )
                        == "yes"
                    )
                    with p.transaction():
                        decide(p, req["id"], req["digest"], ok)
                    if not ok:
                        break
                elif task["phase"] == "repair":
                    continue
                else:
                    break
            if task["phase"] == "review" and a.live:
                with p.transaction():
                    req = dev.commit_proposal(p, ident)
                print(json.dumps(req, ensure_ascii=True, indent=2))
                ok = input("Create this isolated proposal commit? [yes/no] ") == "yes"
                with p.transaction():
                    decide(p, req["id"], req["digest"], ok)
            results.append(dev.summary(p.get("dev_tasks", ident)))
        print(
            json.dumps(
                {
                    "tasks": results,
                    "pass": all(t["phase"] == "proposed" for t in results),
                },
                ensure_ascii=True,
            )
        )
        return 0 if all(t["phase"] == "proposed" for t in results) else 2
    finally:
        p.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
