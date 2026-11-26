"""Trusted, closed set of pure text operations. Data never selects executable code."""

import hashlib
import json
import sys


def guard(event, args):
    if event in {
        "open",
        "subprocess.Popen",
        "os.system",
        "os.putenv",
    } or event.startswith(("socket.", "ctypes.")):
        raise PermissionError("text runtime denies " + event)


def windows_memory_limit(cap):
    import ctypes
    from ctypes import wintypes

    size = ctypes.c_size_t

    class Basic(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working", size),
            ("max_working", size),
            ("active", wintypes.DWORD),
            ("affinity", size),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", size),
            ("job_memory", size),
            ("peak_process", size),
            ("peak_job", size),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    settings = Extended()
    settings.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    settings.process_memory = cap
    if not job or not kernel.SetInformationJobObject(
        wintypes.HANDLE(job), 9, ctypes.byref(settings), ctypes.sizeof(settings)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(-1)):
        raise ctypes.WinError(ctypes.get_last_error())


cap = int(sys.argv[1]) * 1024 * 1024
if sys.platform == "win32":
    windows_memory_limit(cap)
else:
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limits = [n for n in (cap, soft, hard) if n >= 0]
    resource.setrlimit(resource.RLIMIT_AS, (min(limits), min(limits)))
sys.addaudithook(guard)
operation, value = json.load(sys.stdin)
if not isinstance(value, str) or len(value) > 16384:
    raise ValueError("invalid text")
operations = {
    "upper": str.upper,
    "lower": str.lower,
    "count": lambda s: str(len(s)),
    "reverse": lambda s: s[::-1],
    "sha256": lambda s: hashlib.sha256(s.encode()).hexdigest(),
}
json.dump(operations[operation](value), sys.stdout)
