"""Separate user-only vault. Windows also encrypts values with current-user DPAPI."""

import ctypes
from ctypes import wintypes
import os
import tempfile
from pathlib import Path

from .storage import child
from .core import identifier, require


def windows_protect(raw, decrypt):
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    memory = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(memory, ctypes.POINTER(ctypes.c_char)))
    output = Blob()
    library = ctypes.windll.crypt32
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise ValueError("Windows credential protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def files(profile):
    directory = child(profile.directory, "vault", directory=True)
    require(not directory.is_symlink(), "invalid vault directory")
    return directory


def read_secret(profile, name):
    identifier(name, "secret name")
    path = child(files(profile), name + ".secret")
    if not path.exists():
        return None
    raw = path.read_bytes()
    return (windows_protect(raw, True) if os.name == "nt" else raw).decode()


def remove_provider_key(profile, provider):
    from .ai_protocol import HOSTS, LOCAL

    require(provider in HOSTS and provider not in LOCAL, "unknown cloud provider")
    child(files(profile), "ai-" + provider + ".secret").unlink(missing_ok=True)
    return {"removed": "ai-" + provider}


def values(profile):
    directory = files(profile)
    if directory.exists():
        for path in directory.glob("*.secret"):
            require(not path.is_symlink(), "invalid vault entry")
            raw = child(directory, path.name).read_bytes()
            yield (windows_protect(raw, True) if os.name == "nt" else raw).decode()


def store(profile, name, value):
    identifier(name, "secret name")
    require(1 <= len(value) <= 8192, "invalid secret length")
    import json

    for (record,) in profile.db.execute("SELECT value FROM objects"):
        require(
            not contains(json.loads(record), value),
            "value already appears in ordinary profile data",
        )
    directory = files(profile)
    directory.mkdir(mode=0o700, exist_ok=True)
    raw = value.encode()
    if os.name == "nt":
        raw = windows_protect(raw, False)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(raw)
            out.close()
            os.replace(temporary, child(directory, name + ".secret"))
        finally:
            temporary.unlink(missing_ok=True)
    return {"stored": name}


def contains(data, value):
    if isinstance(data, str):
        return value in data
    if isinstance(data, dict):
        return any(contains(k, value) or contains(v, value) for k, v in data.items())
    if isinstance(data, list):
        return any(contains(v, value) for v in data)
    return False
