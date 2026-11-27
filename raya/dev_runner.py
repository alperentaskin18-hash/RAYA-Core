"""Isolated child entry point. Admits Python functions only after OS confinement."""

import ast
import ctypes
import errno
import json
import os
import resource
import socket
import sys
import time


def confine():
    resource.setrlimit(resource.RLIMIT_AS, (160 << 20, 160 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    lib = ctypes.CDLL("libseccomp.so.2")
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x00050000 | errno.EPERM)
    if not ctx:
        raise RuntimeError("seccomp unavailable")
    try:
        for name in (
            "read",
            "write",
            "close",
            "exit",
            "exit_group",
            "brk",
            "munmap",
            "mmap",
            "mmap2",
            "mremap",
            "futex",
            "clock_gettime",
            "rt_sigreturn",
            "fstat",
        ):
            nr = lib.seccomp_syscall_resolve_name(name.encode())
            if nr >= 0 and lib.seccomp_rule_add(ctx, 0x7FFF0000, nr, 0) != 0:
                raise RuntimeError("seccomp rule failed")
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError("seccomp activation failed")
    finally:
        lib.seccomp_release(ctx)
    for operation in (lambda: os.open("/dev/null", os.O_RDONLY), socket.socket):
        try:
            operation()
        except PermissionError:
            continue
        raise RuntimeError("sandbox self-check failed")


def main():
    request = json.loads(sys.stdin.buffer.read(262145))
    os.environ.clear()
    os.closerange(3, 65536)
    confine()
    source = request["source"]
    module = ast.parse(source)
    if (
        len(module.body) != 1
        or not isinstance(module.body[0], ast.FunctionDef)
        or module.body[0].name != "solve"
    ):
        raise ValueError("expected one solve function")
    fn = module.body[0]
    if (
        fn.decorator_list
        or fn.returns
        or fn.args.defaults
        or fn.args.kwonlyargs
        or fn.args.vararg
        or fn.args.kwarg
        or fn.args.posonlyargs
        or len(fn.args.args) != 1
        or fn.args.args[0].annotation
    ):
        raise ValueError("one plain positional argument required")
    for node in ast.walk(module):
        if (
            isinstance(
                node,
                (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef),
            )
            or isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            or isinstance(node, ast.Name)
            and node.id.startswith("_")
        ):
            raise ValueError("unsupported generated construct")
    builtins = {
        k: __builtins__.__dict__[k]
        for k in (
            "abs",
            "all",
            "any",
            "bool",
            "dict",
            "enumerate",
            "float",
            "int",
            "len",
            "list",
            "max",
            "min",
            "range",
            "reversed",
            "round",
            "set",
            "sorted",
            "str",
            "sum",
            "tuple",
            "zip",
        )
    }
    scope = {"__builtins__": builtins}
    exec(compile(module, "<candidate>", "exec"), scope)
    results = []
    start = time.process_time_ns()
    for arg in request["inputs"]:
        results.append(scope["solve"](arg))
    elapsed = time.process_time_ns() - start
    out = json.dumps({"results": results, "cpu_ns": elapsed}, allow_nan=False)
    if len(out) > 60000:
        raise ValueError("result too large")
    print(out)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}))
        sys.exit(1)
