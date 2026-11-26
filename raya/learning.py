"""Read public GitHub metadata at a full commit without cloning or executing code."""

import base64
import hashlib
import tomllib
import json
import re
import urllib.request
from pathlib import PurePosixPath

from .core import require


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch(path):
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open("https://api.github.com/repos/" + path, timeout=10) as response:
        data = response.read(4_000_001)
    require(len(data) <= 4_000_000, "metadata too large")
    return json.loads(data)


def descriptor(repo, path, blob_sha):
    require(re.fullmatch("[0-9a-f]{40}", blob_sha), "invalid blob identity")
    blob = fetch(f"{repo}/git/blobs/{blob_sha}")
    require(
        blob.get("encoding") == "base64" and blob.get("size", 0) <= 262144,
        "unsupported descriptor",
    )
    raw = base64.b64decode("".join(blob["content"].split()), validate=True)
    checksum = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\x00" + raw
    ).hexdigest()
    require(
        len(raw) == blob["size"] and checksum == blob_sha, "descriptor digest mismatch"
    )
    if path == "pyproject.toml":
        data = tomllib.loads(raw.decode())["project"]
        return {
            "dependencies": data.get("dependencies", []),
            "scripts": data.get("scripts", {}),
            "license": data.get("license"),
        }
    if path == "package.json":
        data = json.loads(raw)
        return {
            "dependencies": data.get("dependencies", {}),
            "scripts": data.get("scripts", {}),
            "license": data.get("license"),
        }
    return {
        "dependencies": [
            line.strip()
            for line in raw.decode().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    }


def learn(profile, url, revision):
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)", url)
    require(
        match and re.fullmatch("[0-9a-f]{40}", revision),
        "public GitHub URL and full commit required",
    )
    repo = match[1]
    require(repo.split("/")[1] not in {".", ".."}, "invalid repository")
    key = f"{repo}@{revision}"
    cached = profile.get("imports", key)
    if cached:
        return cached
    metadata = fetch(repo)
    require(
        metadata.get("private") is False
        and metadata["full_name"].lower() == repo.lower(),
        "public repository required",
    )
    commit = fetch(f"{repo}/git/commits/{revision}")
    require(commit["sha"] == revision, "commit mismatch")
    tree_sha = commit["tree"]["sha"]
    require(re.fullmatch("[0-9a-f]{40}", tree_sha), "invalid tree")
    tree = fetch(f"{repo}/git/trees/{tree_sha}?recursive=1")
    require(tree["sha"] == tree_sha and tree.get("truncated") is False, "partial tree")
    files, omitted, descriptors = [], [], {}
    for entry in tree["tree"]:
        path = entry["path"]
        require(
            path
            and not path.startswith("/")
            and not set(path.split("/")) & {"", ".", ".."}
            and "\\" not in path
            and ":" not in path,
            "unsafe repository path",
        )
        if entry["mode"] in {"100644", "100755"}:
            files.append(path)
            if path in {"pyproject.toml", "package.json", "requirements.txt"}:
                try:
                    descriptors[path] = descriptor(repo, path, entry["sha"])
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"invalid descriptor {path}") from exc
        elif entry["mode"] != "040000":
            omitted.append(path)
    specs = {
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
    }
    invocation = []
    for filename in files:
        if filename.endswith("/__main__.py"):
            package = (
                filename.removesuffix("/__main__.py").removeprefix("src/").split("/")
            )
            if all(part.isidentifier() for part in package):
                invocation.append(["python", "-m", ".".join(package)])
    scripts = descriptors.get("package.json", {}).get("scripts", {})
    if isinstance(scripts, dict):
        invocation.extend(
            ["npm", "run", label]
            for label in scripts
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9:_-]*", label)
        )
    report = dict(
        descriptors=descriptors,
        url=url,
        revision=revision,
        license_hint=(metadata.get("license") or {}).get("spdx_id"),
        license_files=[
            p for p in files if PurePosixPath(p).name.upper().startswith("LICENSE")
        ],
        dependency_files=[p for p in files if PurePosixPath(p).name in specs],
        interfaces=[
            p
            for p in files
            if p.endswith(("__main__.py", "openapi.json", "openapi.yaml"))
        ],
        excluded_links=omitted,
        invocation_candidates=invocation,
        risks=[
            "Invocation requires prior review",
            "Dependency declarations may reference external code",
        ],
    )
    profile.put("imports", key, report)
    return report
