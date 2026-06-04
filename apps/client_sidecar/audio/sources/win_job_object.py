# === ANCHOR: WIN_JOB_OBJECT_START ===
"""Bind a child process to the sidecar's lifetime via a Windows Job Object.

Why this exists (Phase 2b orphan cleanup):
On Windows there is no process-group SIGTERM, and WASAPI loopback delivers no
stdout during silence — so the native helper's broken-pipe exit is never probed
while audio is paused. If the sidecar is hard-killed (Task Manager, or Tauri's
``child.kill()`` = TerminateProcess) during silence, ``yeson-win-audio-helper.exe``
can linger as an orphan that keeps capturing.

Fix: put the helper in a Job Object flagged ``KILL_ON_JOB_CLOSE`` whose only
handle is held by *this* (the sidecar) process. When the sidecar dies for any
reason, the OS closes that last handle and reaps the helper immediately. This is
the Windows analog of the macOS process-group SIGTERM cleanup.

No-op on non-Windows: Unix is already covered by the Tauri process-group SIGTERM
plus the helper's own SIGTERM handler.

Best-effort: every failure returns ``None`` (orphan-prevention is defense in
depth, never a correctness gate). The returned handle MUST be kept alive for as
long as the child should live — letting it close/GC fires KILL_ON_JOB_CLOSE and
terminates the child.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# Win32 constants
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class JobHandle:
    """Owns the Job Object handle. Closing it (explicitly or on GC) reaps the
    assigned child via KILL_ON_JOB_CLOSE, so keep it alive deliberately."""

    def __init__(self, handle: int, close_handle) -> None:
        self._handle: int | None = handle
        self._close_handle = close_handle

    def close(self) -> None:
        if self._handle is not None:
            self._close_handle(self._handle)
            self._handle = None

    def __del__(self) -> None:  # pragma: no cover - GC-timing dependent
        self.close()


def bind_process_to_job(pid: int) -> JobHandle | None:
    """Assign ``pid`` to a fresh KILL_ON_JOB_CLOSE Job Object.

    Returns a :class:`JobHandle` to keep alive for the child's lifetime, or
    ``None`` on non-Windows / any Win32 failure.
    """
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t  # ULONG_PTR is pointer-sized

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ulong_ptr),
            ("MaximumWorkingSetSize", ulong_ptr),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ulong_ptr),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ulong_ptr),
            ("JobMemoryLimit", ulong_ptr),
            ("PeakProcessMemoryUsed", ulong_ptr),
            ("PeakJobMemoryUsed", ulong_ptr),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Pin signatures: a HANDLE is pointer-sized; the default c_int restype would
    # truncate it on 64-bit Python and corrupt every subsequent call.
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        logger.warning("CreateJobObjectW failed err=%s", ctypes.get_last_error())
        return None

    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
        job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        logger.warning("SetInformationJobObject failed err=%s", ctypes.get_last_error())
        k32.CloseHandle(job)
        return None

    hproc = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not hproc:
        logger.warning("OpenProcess(pid=%s) failed err=%s", pid, ctypes.get_last_error())
        k32.CloseHandle(job)
        return None

    assigned = k32.AssignProcessToJobObject(job, hproc)
    k32.CloseHandle(hproc)
    if not assigned:
        logger.warning("AssignProcessToJobObject failed err=%s", ctypes.get_last_error())
        k32.CloseHandle(job)
        return None

    logger.info("native helper pid=%s bound to kill-on-close job object", pid)
    return JobHandle(job, k32.CloseHandle)
# === ANCHOR: WIN_JOB_OBJECT_END ===
