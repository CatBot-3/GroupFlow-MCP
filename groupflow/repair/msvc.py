"""Windows: make sure OR-Tools runs on a new enough Microsoft C++ runtime.

OR-Tools wheels are built with Visual Studio 2022 and need msvcp140.dll 14.40 or
newer. Some Python distributions (Anaconda, for one) ship an older msvcp140.dll
next to python.exe, and Windows loads that copy first; CP-SAT then crashes the
whole process with an access violation the moment it solves. Loading the
system's copy (installed by the Visual C++ Redistributable) before OR-Tools
makes every later load of these DLLs reuse it.

If no new enough runtime exists, `runtime_ok()` is False and the solver falls
back to exact enumeration instead of crashing.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

RUNTIME_DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140_1.dll")
MIN_VERSION = (14, 40)
_state: dict[str, object] = {}


def file_version(path: Path) -> tuple[int, int, int, int] | None:
    """The file version of a Windows DLL, from its version resource."""
    if os.name != "nt" or not path.is_file():
        return None
    try:
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr, length = ctypes.c_void_p(), ctypes.c_uint()
        if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents  # VS_FIXEDFILEINFO
        ms, ls = info[2], info[3]
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except (OSError, AttributeError, ValueError):
        return None


def prepare() -> bool:
    """Load a new enough C++ runtime before OR-Tools. Returns True when CP-SAT is safe to use."""
    if "ok" in _state:
        return bool(_state["ok"])
    if os.name != "nt":
        _state["ok"] = True
        return True
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    bundled = Path(sys.base_prefix) / "msvcp140.dll"
    bundled_v = file_version(bundled)
    system_v = file_version(system32 / "msvcp140.dll")
    ok = bundled_v is None or bundled_v[:2] >= MIN_VERSION
    if system_v and system_v[:2] >= MIN_VERSION:
        try:
            for name in RUNTIME_DLLS:
                if (system32 / name).is_file():
                    ctypes.WinDLL(str(system32 / name))
            ok = True
        except OSError:
            pass
    _state.update(ok=ok, bundled=bundled_v, system=system_v)
    return ok


def describe() -> str:
    prepare()
    fmt = lambda v: ".".join(map(str, v)) if v else "none"  # noqa: E731
    return f"C++ runtime next to Python: {fmt(_state.get('bundled'))}; in System32: {fmt(_state.get('system'))}; CP-SAT {'enabled' if _state.get('ok') else 'disabled'}"
