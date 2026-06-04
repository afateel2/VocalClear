"""
Shared UI helpers for VocalClear's PySide6 UI layer.
"""

_DARK_DIALOG_QSS = (
    "QMessageBox { background: #030603; color: #c8ffd4; }"
    "QPushButton { background: #007a40; color: #030603; padding: 4px 14px;"
    "  border: none; font-family: Consolas; }"
    "QPushButton:hover { background: #00e676; }"
)


def style_dialog(widget) -> None:
    """Apply the standard VocalClear dark theme to a QMessageBox or QDialog."""
    widget.setStyleSheet(_DARK_DIALOG_QSS)
