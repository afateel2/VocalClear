"""
Global hotkey manager for VocalClear — GetAsyncKeyState polling approach.

Uses the same mechanism as VocalClear's PTT detection: a background thread
polls GetAsyncKeyState every 15 ms.  This avoids RegisterHotKey / GetMessageW /
PostThreadMessageW entirely, which eliminates the Win32 thread-ID-reuse bug
that caused AppHangB1 when adding or removing soundboard hotkeys.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import threading
import time
from typing import Callable, Dict, Optional

# ── Win32 modifier flags (used only by parse_hotkey / format_display) ──────────
MOD_ALT      = 0x0001
MOD_CTRL     = 0x0002
MOD_SHIFT    = 0x0004
MOD_WIN      = 0x0008
MOD_NOREPEAT = 0x4000   # kept for parse_hotkey compatibility

# ── Virtual key table ──────────────────────────────────────────────────────────
_VK: dict[str, int] = {
    # Function keys
    "f1": 0x70, "f2": 0x71, "f3": 0x72,  "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76,  "f8": 0x77,
    "f9": 0x78, "f10":0x79, "f11":0x7A,  "f12":0x7B,
    # Letters
    "a":0x41,"b":0x42,"c":0x43,"d":0x44,"e":0x45,"f":0x46,"g":0x47,
    "h":0x48,"i":0x49,"j":0x4A,"k":0x4B,"l":0x4C,"m":0x4D,"n":0x4E,
    "o":0x4F,"p":0x50,"q":0x51,"r":0x52,"s":0x53,"t":0x54,"u":0x55,
    "v":0x56,"w":0x57,"x":0x58,"y":0x59,"z":0x5A,
    # Digit row
    "0":0x30,"1":0x31,"2":0x32,"3":0x33,"4":0x34,
    "5":0x35,"6":0x36,"7":0x37,"8":0x38,"9":0x39,
    # Numpad
    "num0":0x60,"num1":0x61,"num2":0x62,"num3":0x63,"num4":0x64,
    "num5":0x65,"num6":0x66,"num7":0x67,"num8":0x68,"num9":0x69,
    # Navigation / editing
    "insert":0x2D,"delete":0x2E,"home":0x24,"end":0x23,
    "prior":0x21,"next":0x22,   # Page Up / Page Down
    "left":0x25,"up":0x26,"right":0x27,"down":0x28,
    # Misc
    "space":0x20,"enter":0x0D,"tab":0x09,
    # Punctuation / symbols
    "comma":0xBC,"period":0xBE,"semicolon":0xBA,"slash":0xBF,
    "backslash":0xDC,"lbracket":0xDB,"rbracket":0xDD,
    "quote":0xDE,"grave":0xC0,"minus":0xBD,"equals":0xBB,
}

_MOD_TAGS: dict[str, int] = {
    "ctrl":  MOD_CTRL,
    "alt":   MOD_ALT,
    "shift": MOD_SHIFT,
    "win":   MOD_WIN,
}

# tkinter keysym → internal key name
_TK_KEYSYM: dict[str, str] = {
    "F1":"f1","F2":"f2","F3":"f3","F4":"f4","F5":"f5","F6":"f6",
    "F7":"f7","F8":"f8","F9":"f9","F10":"f10","F11":"f11","F12":"f12",
    "space":"space","Return":"enter","Tab":"tab",
    "Insert":"insert","Delete":"delete","Home":"home","End":"end",
    "Prior":"prior","Next":"next",
    "Left":"left","Up":"up","Right":"right","Down":"down",
}

# tkinter modifier state bit masks (Windows)
_TK_CTRL  = 0x0004
_TK_SHIFT = 0x0001
_TK_ALT   = 0x0008


# ── Modifier VK codes for GetAsyncKeyState polling ─────────────────────────────
_MOD_VK: dict[int, list[int]] = {
    MOD_CTRL:  [0x11],          # VK_CONTROL
    MOD_ALT:   [0x12],          # VK_MENU
    MOD_SHIFT: [0x10],          # VK_SHIFT
    MOD_WIN:   [0x5B, 0x5C],    # VK_LWIN, VK_RWIN
}

_POLL_INTERVAL = 0.015          # 15 ms — same order of magnitude as PTT


# ── Public helpers ─────────────────────────────────────────────────────────────

def parse_hotkey(hk: str) -> Optional[tuple[int, int]]:
    """
    Parse a hotkey string → (vk_code, modifier_flags), or None if unrecognised.

    Syntax: 'f1'  '<ctrl>f1'  '<ctrl><shift>f1'  '<alt>a'  etc.
    """
    hk = hk.strip().lower()
    mods = MOD_NOREPEAT
    for tag in re.findall(r"<([^>]+)>", hk):
        m = _MOD_TAGS.get(tag)
        if m:
            mods |= m
    key = re.sub(r"<[^>]+>", "", hk).strip()
    vk  = _VK.get(key)
    return (vk, mods) if vk is not None else None


def format_display(hk: str) -> str:
    """
    Convert internal hotkey string to a human-readable label.
    '<ctrl><shift>f1' → 'CTRL+SHIFT+F1'
    """
    if not hk:
        return ""
    mods = re.findall(r"<([^>]+)>", hk)
    key  = re.sub(r"<[^>]+>", "", hk).strip()
    parts = [m.upper() for m in mods] + [key.upper()]
    return "+".join(parts)


def tk_event_to_hotkey(event) -> Optional[str]:
    """
    Convert a tkinter <KeyPress> event to an internal hotkey string.

    Returns '__escape__'   if the user pressed Escape (cancel signal).
    Returns '__clear__'    if the user pressed Backspace / Delete (clear signal).
    Returns None           if the key cannot be used as a hotkey.
    Returns a hotkey str   otherwise, e.g. 'f1' or '<ctrl>a'.
    """
    ks = event.keysym

    # Pure-modifier keystrokes are not hotkeys by themselves
    if ks in ("Shift_L","Shift_R","Control_L","Control_R","Alt_L","Alt_R",
              "Super_L","Super_R","Caps_Lock","Num_Lock","Scroll_Lock"):
        return None

    if ks == "Escape":
        return "__escape__"
    if ks in ("BackSpace", "Delete"):
        return "__clear__"

    # Map keysym → key name
    key = _TK_KEYSYM.get(ks)
    if key is None:
        if len(ks) == 1 and (ks.isalpha() or ks.isdigit()):
            key = ks.lower()
        else:
            return None

    if key not in _VK:
        return None

    state = event.state
    ctrl  = bool(state & _TK_CTRL)
    shift = bool(state & _TK_SHIFT)
    alt   = bool(state & _TK_ALT)

    # Bare letter/digit requires at least one modifier (too collision-prone without)
    is_fn = key.startswith("f") and key[1:].isdigit()
    if not is_fn and not ctrl and not alt:
        return None

    parts = []
    if ctrl:  parts.append("<ctrl>")
    if shift: parts.append("<shift>")
    if alt:   parts.append("<alt>")
    parts.append(key)
    return "".join(parts)


# ── Main class ─────────────────────────────────────────────────────────────────

class HotkeyManager:
    """
    Global hotkey manager using GetAsyncKeyState polling.

    Mirrors the PTT detection approach used by AudioEngine._ptt_loop: a daemon
    thread polls every 15 ms with GetAsyncKeyState, detects down-edges on the
    main key while all required modifiers are held, and fires callbacks.

    This avoids RegisterHotKey / GetMessageW / PostThreadMessageW entirely.
    Those Win32 APIs require a per-thread message queue and are stopped via
    PostThreadMessageW(tid, WM_QUIT), which can silently deliver WM_QUIT to the
    wrong thread if Windows reuses the TID — causing AppHangB1.

    Usage::

        mgr = HotkeyManager()
        mgr.set_hotkeys({"f1": play_kick, "<ctrl>f2": play_horn})
        # ... later ...
        mgr.stop()
    """

    def __init__(self) -> None:
        self._thread:    Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._mapping:   Dict[str, Callable] = {}
        self._lock       = threading.Lock()

    # ── Public ────────────────────────────────────────────────────────────────

    def set_hotkeys(self, mapping: Dict[str, Callable]) -> None:
        """Replace the active hotkey set (stops previous listener first)."""
        with self._lock:
            self._stop_locked()
            self._mapping = {hk: cb for hk, cb in mapping.items()
                             if parse_hotkey(hk) is not None}
            if not self._mapping:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, args=(self._stop_event,),
                daemon=True, name="VocalClear-hotkeys")
            self._thread.start()

    def stop(self) -> None:
        """Stop the hotkey listener."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """Signal the polling thread to exit and wait briefly for it."""
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=0.1)

    # ── Private ───────────────────────────────────────────────────────────────

    def _run(self, stop_event: threading.Event) -> None:
        """
        Poll GetAsyncKeyState for each registered combination.

        The 0x0001 bit of GetAsyncKeyState(vk) is set — and then cleared — the
        first time it is read after the key transitioned to pressed.  This gives
        a clean down-edge detection without manual state tracking, identical to
        how the soundboard hotkey capture popup detects key presses.
        """
        GAS = ctypes.windll.user32.GetAsyncKeyState

        # Pre-compute (modifier_vk_list, main_vk, callback) tuples once.
        specs: list[tuple[list[int], int, Callable]] = []
        for hk_str, cb in self._mapping.items():
            parsed = parse_hotkey(hk_str)
            if not parsed:
                continue
            vk, mods = parsed
            mod_vks: list[int] = []
            for flag, vks in _MOD_VK.items():
                if mods & flag:
                    mod_vks.extend(vks)
            specs.append((mod_vks, vk, cb))

        while not stop_event.is_set():
            for mod_vks, vk, cb in specs:
                # All modifier keys must be held down
                if not all(GAS(m) & 0x8000 for m in mod_vks):
                    continue
                # Main key: 0x0001 = pressed since last GAS call (auto-cleared)
                if GAS(vk) & 0x0001:
                    threading.Thread(target=cb, daemon=True).start()
            time.sleep(_POLL_INTERVAL)
