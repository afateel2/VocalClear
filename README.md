# VocalClear

Real-time microphone noise suppression for Windows. Runs silently in the system tray and routes clean audio through VB-CABLE so Discord, Zoom, or any app automatically picks it up.

---

## Features

- AI-powered noise suppression (RNNoise) with VAD — removes background noise, keyboard clicks, fans, and breathing
- Upgradeable to DeepFilterNet3 (Krisp-class quality) if you have the build tools
- Soundboard — play audio clips directly into your mic
- Strength slider — dial in how aggressive the filtering is
- Pause/Resume from tray with a single click
- Starts with Windows (optional)

---

## Requirements

- Windows 10 / 11 (64-bit)
- [VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/) — free, installs in 30 seconds
- Python 3.10+ (only needed if running from source)

---

## Option A — Run the Pre-built Exe (Easiest)

1. Download or clone this repo
2. Install VB-CABLE from https://vb-audio.com/Cable/ and reboot
3. Open `dist\VocalClear\` and double-click **VocalClear.exe**
4. The VocalClear icon will appear in your system tray (check the `^` overflow arrow near the clock if you don't see it)
5. In Discord/Zoom, set your microphone to **CABLE Output (VB-Audio Virtual Cable)**

---

## Option B — Run from Source

### 1. Install Python

Download Python 3.10 or newer from https://python.org. During install, check **"Add Python to PATH"**.

### 2. Install VB-CABLE

Download and install from https://vb-audio.com/Cable/. Reboot after install.

### 3. Clone this repo

```
git clone https://github.com/afateel2/VocalClear.git
cd VocalClear
```

### 4. Install dependencies

```
pip install -r requirements.txt
```

### 5. Run

```
pythonw main.py
```

The app starts minimized to the system tray. Look for the icon near the clock (click `^` to expand the overflow if needed).

### 6. Create a desktop shortcut (optional)

```
python create_shortcut.py
```

This places a **VocalClear** shortcut on your Desktop that launches it silently with no console window.

---

## Setting up your mic in Discord / Zoom

1. Open Discord → Settings → Voice & Video
2. Set **Input Device** to `CABLE Output (VB-Audio Virtual Cable)`
3. Disable Discord's own noise suppression (Krikrisp) — VocalClear handles it

The same applies to Zoom, Teams, or any other app.

---

## Building the Exe Yourself

```
pip install pyinstaller
python -m PyInstaller VocalClear.spec --noconfirm
```

Output: `dist\VocalClear\VocalClear.exe`

---

## Upgrading to DeepFilterNet (Optional — Best Quality)

If you have Visual Studio Build Tools installed:

```
pip install deepfilternet
```

VocalClear auto-detects it on next launch and uses it instead of RNNoise.

---

## Troubleshooting

**No icon in tray**
Click the `^` arrow next to the clock. Right-click the VocalClear icon → "Show icon and notifications" to pin it permanently.

**Audio engine failed to start**
VB-CABLE may not be installed or the device name changed. Install VB-CABLE, reboot, then open Settings in VocalClear and click **Apply & Restart Audio**.

**Voice sounds thin or over-filtered**
Lower the Strength slider in Settings. Around 40–60% works well for most environments.

**App won't start — another instance is already running**
Check Task Manager for a `VocalClear.exe` or `pythonw.exe` process and end it, then relaunch.
