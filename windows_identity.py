r"""
Windows process identity for VocalClear source launches.

Problem: Task Manager's name/description column comes from the VERSIONINFO
resource of the *executable file* hosting the process.  Running from source
(`pythonw.exe main.py`) therefore always shows "Python", regardless of window
icon, AppUserModelID, shortcuts, or "proper installation".

Fix: create `VocalClear.exe` — a byte copy of pythonw.exe with its
version-info + icon resources rewritten in place via the Win32 UpdateResource
API.  Launching `VocalClear.exe main.py` is then indistinguishable from
pythonw for Python itself, but Task Manager shows "VocalClear" with our icon.

The launcher is fully self-contained in %USERPROFILE%\.vocalclear\bin\
(the Python install dir usually needs elevation to write to):
    VocalClear.exe      patched copy of pythonw.exe
    python312.dll etc.  runtime DLLs copied beside the exe (loader searches
                        the exe's own directory first)
    VocalClear._pth     bakes the REAL install's sys.path (a `._pth` file
                        beside the exe pins sys.path explicitly — the same
                        mechanism the embeddable distribution uses)
    source.json         where the copies came from, for staleness checks

Run directly to (re)build the launcher and print verification:
    python windows_identity.py

Notes:
  - The copy invalidates python.org's Authenticode signature.  That is fine
    for a locally created file (no Mark-of-the-Web, so no SmartScreen prompt).
  - After a Python upgrade, ensure_launcher() rebuilds automatically (mtime
    comparison against the source pythonw.exe / DLLs); otherwise it's a cheap
    no-op, so main.py calls it best-effort on every start.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import struct
import sys
from pathlib import Path

APP_NAME    = "VocalClear"
APP_VERSION = (1, 0, 0, 0)

LAUNCHER_DIR = Path.home() / ".vocalclear" / "bin"
_SOURCE_META = LAUNCHER_DIR / "source.json"
# DLLs pythonw.exe (or stable-ABI extension modules) resolve from the exe dir
_RUNTIME_DLLS = ("python3*.dll", "vcruntime140.dll", "vcruntime140_1.dll")

_RT_ICON       = 3
_RT_GROUP_ICON = 14
_RT_VERSION    = 16
_LANG_EN_US    = 1033

_LOAD_LIBRARY_AS_DATAFILE = 0x00000002

# Explicit prototypes are mandatory here: without restype, 64-bit HANDLEs
# come back truncated to c_int and UpdateResource fails with error 87.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.BeginUpdateResourceW.restype  = wt.HANDLE
_k32.BeginUpdateResourceW.argtypes = (wt.LPCWSTR, wt.BOOL)
_k32.UpdateResourceW.restype  = wt.BOOL
_k32.UpdateResourceW.argtypes = (wt.HANDLE, wt.LPWSTR, wt.LPWSTR, wt.WORD,
                                 ctypes.c_void_p, wt.DWORD)
_k32.EndUpdateResourceW.restype  = wt.BOOL
_k32.EndUpdateResourceW.argtypes = (wt.HANDLE, wt.BOOL)
_k32.LoadLibraryExW.restype  = wt.HMODULE
_k32.LoadLibraryExW.argtypes = (wt.LPCWSTR, wt.HANDLE, wt.DWORD)
_k32.FreeLibrary.argtypes = (wt.HMODULE,)

_ENUMNAME = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p)
_ENUMLANG = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p,
                               ctypes.c_void_p, wt.WORD, ctypes.c_void_p)
_k32.EnumResourceNamesW.restype  = wt.BOOL
_k32.EnumResourceNamesW.argtypes = (wt.HMODULE, wt.LPWSTR, _ENUMNAME,
                                    ctypes.c_void_p)
_k32.EnumResourceLanguagesW.restype  = wt.BOOL
_k32.EnumResourceLanguagesW.argtypes = (wt.HMODULE, wt.LPWSTR, wt.LPWSTR,
                                        _ENUMLANG, ctypes.c_void_p)


def launcher_path() -> Path:
    return LAUNCHER_DIR / f"{APP_NAME}.exe"


def _source_dir() -> Path:
    """Directory of the real Python install (where pythonw.exe lives).

    When this very process was started via the launcher, sys.base_prefix IS
    the launcher dir (the ._pth pins it there) — fall back to the recorded
    source of the existing build.
    """
    cand = Path(sys.base_prefix)
    if (cand / "pythonw.exe").exists() and cand != LAUNCHER_DIR:
        return cand
    try:
        return Path(json.loads(_SOURCE_META.read_text(encoding="utf-8"))["dir"])
    except Exception:
        raise FileNotFoundError(
            "Cannot locate the source Python install for the launcher")


def _source_files(src_dir: Path) -> list[Path]:
    """pythonw.exe + runtime DLLs that must be copied beside the launcher."""
    files = [src_dir / "pythonw.exe"]
    for pattern in _RUNTIME_DLLS:
        files.extend(sorted(src_dir.glob(pattern)))
    return [f for f in files if f.exists()]


def _is_stale(src_files: list[Path]) -> bool:
    exe = launcher_path()
    if not exe.exists():
        return True
    for f in src_files:
        dst = LAUNCHER_DIR / (exe.name if f.name == "pythonw.exe" else f.name)
        if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime:
            return True
    return False


def _build_pth() -> str:
    """Freeze the real interpreter's sys.path into ._pth lines.

    Only meaningful when running under the real install — a launcher-started
    process would just echo back what the ._pth already says.
    """
    lines = []
    for p in sys.path:
        if p and Path(p).is_dir():
            lines.append(p)
    # pywin32 extends sys.path via a site-packages .pth file; site.main()
    # can't discover site-packages from the launcher prefix, so bake the
    # extra dirs in explicitly if they exist.
    for p in list(lines):
        for extra in ("win32", os.path.join("win32", "lib"), "Pythonwin"):
            e = os.path.join(p, extra)
            if os.path.isdir(e) and e not in lines:
                lines.append(e)
    seen: set[str] = set()
    uniq = [p for p in lines if not (p in seen or seen.add(p))]
    return "\n".join(uniq) + "\nimport site\n"


# ─────────────────────────────────────────────────────────────────────────────
# VS_VERSIONINFO blob construction (the documented resource layout)
# ─────────────────────────────────────────────────────────────────────────────

def _vs_block(key: str, value: bytes = b"", is_text: bool = False,
              children: tuple[bytes, ...] = ()) -> bytes:
    """One {wLength, wValueLength, wType, szKey, pad, Value, pad, children}."""
    key_b = (key + "\0").encode("utf-16-le")
    w_value_len = (len(value) // 2) if is_text else len(value)
    part = struct.pack("<HHH", 0, w_value_len, 1 if is_text else 0) + key_b
    part += b"\0" * (-len(part) % 4)
    part += value
    for child in children:
        part += b"\0" * (-len(part) % 4)
        part += child
    return struct.pack("<H", len(part)) + part[2:]   # backfill wLength


def _vs_string(name: str, val: str) -> bytes:
    return _vs_block(name, (val + "\0").encode("utf-16-le"), is_text=True)


def _build_version_info() -> bytes:
    ver_str = ".".join(map(str, APP_VERSION))
    fixed = struct.pack(
        "<13I",
        0xFEEF04BD,                                   # signature
        0x00010000,                                   # struc version
        (APP_VERSION[0] << 16) | APP_VERSION[1],      # file version MS
        (APP_VERSION[2] << 16) | APP_VERSION[3],      # file version LS
        (APP_VERSION[0] << 16) | APP_VERSION[1],      # product version MS
        (APP_VERSION[2] << 16) | APP_VERSION[3],      # product version LS
        0x3F, 0,                                      # flags mask / flags
        0x00040004,                                   # VOS_NT_WINDOWS32
        0x1, 0,                                       # VFT_APP / subtype
        0, 0,                                         # date
    )
    strings = (
        _vs_string("CompanyName",      APP_NAME),
        _vs_string("FileDescription",  APP_NAME),     # ← Task Manager name
        _vs_string("FileVersion",      ver_str),
        _vs_string("InternalName",     APP_NAME),
        _vs_string("OriginalFilename", f"{APP_NAME}.exe"),
        _vs_string("ProductName",      APP_NAME),
        _vs_string("ProductVersion",   ver_str),
    )
    string_table     = _vs_block("040904b0", is_text=True, children=strings)
    string_file_info = _vs_block("StringFileInfo", is_text=True,
                                 children=(string_table,))
    translation      = _vs_block("Translation",
                                 struct.pack("<HH", 0x0409, 0x04B0))
    var_file_info    = _vs_block("VarFileInfo", is_text=True,
                                 children=(translation,))
    return _vs_block("VS_VERSION_INFO", fixed,
                     children=(string_file_info, var_file_info))


# ─────────────────────────────────────────────────────────────────────────────
# Icon parsing (.ico file → RT_GROUP_ICON blob + RT_ICON payloads)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ico(ico_path: Path, first_icon_id: int) -> tuple[bytes, list[tuple[int, bytes]]]:
    data = ico_path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or ico_type != 1 or count == 0:
        raise ValueError(f"{ico_path} is not a valid .ico file")

    group = struct.pack("<HHH", 0, 1, count)
    icons: list[tuple[int, bytes]] = []
    for i in range(count):
        (w, h, colors, rsvd, planes, bitcount,
         size, offset) = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
        icon_id = first_icon_id + i
        # GRPICONDIRENTRY = ICONDIRENTRY with WORD resource id, no offset
        group += struct.pack("<BBBBHHIH", w, h, colors, rsvd,
                             planes, bitcount, size, icon_id)
        icons.append((icon_id, data[offset:offset + size]))
    return group, icons


# ─────────────────────────────────────────────────────────────────────────────
# Existing-resource inspection (so we replace the right IDs/languages)
# ─────────────────────────────────────────────────────────────────────────────

def _intres(i: int):
    return ctypes.cast(ctypes.c_void_p(i & 0xFFFF), wt.LPWSTR)


def _enum_existing(path: Path) -> tuple[list[int], object, int, int]:
    """Return (existing RT_ICON ids, first RT_GROUP_ICON name, its lang,
    RT_VERSION lang) of the exe at *path*."""
    hmod = _k32.LoadLibraryExW(str(path), None, _LOAD_LIBRARY_AS_DATAFILE)
    if not hmod:
        return [], 1, _LANG_EN_US, _LANG_EN_US

    def _name_value(p):
        # c_void_p callback params arrive as plain int (or None).
        # Integer resource IDs are encoded as pointer values < 0x10000.
        v = p or 0
        return v if v < 0x10000 else ctypes.wstring_at(v)

    icon_ids: list[int] = []
    group_names: list = []
    langs: dict = {}

    def _on_name(_h, _t, name_p, param):
        target = icon_ids if param == 1 else group_names
        target.append(_name_value(name_p))
        return True

    def _on_lang(_h, _t, _n, lang, param):
        langs[param] = lang
        return True

    cb = _ENUMNAME(_on_name)
    _k32.EnumResourceNamesW(hmod, _intres(_RT_ICON), cb, 1)
    _k32.EnumResourceNamesW(hmod, _intres(_RT_GROUP_ICON), cb, 2)

    group_name = group_names[0] if group_names else 1
    lcb = _ENUMLANG(_on_lang)
    gname = _intres(group_name) if isinstance(group_name, int) else group_name
    _k32.EnumResourceLanguagesW(hmod, _intres(_RT_GROUP_ICON), gname, lcb, 10)
    _k32.EnumResourceLanguagesW(hmod, _intres(_RT_VERSION), _intres(1), lcb, 20)
    _k32.FreeLibrary(hmod)

    numeric_ids = [i for i in icon_ids if isinstance(i, int)]
    return (numeric_ids,
            group_name,
            langs.get(10, _LANG_EN_US),
            langs.get(20, _LANG_EN_US))


# ─────────────────────────────────────────────────────────────────────────────
# Resource patching
# ─────────────────────────────────────────────────────────────────────────────

def _patch_resources(exe: Path, ico_path: Path) -> None:
    icon_ids, group_name, group_lang, ver_lang = _enum_existing(exe)
    first_free_id = (max(icon_ids) + 1) if icon_ids else 101
    group_blob, icons = _parse_ico(ico_path, first_free_id)
    version_blob = _build_version_info()

    h = _k32.BeginUpdateResourceW(str(exe), False)
    if not h:
        raise OSError(
            f"BeginUpdateResource failed ({ctypes.get_last_error()})")

    def _upd(rtype: int, name, lang: int, blob: bytes) -> None:
        buf = ctypes.create_string_buffer(blob, len(blob))
        name_p = _intres(name) if isinstance(name, int) else name
        if not _k32.UpdateResourceW(h, _intres(rtype), name_p, lang,
                                    buf, len(blob)):
            raise OSError(f"UpdateResource({rtype}) failed "
                          f"({ctypes.get_last_error()})")

    try:
        _upd(_RT_VERSION, 1, ver_lang, version_blob)
        for icon_id, payload in icons:
            _upd(_RT_ICON, icon_id, group_lang, payload)
        # Replace the FIRST existing icon group — Explorer / Task Manager use
        # the first group in resource-directory order.
        _upd(_RT_GROUP_ICON, group_name, group_lang, group_blob)
    except Exception:
        _k32.EndUpdateResourceW(h, True)   # discard
        raise
    if not _k32.EndUpdateResourceW(h, False):
        raise OSError(
            f"EndUpdateResource failed ({ctypes.get_last_error()})")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def ensure_launcher(ico_path: Path | None = None, force: bool = False) -> Path:
    """Create/refresh the self-contained launcher. Returns its path."""
    src_dir = _source_dir()
    files = _source_files(src_dir)
    if not files or files[0].name != "pythonw.exe":
        raise FileNotFoundError(src_dir / "pythonw.exe")

    dst = launcher_path()
    if not force and not _is_stale(files):
        return dst

    LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)
    if ico_path is None:
        ico_path = Path(__file__).resolve().parent / "vocalclear.ico"

    # Runtime DLLs first (plain copies, replaceable unless loaded — and if the
    # launcher is running, the same-version DLLs are already in place).
    for f in files[1:]:
        try:
            shutil.copy2(f, LAUNCHER_DIR / f.name)
        except PermissionError:
            pass   # in use by a running launcher → already current enough

    # ._pth pins sys.path to the real install.  Only (re)generate when built
    # from the real interpreter — from a launcher process sys.path is just
    # the baked copy.  Written for both lookup names Python honours: next to
    # the exe (VocalClear._pth) and next to the DLL (python312._pth).
    if Path(sys.base_prefix) == src_dir:
        pth = _build_pth()
        dll_stem = next((f.stem for f in files[1:]
                         if f.name.startswith("python3") and f.stem != "python3"),
                        "python312")
        for pth_name in (f"{APP_NAME}._pth", f"{dll_stem}._pth"):
            (LAUNCHER_DIR / pth_name).write_text(pth, encoding="utf-8")

    _SOURCE_META.write_text(json.dumps({"dir": str(src_dir)}), encoding="utf-8")

    # Build the exe under a temp name, then swap in — works even while the
    # current launcher is running (a running exe can be renamed, not written).
    tmp = dst.with_suffix(".exe.new")
    try:
        tmp.unlink(missing_ok=True)   # leftover from an aborted build
    except OSError:
        pass
    shutil.copy2(files[0], tmp)
    try:
        _patch_resources(tmp, ico_path)
        try:
            os.replace(tmp, dst)
        except PermissionError:
            old = dst.with_suffix(".exe.old")
            old.unlink(missing_ok=True)
            os.rename(dst, old)
            os.rename(tmp, dst)
            try:
                old.unlink()
            except OSError:
                pass   # locked by the running instance; gone next rebuild
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dst


def read_file_description(path: Path) -> str:
    """Read FileDescription via version.dll — what Task Manager displays."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return ""
    p = ctypes.c_void_p()
    n = wt.UINT()
    if not ver.VerQueryValueW(
            buf, r"\StringFileInfo\040904b0\FileDescription",
            ctypes.byref(p), ctypes.byref(n)) or not p.value:
        return ""
    return ctypes.wstring_at(p.value)


if __name__ == "__main__":
    target = ensure_launcher(force=True)
    desc = read_file_description(target)
    print(f"Launcher : {target}")
    print(f"FileDescription now reads: {desc!r}")
    if desc != APP_NAME:
        sys.exit("PATCH FAILED — description did not take")
    print("OK — launch with:  "
          f'"{target}" "{Path(__file__).parent / "main.py"}"')
