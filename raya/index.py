"""Incremental static analysis of explicitly selected Python source."""

import ast
import hashlib
import os
import stat
from pathlib import Path

from .core import require

SKIP = {"__pycache__", "build", "dist", "venv", "node_modules", "private", "secrets"}


def parse(content, module, package):
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, UnicodeError) as exc:
        return {"symbols": [], "imports": [], "uncertain": True, "error": str(exc)}
    symbols, imports = [], set()
    dynamic = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append([node.name, node.lineno, node.end_lineno])
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                base = ".".join(
                    filter(None, parts[: max(0, len(parts) - node.level + 1)] + [base])
                )
            if base:
                imports.add(base)
            imports.update(".".join(filter(None, [base, a.name])) for a in node.names)
        if isinstance(node, ast.Call) and (
            getattr(node.func, "id", "") in {"eval", "exec", "__import__"}
            or getattr(node.func, "attr", "") == "import_module"
        ):
            dynamic = True
    return dict(symbols=symbols, imports=sorted(imports), uncertain=dynamic)


def index(profile, root):
    root = Path(root).resolve(strict=True)
    require(root.is_dir(), "project directory required")
    previous = profile.get("indexes", str(root), {})
    entries, parsed = {}, 0
    if hasattr(os, "fwalk"):
        traversal = os.fwalk(root, follow_symlinks=False)
    else:
        traversal = (
            (directory, dirs, files, None) for directory, dirs, files in os.walk(root)
        )
    for directory, dirs, files, parent_fd in traversal:
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d.casefold() not in SKIP
            and Path(directory, d).resolve() == Path(directory, d)
            and Path(directory, d) != profile.directory
        )
        for name in sorted(files):
            path = Path(directory, name)
            if (
                not name.endswith(".py")
                or name.startswith(".")
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            rel = path.relative_to(root).as_posix()
            if path.resolve() != path:
                continue
            before = path.lstat()
            if before.st_nlink != 1 or not stat.S_ISREG(before.st_mode):
                continue
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_BINARY", 0)
            )
            fd = os.open(
                name if parent_fd is not None else path, flags, dir_fd=parent_fd
            )
            with os.fdopen(fd, "rb") as source:
                opened = os.fstat(source.fileno())
                require(
                    os.path.samestat(before, opened)
                    and stat.S_ISREG(opened.st_mode)
                    and opened.st_nlink == 1,
                    "source changed during indexing",
                )
                data = source.read(2**20 + 1)
            require(len(data) <= 2**20, f"file too large: {rel}")
            checksum = hashlib.sha256(data).hexdigest()
            if previous.get(rel, {}).get("sha") == checksum:
                entries[rel] = previous[rel]
                continue
            module = rel[:-3].replace("/", ".").removesuffix(".__init__")
            package = module if name == "__init__.py" else module.rpartition(".")[0]
            entries[rel] = dict(
                sha=checksum, module=module, **parse(data, module, package)
            )
            parsed += 1
    profile.put("indexes", str(root), entries)
    return {"parsed": parsed, "files": len(entries)}


def context(profile, root, symbol):
    entries = profile.get("indexes", str(Path(root).resolve()))
    require(entries is not None, "index project first")
    matches = {
        p: [s for s in e["symbols"] if s[0] == symbol] for p, e in entries.items()
    }
    matches = {p: s for p, s in matches.items() if s}
    affected = set(matches)
    while True:
        modules = {entries[p]["module"] for p in affected}
        new = {p for p, e in entries.items() if modules.intersection(e["imports"])}
        if new <= affected:
            break
        affected.update(new)
    tests = {
        p for p in entries if Path(p).name.startswith("test_") or p.endswith("_test.py")
    }
    return dict(
        definitions=matches,
        importers=sorted(affected - set(matches)),
        impacted_tests=sorted(tests & affected),
        mandatory_tests=sorted(tests),
        uncertain=sorted(p for p, e in entries.items() if e["uncertain"]),
    )
