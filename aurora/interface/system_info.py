"""
Real system readings for the Command Centre, using Windows APIs through
ctypes (no extra dependency). On other systems, or if a call fails, the
functions return None and the interface shows "unavailable": a reading
is never invented.
"""

import ctypes
import os
import sys
import time
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class MemoryInfo:
    total_bytes: int
    used_bytes: int

    @property
    def percent(self) -> float:
        return 100.0 * self.used_bytes / self.total_bytes if self.total_bytes else 0.0


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    @property
    def value(self) -> int:
        return (self.high << 32) | self.low


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class CpuMeter:
    """System-wide CPU use between two calls (the first call returns None)."""

    def __init__(self):
        self._last = None

    def _times(self):
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return idle.value, kernel.value + user.value   # Kernel time includes idle time.

    def percent(self) -> float | None:
        if not IS_WINDOWS:
            return None
        try:
            now = self._times()
        except (AttributeError, OSError):
            return None
        if now is None:
            return None
        previous, self._last = self._last, now
        if previous is None:
            return None
        idle = now[0] - previous[0]
        total = now[1] - previous[1]
        if total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (total - idle) / total))


def system_memory() -> MemoryInfo | None:
    if not IS_WINDOWS:
        return None
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return MemoryInfo(status.ullTotalPhys, status.ullTotalPhys - status.ullAvailPhys)
    except (AttributeError, OSError):
        return None


def process_memory_bytes() -> int | None:
    """Memory used by this program (working set)."""
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        # HANDLE is 64 bits wide on 64-bit Windows; ctypes defaults to 32-bit ints.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        get_info = kernel32.K32GetProcessMemoryInfo
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCounters), ctypes.c_uint32]
        get_info.restype = ctypes.c_int
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        if not get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    except (AttributeError, OSError):
        return None


def cpu_count() -> int:
    return os.cpu_count() or 0


def human_bytes(value: int | None) -> str:
    if value is None:
        return "unavailable"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


START_TIME = time.monotonic()


def uptime_seconds() -> float:
    return time.monotonic() - START_TIME
