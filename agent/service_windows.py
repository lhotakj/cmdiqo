"""Native Windows Service host for shango-agent using standard library ctypes.

Provides native Windows Service Control Manager (SCM) integration with zero
external dependencies. Works in both Python runtime and PyInstaller
frozen executables.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import sys
import threading
from collections.abc import Callable, Coroutine
from ctypes import wintypes
from pathlib import Path
from typing import Any

log = logging.getLogger("shango-agent.service")

SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
SERVICE_STOPPED = 0x00000001

SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005
SERVICE_CONTROL_INTERROGATE = 0x00000004

SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004

NO_ERROR = 0
ERROR_CALL_NOT_IMPLEMENTED = 0x00000078
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


SERVICE_MAIN_FUNCTION = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR)
)
HANDLER_FUNCTION_EX = ctypes.WINFUNCTYPE(
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID
)


class SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", SERVICE_MAIN_FUNCTION),
    ]


# Persistent handles to avoid Python garbage collection
_c_main_func: Any = None
_c_handler_func: Any = None
_h_status: wintypes.HANDLE | None = None
_stop_event = threading.Event()


def _set_status(state: int, win32_exit_code: int = NO_ERROR, wait_hint: int = 0) -> None:
    if not _h_status:
        return
    controls = (SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN) if state == SERVICE_RUNNING else 0
    status = SERVICE_STATUS(
        dwServiceType=SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState=state,
        dwControlsAccepted=controls,
        dwWin32ExitCode=win32_exit_code,
        dwServiceSpecificExitCode=0,
        dwCheckPoint=0,
        dwWaitHint=wait_hint,
    )
    ctypes.windll.advapi32.SetServiceStatus(_h_status, ctypes.byref(status))


def setup_service_logging(log_file: Path | None = None) -> Path:
    """Redirect logging to persistent file when running under Windows Service Control Manager."""
    if log_file is None:
        log_dir = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Shango" / "Agent" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "service.log"
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    return log_file


def run_as_windows_service(
    service_name: str,
    run_coro_fn: Callable[[threading.Event], Coroutine[Any, Any, None]],
    log_path: Path | None = None,
) -> None:
    """Run the given async callable as a native Windows Service."""
    global _c_main_func, _c_handler_func

    log_file = setup_service_logging(log_path)
    log.info("Starting ShangoAgent service dispatcher (logs: %s)...", log_file)

    advapi32 = ctypes.windll.advapi32
    advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
        wintypes.LPCWSTR,
        HANDLER_FUNCTION_EX,
        wintypes.LPVOID,
    ]
    advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE

    advapi32.SetServiceStatus.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(SERVICE_STATUS),
    ]
    advapi32.SetServiceStatus.restype = wintypes.BOOL

    advapi32.StartServiceCtrlDispatcherW.argtypes = [
        ctypes.POINTER(SERVICE_TABLE_ENTRYW)
    ]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    def service_handler(
        dw_control: int, dw_event_type: int, lp_event_data: int, lp_context: int
    ) -> int:
        if dw_control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            log.info("Received stop/shutdown signal from Windows Service Control Manager.")
            _set_status(SERVICE_STOP_PENDING, wait_hint=5000)
            _stop_event.set()
            return NO_ERROR
        if dw_control == SERVICE_CONTROL_INTERROGATE:
            return NO_ERROR
        return ERROR_CALL_NOT_IMPLEMENTED

    _c_handler_func = HANDLER_FUNCTION_EX(service_handler)

    def service_main(argc: int, argv: ctypes.POINTER(wintypes.LPWSTR)) -> None:
        global _h_status
        _h_status = advapi32.RegisterServiceCtrlHandlerExW(
            service_name, _c_handler_func, None
        )
        if not _h_status:
            log.error("RegisterServiceCtrlHandlerExW failed.")
            return

        _set_status(SERVICE_START_PENDING, wait_hint=3000)
        _set_status(SERVICE_RUNNING)
        log.info("Shango Windows Service is active and reported RUNNING to SCM.")

        try:
            asyncio.run(run_coro_fn(_stop_event))
        except Exception:
            log.exception("Exception in Windows service worker loop")
        finally:
            log.info("Shango Windows Service terminated; reporting STOPPED to SCM.")
            _set_status(SERVICE_STOPPED)

    _c_main_func = SERVICE_MAIN_FUNCTION(service_main)

    dispatch_table = (SERVICE_TABLE_ENTRYW * 2)(
        SERVICE_TABLE_ENTRYW(service_name, _c_main_func),
        SERVICE_TABLE_ENTRYW(None, ctypes.cast(None, SERVICE_MAIN_FUNCTION)),
    )

    if not advapi32.StartServiceCtrlDispatcherW(dispatch_table):
        err = ctypes.GetLastError()
        log.error("StartServiceCtrlDispatcherW failed with Win32 error %d.", err)
        if err == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
            print(
                "[ERROR] This program was invoked with --service, but it was not started\n"
                "by the Windows Service Control Manager (SCM).\n"
                "\n"
                "  * To run interactively: omit the --service flag.\n"
                "  * To install as a service: run install-service.ps1 as Administrator.\n",
                file=sys.stderr,
            )
            sys.exit(1)
        sys.exit(err)
