"""
Creates a desktop shortcut for VocalClear.
Run once after installation:  python create_shortcut.py

Generates vocalclear.ico (multi-size) in the project folder,
then creates a .lnk on the Desktop pointing to main.py with that icon.
"""

import sys
from pathlib import Path


def create_desktop_shortcut() -> bool:
    project_dir = Path(__file__).resolve().parent

    # ── Generate the .ico file ────────────────────────────────────────────────
    ico_path = project_dir / "vocalclear.ico"
    try:
        from icon import make_ico
        make_ico(ico_path)
        print(f"[icon]     Generated: {ico_path}")
    except Exception as exc:
        print(f"[icon]     Warning — could not generate icon: {exc}")
        ico_path = None

    # ── Create the .lnk shortcut ──────────────────────────────────────────────
    try:
        import win32com.client  # type: ignore
    except ImportError:
        print("[shortcut] pywin32 not found. Run: pip install pywin32")
        return False

    main_script = project_dir / "main.py"
    python_exe  = Path(sys.executable)
    pythonw_exe = python_exe.parent / "pythonw.exe"
    launcher    = str(pythonw_exe) if pythonw_exe.exists() else str(python_exe)

    shell          = win32com.client.Dispatch("WScript.Shell")
    desktop        = Path(shell.SpecialFolders("Desktop"))
    shortcut_path  = desktop / "VocalClear.lnk"

    lnk = shell.CreateShortCut(str(shortcut_path))
    lnk.TargetPath      = launcher
    lnk.Arguments       = f'"{main_script}"'
    lnk.WorkingDirectory = str(project_dir)
    lnk.Description     = "VocalClear – Real-time Microphone Noise Suppression"
    lnk.WindowStyle     = 1   # 1 = normal window (main window shown on launch)

    if ico_path and ico_path.exists():
        lnk.IconLocation = f"{ico_path}, 0"
    else:
        lnk.IconLocation = launcher

    lnk.save()
    print(f"[shortcut] Created: {shortcut_path}")
    return True


if __name__ == "__main__":
    ok = create_desktop_shortcut()
    if ok:
        print("\nDone.  Double-click 'VocalClear' on your Desktop to launch.")
    else:
        print("\nShortcut creation failed – see message above.")
    input("Press Enter to close…")
