"""Suspend/restart recovery for the isolated training subprocess.

This never touches the predictor, trading processes, or a PID whose
creation identity does not match the recorded training child. Instant
suspension is only available while the operating system retains the child.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


class ProcessControlError(OSError):
    """A training process cannot be identified or suspended safely."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_SUSPEND_RESUME = 0x0800
    _STILL_ACTIVE = 259

    class _FILETIME(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    _kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
    )
    _kernel.GetProcessTimes.restype = wintypes.BOOL
    _kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel.GetExitCodeProcess.restype = wintypes.BOOL
    _ntdll.NtSuspendProcess.argtypes = (wintypes.HANDLE,)
    _ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    _ntdll.NtSuspendProcess.restype = wintypes.LONG
    _ntdll.NtResumeProcess.restype = wintypes.LONG


def _pid(pid: int) -> int:
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise ProcessControlError("invalid external training worker PID")
    return pid


def identity(pid: int) -> str:
    """Record a creation timestamp so a recycled PID cannot be controlled."""
    pid = _pid(pid)
    if sys.platform == "win32":
        handle = _kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise ProcessControlError(f"Cannot inspect training worker PID {pid}")
        try:
            create, exit_time, kernel, user = (_FILETIME() for _ in range(4))
            code = wintypes.DWORD()
            if not _kernel.GetProcessTimes(handle, ctypes.byref(create),
                    ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
                raise ProcessControlError("Cannot read training worker creation identity")
            if (not _kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                    or code.value != _STILL_ACTIVE):
                raise ProcessControlError("Training worker already exited")
            return f"win:{pid}:{(create.high << 32) | create.low}"
        finally:
            _kernel.CloseHandle(handle)
    proc = Path(f"/proc/{pid}/stat")
    if not proc.is_file():
        raise ProcessControlError("Operating system cannot verify worker identity")
    try:
        parts = proc.read_text(encoding="ascii").rsplit(") ", 1)[1].split()
        if parts[0] in ("Z", "X"):
            raise ProcessControlError("Training worker is no longer live")
        return f"proc:{pid}:{parts[19]}"  # Linux /proc/<pid>/stat field 22
    except (OSError, ValueError, IndexError) as exc:
        raise ProcessControlError("Unable to inspect live worker identity") from exc


def is_alive(pid: int, expected_identity: str) -> bool:
    try:
        return bool(expected_identity) and identity(pid) == expected_identity
    except (ProcessControlError, OSError):
        return False


def _toggle(pid: int, *, freeze: bool) -> None:
    pid = _pid(pid)
    if sys.platform == "win32":
        handle = _kernel.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_SUSPEND_RESUME,
            False, pid,
        )
        if not handle:
            raise ProcessControlError("Windows denied training worker suspension")
        try:
            result = (_ntdll.NtSuspendProcess if freeze else _ntdll.NtResumeProcess)(handle)
            if result < 0:
                raise ProcessControlError(
                    f"Windows training worker {'suspend' if freeze else 'resume'} failed "
                    f"(NTSTATUS 0x{result & 0xffffffff:08x})"
                )
        finally:
            _kernel.CloseHandle(handle)
    else:
        if not Path(f"/proc/{pid}/stat").exists():
            raise ProcessControlError("Process suspension not supported here")
        os.kill(pid, signal.SIGSTOP if freeze else signal.SIGCONT)


def suspend(pid: int) -> None:
    """Freeze the exact worker process, including native model-fit threads."""
    _toggle(pid, freeze=True)


def resume(pid: int) -> None:
    """Thaw the same worker without recomputing its in-memory model fit."""
    _toggle(pid, freeze=False)
