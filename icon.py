"""
VocalClear icon generator — oscilloscope / neon-green aesthetic.

Generates PIL images used for:
  - The system tray icon (active = neon green, inactive = dim gray)
  - The .ico file embedded in the desktop shortcut (multi-size)

Design:
  - Near-black background with rounded corners
  - Subtle green grid
  - 7 equalizer-style bars following a bell-curve height profile
  - Bright top cap on each bar
  - Thin green border frame
"""

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw

# ── Palette ───────────────────────────────────────────────────────────────────
_BG          = (8,  12,  8, 255)      # near-black, green-tinted
_BORDER      = (0,  60,  30, 255)     # very dim green border
_GRID        = (12, 30,  12, 255)     # barely-visible grid
_GREEN       = (0,  230, 118, 255)    # #00e676 — neon green bars
_GREEN_CAP   = (180, 255, 210, 255)   # bright top cap
_GREEN_FLOOR = (0,  80,  40, 180)     # faint floor line
_GRAY        = (75,  85,  75, 255)    # inactive bars
_GRAY_CAP    = (110, 120, 110, 255)   # inactive top cap
_GRAY_FLOOR  = (40,  50,  40, 180)   # inactive floor

# Normalised bar heights — bell-curve profile (centre bar tallest)
_BAR_PROFILE_7 = [0.30, 0.55, 0.78, 1.00, 0.78, 0.55, 0.30]
_BAR_PROFILE_5 = [0.38, 0.68, 1.00, 0.68, 0.38]


def draw_icon(size: int, active: bool = True) -> Image.Image:
    """
    Draw a VocalClear icon at the given pixel size.

    Args:
        size:   Square canvas size in pixels (e.g. 16, 32, 48, 64, 256).
        active: True → neon green (running), False → dim gray (paused).
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    radius = max(2, size // 7)

    # ── Background ────────────────────────────────────────────────────────────
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=_BG)

    # ── Border ───────────────────────────────────────────────────────────────
    d.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius,
        outline=_BORDER, width=max(1, size // 32),
    )

    # ── Grid (only at sizes ≥ 32) ─────────────────────────────────────────────
    if size >= 32:
        n = 4
        for i in range(1, n):
            v = (size * i) // n
            d.line([(2, v), (size - 3, v)], fill=_GRID, width=1)
            d.line([(v, 2), (v, size - 3)], fill=_GRID, width=1)

    # ── Floor line ────────────────────────────────────────────────────────────
    margin_side = max(3, size // 8)
    margin_top  = max(3, size // 7)
    floor_y     = size - margin_side
    floor_col   = _GREEN_FLOOR if active else _GRAY_FLOOR
    d.line([(margin_side, floor_y), (size - margin_side, floor_y)],
           fill=floor_col, width=max(1, size // 48))

    # ── Equalizer bars ────────────────────────────────────────────────────────
    profile    = _BAR_PROFILE_5 if size < 32 else _BAR_PROFILE_7
    n_bars     = len(profile)
    bar_col    = _GREEN     if active else _GRAY
    cap_col    = _GREEN_CAP if active else _GRAY_CAP

    avail_w  = size - 2 * margin_side
    gap      = max(1, avail_w // (n_bars * 3))          # ~1/3 of bar width
    bar_w    = max(1, (avail_w - (n_bars - 1) * gap) // n_bars)
    total_w  = n_bars * bar_w + (n_bars - 1) * gap
    start_x  = margin_side + (avail_w - total_w) // 2

    max_h    = floor_y - margin_top
    cap_h    = max(1, size // 24)

    for i, h_norm in enumerate(profile):
        bar_h = max(cap_h + 1, int(max_h * h_norm))
        x0    = start_x + i * (bar_w + gap)
        x1    = x0 + bar_w - 1
        y1    = floor_y - 1
        y0    = y1 - bar_h + 1

        # Main bar (slightly dimmer at bottom, full colour at top)
        d.rectangle([x0, y0 + cap_h, x1, y1], fill=bar_col)
        # Bright cap
        d.rectangle([x0, y0, x1, y0 + cap_h - 1], fill=cap_col)

    return img


def make_ico(dest: Path) -> Path:
    """
    Generate a multi-resolution .ico file at *dest* and return the path.
    Sizes included: 16, 32, 48, 64, 128, 256
    """
    sizes   = [16, 32, 48, 64, 128, 256]
    images  = [draw_icon(s, active=True) for s in sizes]
    # ico format: save largest first, list the rest as append_images
    images[0].save(
        dest,
        format   = "ICO",
        sizes    = [(s, s) for s in sizes],
        append_images = images[1:],
    )
    return dest


# ── CLI helper: run directly to regenerate the icon ──────────────────────────
if __name__ == "__main__":
    out = Path(__file__).parent / "vocalclear.ico"
    make_ico(out)
    print(f"Icon written to {out}")

    # Quick visual check — open the 256-px version
    try:
        draw_icon(256, active=True).show()
    except Exception:
        pass
