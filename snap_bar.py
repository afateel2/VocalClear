"""
SnapBar — PCB edge-connector style sticky-window indicator.

Each bar runs the full height of its window as a narrow Canvas overlay.
It draws a central trace line with evenly-spaced solder-pad squares — like
an exposed PCB edge connector.  When a companion window snaps to this side
the trace and pads light up bright green.

Because all windows share the same height the pad positions are identical on
every bar.  When two windows snap flush, the pads on the facing bars line up
across the join — exactly like connector pins mating.

            unattached          snapped
   ┆ ·  ·  ┆            ┆ ●  ● ┆
   ┆ │  │  ┆            ┆ ║  ║ ┆
   ┆ ·  ·  ┆            ┆ ●  ● ┆
   ┆ │  │  ┆            ┆ ║  ║ ┆
   ┆ ·  ·  ┆            ┆ ●  ● ┆
 left-bar  right-bar   (lit up when snapped)
"""

import tkinter as tk

# Palette — matches the three window files
_BG       = "#030603"   # BG_ROOT  (bar background, blends with window edge)
_DIM      = "#004d28"   # GREEN_LO (inactive trace + pads)
_BRIGHT   = "#00e676"   # GREEN    (active trace + pads)
_PAD_GAP  = 18          # pixels between pad centres
_PAD_R    = 2           # half-size of each pad square  (draws 5×5 px box)
BAR_WIDTH = 8           # total canvas width in pixels


class SnapBar(tk.Canvas):
    """
    Decorative PCB-trace connector bar.

    Usage
    ─────
        bar = SnapBar(root)
        bar.place(x=0, y=0, width=BAR_WIDTH, relheight=1.0)   # left edge

        bar.set_active(True)    # lights up (called from _tick loop)
        bar.set_active(False)   # dims

    The bar never captures clicks — cursor stays default so it does not
    interfere with window dragging.
    """

    def __init__(self, parent, **kw):
        super().__init__(
            parent,
            width=BAR_WIDTH,
            highlightthickness=0,
            bg=_BG,
            cursor="arrow",
            **kw,
        )
        self._active = False
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        h  = self.winfo_height() or 410
        w  = self.winfo_width()  or BAR_WIDTH
        cx = w // 2

        col = _BRIGHT if self._active else _DIM

        # ── Central trace ─────────────────────────────────────────────────
        # Draw as a sequence of short dashes to give a subtler, less
        # "wall-like" feel than a solid line — the pads visually complete it.
        dash_on, dash_off = 3, 3
        y = 0
        while y < h:
            y1 = min(y + dash_on, h)
            self.create_line(cx, y, cx, y1, fill=col, width=1)
            y += dash_on + dash_off

        # ── Solder pads at regular intervals ──────────────────────────────
        y = _PAD_GAP
        while y <= h - _PAD_GAP:
            self.create_rectangle(
                cx - _PAD_R, y - _PAD_R,
                cx + _PAD_R, y + _PAD_R,
                fill=col, outline="",
            )
            y += _PAD_GAP

        # ── Cap marks at top and bottom ───────────────────────────────────
        for cap_y in (6, h - 6):
            self.create_rectangle(
                cx - _PAD_R, cap_y - _PAD_R,
                cx + _PAD_R, cap_y + _PAD_R,
                fill=col, outline="",
            )
