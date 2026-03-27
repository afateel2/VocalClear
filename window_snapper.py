"""
Winamp-style sticky windows for VocalClear — horizontal snapping only.

Windows snap flush on their left/right edges (no visual gap).  An optional
*anchor* window restricts snapping so only pairs that involve the anchor are
formed (e.g. settings — main — soundboard, never settings — soundboard).

Flush positioning
─────────────────
GetWindowRect includes the invisible DWM resize/shadow border (~7-8 px on
left/right/bottom, 0 on top).  We use DwmGetWindowAttribute(
DWMWA_EXTENDED_FRAME_BOUNDS) to find the *visible* rect so that two snapped
windows touch with zero visual gap.

Threading model
───────────────
Each window lives on its own thread with its own tkinter mainloop.
  • Position reading: Win32 GetWindowRect  (callable from any thread).
  • Position polling: root.after() loop    (runs on that window's own thread).
  • Companion move:   Win32 SetWindowPos   (thread-safe kernel call).
  • State:            protected by a threading.Lock.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from typing import Optional

_GA_ROOT                    = 2    # GetAncestor → top-level frame
_DWMWA_EXTENDED_FRAME_BOUNDS = 9   # visible client area rect

_SWP_NOSIZE     = 0x0001
_SWP_NOZORDER   = 0x0004
_SWP_NOACTIVATE = 0x0010


# ─────────────────────────────────────────────────────────────────────────────
# Low-level Win32 helpers
# ─────────────────────────────────────────────────────────────────────────────

def _frame_hwnd(root) -> int:
    """Return the outer frame HWND for a tk.Tk() window."""
    inner = root.winfo_id()
    hwnd  = ctypes.windll.user32.GetAncestor(inner, _GA_ROOT)
    return hwnd if hwnd else inner


def _get_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) from GetWindowRect — includes invisible DWM border."""
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def _get_visible_rect(hwnd: int) -> tuple[int, int, int, int]:
    """
    (x, y, w, h) from DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS).
    Returns the *visible* window area, excluding the transparent resize shadow.
    Falls back to _get_rect on error (older Windows / DWM off).
    """
    try:
        r = ctypes.wintypes.RECT()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(r), ctypes.sizeof(r),
        )
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return _get_rect(hwnd)


def _insets(hwnd: int) -> tuple[int, int]:
    """
    (left_inset, right_inset): invisible border widths in pixels.
    left_inset  = visible_x - outer_x
    right_inset = (outer_x + outer_w) - (visible_x + visible_w)
    """
    ox, oy, ow, oh = _get_rect(hwnd)
    vx, vy, vw, vh = _get_visible_rect(hwnd)
    return (vx - ox, (ox + ow) - (vx + vw))


def _set_pos(hwnd: int, x: int, y: int) -> None:
    ctypes.windll.user32.SetWindowPos(
        hwnd, None, x, y, 0, 0,
        _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
    )


# ─────────────────────────────────────────────────────────────────────────────

