"""
Persistent JSON configuration stored at %USERPROFILE%\.vocalclear\config.json
"""

import json
import logging
import os
import winreg
import sys
from pathlib import Path

log = logging.getLogger("vocalclear")

CONFIG_DIR = Path.home() / ".vocalclear"
CONFIG_FILE = CONFIG_DIR / "config.json"

MAIN_SCRIPT = Path(__file__).resolve().parent / "main.py"

DEFAULTS = {
    "input_device": None,      # None = system default
    "output_device": None,     # None = auto-detect VB-CABLE, else system default
    "strength": 0.50,          # Noise reduction strength 0.0–1.0
    "enabled": True,
    "start_with_windows": False,
    "sample_rate": 48000,
    "block_size": 480,         # 10 ms at 48 kHz — matches RNNoise frame exactly
    "wasapi_exclusive": False,  # Exclusive WASAPI mode: ~3 ms vs ~20 ms shared
    "output_gain": 1.0,        # Output gain multiplier 0.5×–3.0× applied before VB-CABLE
    "ptt_enabled": False,      # Push-to-talk: mic only active while ptt_vk is held
    "ptt_key": "",             # Human-readable PTT key label (e.g. "F4")
    "ptt_vk": 0,               # Win32 virtual-key code for PTT key
}


class Config:
    def __init__(self):
        self._data: dict = DEFAULTS.copy()
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update({k: v for k, v in saved.items() if k in DEFAULTS})
            except Exception as e:
                log.warning("config.json is corrupt (%s) — defaults loaded", e)
                # Back up the corrupt file so the user can inspect it
                bad = CONFIG_FILE.with_suffix(".json.bad")
                try:
                    CONFIG_FILE.replace(bad)
                    log.warning("Corrupt config saved to %s", bad)
                except Exception:
                    pass
        # Migrate: reset old large block_size values to the low-latency default
        bs = self._data.get("block_size", 0)
        if bs > 480 or bs < 64:
            self._data["block_size"] = 480

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)   # atomic on Windows NTFS
        except Exception as e:
            log.error("Failed to save config: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Dict-like access
    # ------------------------------------------------------------------

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    # ------------------------------------------------------------------
    # Windows startup registry
    # ------------------------------------------------------------------

    _REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    _REG_NAME = "VocalClear"

    def set_startup(self, enabled: bool) -> None:
        """Add or remove VocalClear from Windows startup."""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._REG_KEY,
                0,
                winreg.KEY_SET_VALUE,
            )
            if enabled:
                cmd = f'"{sys.executable}" "{MAIN_SCRIPT}"'
                winreg.SetValueEx(key, self._REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, self._REG_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            self["start_with_windows"] = enabled
        except Exception as e:
            print(f"[Config] Failed to update startup registry: {e}")

    def is_startup_enabled(self) -> bool:
        """Check whether the registry entry exists."""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._REG_KEY,
                0,
                winreg.KEY_READ,
            )
            winreg.QueryValueEx(key, self._REG_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False
