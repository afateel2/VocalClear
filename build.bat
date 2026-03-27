@echo off
:: VocalClear — PyInstaller build script
:: Run this from the VocalClear directory with your venv active.
::
:: Output: dist\VocalClear\VocalClear.exe
::
:: First-time setup:
::   pip install pyinstaller

echo [VocalClear Build] Checking PyInstaller...
pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

echo [VocalClear Build] Cleaning previous build...
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

echo [VocalClear Build] Building...
pyinstaller VocalClear.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo BUILD COMPLETE.
echo Executable: dist\VocalClear\VocalClear.exe
echo.
echo To distribute: zip the entire dist\VocalClear\ folder.
pause
