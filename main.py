"""
VocalClear – Real-time Microphone Noise Suppression
====================================================

Entry point.  Designed to run with pythonw.exe so no console window appears.
The app lives entirely in the system tray.

Usage:
    pythonw.exe main.py          # normal launch (no console)
    python.exe  main.py          # launch with console (useful for debugging)
"""

import sys
import os
import traceback
from pathlib import Path

# Add the project directory to sys.path so sibling modules are importable
# regardless of the working directory at launch time.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Log file — captures silent crashes when running under pythonw.exe
_LOG = Path.home() / ".vocalclear" / "vocalclear.log"


def _log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _ensure_single_instance() -> bool:
    """
    Use a named Windows mutex to prevent multiple instances.
    Returns True if this is the first instance, False otherwise.
    """
    try:
        import ctypes
        import ctypes.wintypes

        CREATE_MUTEX = ctypes.windll.kernel32.CreateMutexW
        mutex = CREATE_MUTEX(None, False, "Global\\VocalClear_SingleInstance")
        last_error = ctypes.windll.kernel32.GetLastError()
        ERROR_ALREADY_EXISTS = 183

        if last_error == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True  # If mutex check fails, let it run


def _set_process_identity() -> None:
    """
    Make VocalClear appear as its own entry in Windows Task Manager.

    - AppUserModelID: Windows groups this process separately from other
      Python processes and associates it with our custom icon.
    - SetConsoleTitle: fills the 'Description' column in Task Manager's
      Details tab when running under python.exe (no-op for pythonw.exe).
    """
    try:
        import ctypes
        # Distinct App User Model ID — Task Manager uses this to group and
        # label the process, and to resolve the icon from the window handle.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VocalClear.NoiseSuppress.1"
        )
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("VocalClear")
    except Exception:
        pass


def main() -> None:
    _set_process_identity()
    _log("VocalClear starting")
    try:
        if not _ensure_single_instance():
            # Another instance is already running; show a brief notice and exit.
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "VocalClear is already running.\n\nLook for the microphone icon in the system tray.",
                    "VocalClear",
                    0x40,  # MB_ICONINFORMATION
                )
            except Exception:
                pass
            sys.exit(0)

        # Lazy import to keep startup fast
        from tray_app import TrayApp
        app = TrayApp()
        app.run()
        _log("VocalClear exited cleanly")

    except Exception:
        err = traceback.format_exc()
        _log(f"CRASH:\n{err}")
        # Show error dialog when running under pythonw.exe (no console)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"VocalClear encountered an error and could not start:\n\n{err}\n\nSee: {_LOG}",
                "VocalClear — Error",
                0x10,  # MB_ICONERROR
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
