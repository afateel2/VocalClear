"""
Shared UI helpers for VocalClear's PySide6 UI layer.
"""

import ctypes
import ctypes.wintypes as _wt

_DARK_DIALOG_QSS = (
    "QMessageBox { background: #030603; color: #c8ffd4; }"
    "QPushButton { background: #007a40; color: #030603; padding: 4px 14px;"
    "  border: none; font-family: Consolas; }"
    "QPushButton:hover { background: #00e676; }"
)


def style_dialog(widget) -> None:
    """Apply the standard VocalClear dark theme to a QMessageBox or QDialog."""
    widget.setStyleSheet(_DARK_DIALOG_QSS)


def apply_dark_titlebar(widget) -> None:
    """Turn the native titlebar (caption + min/max/close strip) dark.

    Call once after the window exists AND again from showEvent — DWM can
    ignore/reset the attribute when it is set before the native window has
    ever been composed, which is how a lazily-created window can end up with
    a white titlebar while its siblings are dark.  Attribute 20 is
    DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 20H1+); 19 is the pre-20H1 value.
    """
    try:
        hwnd  = _wt.HWND(int(widget.winId()))
        value = ctypes.c_int(1)
        dwm   = ctypes.windll.dwmapi
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(
                    hwnd, _wt.DWORD(attr),
                    ctypes.byref(value), ctypes.sizeof(value)) == 0:
                return
    except Exception:
        pass
