@echo off
setlocal
title VocalClear Installer

echo.
echo  ============================================================
echo    VocalClear  ^|  Real-time Microphone Noise Suppression
echo  ============================================================
echo.

:: ── Verify Python is available ───────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python was not found.
    echo  Please install Python 3.10+ from https://python.org and rerun this script.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  Found: %PY_VER%
echo.

:: ── Install Python dependencies ──────────────────────────────────────────
echo  Installing Python packages…
pip install --upgrade pip --quiet
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo  [ERROR] pip install failed.  Check the error above.
    pause
    exit /b 1
)
echo.
echo  Packages installed successfully.
echo.

:: ── Create desktop shortcut ──────────────────────────────────────────────
echo  Creating desktop shortcut…
python "%~dp0create_shortcut.py"
echo.

:: ── VB-CABLE reminder ────────────────────────────────────────────────────
echo  ============================================================
echo   IMPORTANT:  VB-CABLE virtual audio driver
echo  ============================================================
echo.
echo  VocalClear routes your processed audio through a virtual
echo  microphone device.  If you do not have VB-CABLE installed:
echo.
echo    1. Download it for FREE from:
echo         https://vb-audio.com/Cable/
echo    2. Run the installer as Administrator.
echo    3. Reboot (required by the driver).
echo    4. In Discord / Zoom / Teams, choose "CABLE Output" as your
echo       input device instead of your real microphone.
echo.
echo  VocalClear will detect VB-CABLE automatically on next launch.
echo  ============================================================
echo.
echo  Installation complete!
echo  Launch VocalClear from the 'VocalClear' shortcut on your Desktop.
echo.
pause
endlocal