class SnapManager:
    """
    Register Tk windows; they snap flush on left/right edges when dragged
    within SNAP_DIST pixels.  All connected windows move as a chain.

    Snap record: (name_a, name_b, off_x, off_y)
        pos_b = pos_a + (off_x, off_y)   [in GetWindowRect coordinates]

    Flush offset:
        off_x = a_outer_width - a_right_inset - b_left_inset
    so that b's visible left edge aligns exactly with a's visible right edge.
    """

    SNAP_DIST   = 18   # px — visible-edge proximity to trigger snap
    UNSNAP_DIST = 40   # px — drag distance to break snap
    POLL_MS     = 25   # ms — position check interval

    def __init__(self) -> None:
        self._wins:       dict[str, dict]                 = {}
        self._snaps:      list[tuple[str, str, int, int]] = []
        self._anchor:     Optional[str]                   = None
        self._snap_sides: dict[str, str]                  = {}
        self._lock        = threading.Lock()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def set_anchor(self, name: str) -> None:
        """
        Restrict snap formation to pairs that include this window.
        (Other windows can still snap to each other IF the anchor is already
        in the chain — but direct non-anchor pairs are blocked.)
        """
        self._anchor = name

    def register(self, name: str, root, snap_side: str = "any") -> None:
        """
        Register a visible tk.Tk() window.  Call from inside a root.after()
        callback so the window is fully mapped and winfo_id() is valid.

        snap_side:
          "any"        — window may snap on either side of a partner  (default)
          "left-only"  — window may only be the LEFT member of a snap pair
                         (e.g. soundboard always sits to the left of main)
          "right-only" — window may only be the RIGHT member of a snap pair
                         (e.g. settings always sits to the right of main)
        """
        hwnd = _frame_hwnd(root)
        rect = _get_rect(hwnd)
        li, ri = _insets(hwnd)
        with self._lock:
            self._wins[name] = {
                "hwnd":       hwnd,
                "root":       root,
                "last_pos":   rect,
                "prog_pos":   None,
                "left_inset": li,
                "right_inset": ri,
            }
            self._snap_sides[name] = snap_side
        root.after(self.POLL_MS, lambda n=name: self._poll(n))

    def unregister(self, name: str) -> None:
        with self._lock:
            self._wins.pop(name, None)
            self._snaps = [s for s in self._snaps if name not in (s[0], s[1])]

    def get_snap_sides(self, name: str) -> tuple[bool, bool]:
        """
        Return (left_snapped, right_snapped) for window `name`.

        A snap record (na, nb, off_x, 0) with off_x > 0 means nb is to the
        RIGHT of na:
          • na  → right side connected
          • nb  → left  side connected
        """
        with self._lock:
            snaps = list(self._snaps)
        left = right = False
        for na, nb, off_x, off_y in snaps:
            if off_x <= 0:
                continue
            if na == name:
                right = True
            elif nb == name:
                left = True
        return left, right

    # ── Default positioning ──────────────────────────────────────────────────

    def position_right_of(self, parent_name: str, child_root) -> None:
        """Position child_root immediately to the right of the named window."""
        with self._lock:
            info = self._wins.get(parent_name)
        if info:
            px, py, pw, ph = info["last_pos"]
            p_li, p_ri = info["left_inset"], info["right_inset"]
            # Align child's visible left with parent's visible right (flush)
            c_li, c_ri = _insets(_frame_hwnd(child_root))
            target_x = px + pw - p_ri - c_li
            child_root.geometry(f"+{target_x}+{py}")

    def position_left_of(self, parent_name: str, child_root) -> None:
        """Position child_root immediately to the left of the named window."""
        with self._lock:
            info = self._wins.get(parent_name)
        if info:
            px, py, pw, ph = info["last_pos"]
            p_li, p_ri = info["left_inset"], info["right_inset"]
            cw = child_root.winfo_reqwidth() or pw
            c_li, c_ri = _insets(_frame_hwnd(child_root))
            # Align child's visible right with parent's visible left (flush)
            target_x = px + p_li - (cw - c_ri)
            child_root.geometry(f"+{target_x}+{py}")

    def position_below(self, parent_name: str, child_root) -> None:
        """Position child_root immediately below the named window (legacy)."""
        with self._lock:
            info = self._wins.get(parent_name)
        if info:
            px, py, pw, ph = info["last_pos"]
            child_root.geometry(f"+{px}+{py + ph}")

    def is_snapped(self) -> bool:
        with self._lock:
            return len(self._snaps) > 0

    # ─────────────────────────────────────────────────────────────────────────
    # Polling (runs on each window's own mainloop thread)
    # ─────────────────────────────────────────────────────────────────────────

    def _poll(self, name: str) -> None:
        with self._lock:
            info = self._wins.get(name)
        if info is None:
            return

        try:
            cur  = _get_rect(info["hwnd"])
            last = info["last_pos"]

            if cur[0] != last[0] or cur[1] != last[1]:
                prog = info["prog_pos"]
                was_prog = (
                    prog is not None
                    and abs(cur[0] - prog[0]) <= 6
                    and abs(cur[1] - prog[1]) <= 6
                )
                with self._lock:
                    info["last_pos"] = cur
                    if was_prog:
                        info["prog_pos"] = None
                if not was_prog:
                    self._on_user_move(name, cur)
        except Exception:
            pass

        try:
            info["root"].after(self.POLL_MS, lambda n=name: self._poll(n))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Snap / unsnap logic
    # ─────────────────────────────────────────────────────────────────────────

    def _on_user_move(self, name: str, rect: tuple) -> None:
        x, y, w, h = rect

        # ── Anchor moves freely — drag all connected companions and return ──
        if name == self._anchor:
            self._propagate_move(name, x, y)
            return

        # ── Check for snaps to break (follower drifted too far from anchor) ─
        new_snaps = []
        with self._lock:
            snaps = list(self._snaps)
            wins  = {n: dict(i) for n, i in self._wins.items()}

        for snap in snaps:
            na, nb, off_x, off_y = snap
            if nb == name:
                # name is the follower — check it didn't drift from anchor
                a_info = wins.get(na)
                if a_info:
                    ax, ay, _, _ = a_info["last_pos"]
                    dx    = x - (ax + off_x)
                    dy    = y - (ay + off_y)
                    drift = (dx * dx + dy * dy) ** 0.5
                    if drift > self.UNSNAP_DIST:
                        continue   # drop this snap
            elif na == name:
                # name is the anchor in this pair — check it didn't drift
                # from where the follower expects it to be
                b_info = wins.get(nb)
                if b_info:
                    bx, by, _, _ = b_info["last_pos"]
                    dx    = x - (bx - off_x)
                    dy    = y - (by - off_y)
                    drift = (dx * dx + dy * dy) ** 0.5
                    if drift > self.UNSNAP_DIST:
                        continue   # drop this snap
            new_snaps.append(snap)

        with self._lock:
            self._snaps = new_snaps

        # ── Try to form a new snap ──────────────────────────────────────────
        with self._lock:
            already_snapped = any(name in (s[0], s[1]) for s in self._snaps)

        if not already_snapped:
            self._try_snap(name, x, y, w, h)

        # ── Propagate movement to all connected windows ─────────────────────
        # Re-read position: _try_snap may have snapped the window to a new spot
        with self._lock:
            info = self._wins.get(name)
        if info:
            x, y = info["last_pos"][:2]
        self._propagate_move(name, x, y)

    def _try_snap(self, name: str, x: int, y: int, w: int, h: int) -> None:
        """Detect left/right visible-edge proximity and form a flush snap."""
        with self._lock:
            name_info = self._wins.get(name)
            others    = {n: dict(i) for n, i in self._wins.items() if n != name}
        if name_info is None:
            return

        n_li = name_info["left_inset"]
        n_ri = name_info["right_inset"]
        # Visible extents of the moving window
        vx_left  = x + n_li                # visible left  (screen coords)
        vx_right = x + w - n_ri            # visible right (screen coords)

        v_tol = self.SNAP_DIST * 4         # generous vertical tolerance

        for other_name, oinfo in others.items():
            # Anchor restriction: at least one side must be the anchor
            if self._anchor and self._anchor not in (name, other_name):
                continue

            with self._lock:
                already = any(
                    (s[0] == name and s[1] == other_name) or
                    (s[1] == name and s[0] == other_name)
                    for s in self._snaps
                )
            if already:
                continue

            ox, oy, ow, oh = oinfo["last_pos"]
            o_li = oinfo["left_inset"]
            o_ri = oinfo["right_inset"]
            o_vx_left  = ox + o_li
            o_vx_right = ox + ow - o_ri

            n_side = self._snap_sides.get(name,       "any")
            o_side = self._snap_sides.get(other_name, "any")

            # Drag `name` so its visible LEFT is near other's visible RIGHT
            # → snap = (other, name) — other is LEFT (na), name is RIGHT (nb)
            if abs(vx_left - o_vx_right) <= self.SNAP_DIST and abs(y - oy) <= v_tol:
                name_ok  = n_side in ("right-only", "any")
                other_ok = o_side in ("left-only",  "any")
                if name_ok and other_ok:
                    off_x    = ow - o_ri - n_li
                    target_x = ox + off_x
                    with self._lock:
                        self._snaps.append((other_name, name, off_x, 0))
                    self._move_companion(name, target_x, oy)
                    return

            # Drag `name` so its visible RIGHT is near other's visible LEFT
            # → snap = (name, other) — name is LEFT (na), other is RIGHT (nb)
            if abs(vx_right - o_vx_left) <= self.SNAP_DIST and abs(y - oy) <= v_tol:
                name_ok  = n_side in ("left-only",  "any")
                other_ok = o_side in ("right-only", "any")
                if name_ok and other_ok:
                    off_x    = w - n_ri - o_li
                    target_x = ox - off_x
                    with self._lock:
                        self._snaps.append((name, other_name, off_x, 0))
                    self._move_companion(name, target_x, oy)
                    return

    def _propagate_move(self, moved_name: str, new_x: int, new_y: int) -> None:
        """BFS from moved_name through snap connections, moving all companions."""
        with self._lock:
            snaps = list(self._snaps)

        positions: dict[str, tuple[int, int]] = {moved_name: (new_x, new_y)}
        queue:     list[str]                  = [moved_name]
        visited:   set[str]                   = {moved_name}

        while queue:
            cur    = queue.pop(0)
            cx, cy = positions[cur]

            for na, nb, off_x, off_y in snaps:
                if na == cur and nb not in visited:
                    positions[nb] = (cx + off_x, cy + off_y)
                    visited.add(nb)
                    queue.append(nb)
                elif nb == cur and na not in visited:
                    positions[na] = (cx - off_x, cy - off_y)
                    visited.add(na)
                    queue.append(na)

        for n, (tx, ty) in positions.items():
            if n != moved_name:
                self._move_companion(n, tx, ty)

    def _move_companion(self, name: str, tx: int, ty: int) -> None:
        with self._lock:
            info = self._wins.get(name)
        if info is None:
            return
        info["prog_pos"] = (tx, ty)
        _set_pos(info["hwnd"], tx, ty)
        with self._lock:
            lp = info["last_pos"]
            info["last_pos"] = (tx, ty, lp[2], lp[3])
