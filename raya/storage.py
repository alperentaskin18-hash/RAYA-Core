"""Validate paths inside an operator-owned, private local profile."""

import os
from pathlib import Path, PureWindowsPath
import stat


def checked(path, *, directory=False):
    """Reject aliases, special files and multiply-linked data before use."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if reparse or not expected(info.st_mode) or (not directory and info.st_nlink != 1):
        raise ValueError("unsafe profile path")
    if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise ValueError(
            "profile path must be owned and writable only by the current user"
        )
    return path


def profile_root(value):
    path = Path(value).absolute()
    checked(path, directory=True)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    checked(path, directory=True)
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("profile directory must be private (mode 0700)")
    return path.resolve(strict=True)


def child(parent, name, *, directory=False):
    if not name or name in (".", "..") or any(c in name for c in "/\\:\x00"):
        raise ValueError("invalid profile path component")
    if PureWindowsPath(name).is_reserved() or name.endswith((" ", ".")):
        raise ValueError("reserved profile path component")
    return checked(parent / name, directory=directory)
