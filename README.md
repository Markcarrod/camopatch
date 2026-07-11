# Camoufox Stealth Browser — Cross-Platform Setup Guide

Maximum-stealth Camoufox launcher and multi-profile manager.  
Simulates authentic browser sessions, implements dynamic hardware pairing (screen resolutions, core counts, GPUs, DPR), and enforces window-viewport constraints to bypass fingerprinting systems.

Supports **Windows RDP** and **Ubuntu RDP / Linux**.

---

## Files

| File | Description |
|---|---|
| `camoufox_stealth.py` | Core stealth launcher — humanized mouse/typing/scrolling + profile manager |
| `launch_new_profile.py` | Auto-detects existing profiles and launches the next sequential one |
| `run_two_profiles.py` | Opens two test profiles sequentially on CreepJS |
| `stealth_patch.js` | Deep-stealth JS patch for OffscreenCanvas, WebGPU, and font metrics |
| `requirements.txt` | Pip package requirements |
| `profiles/` *(git-ignored)* | Persistent sessions, cookies, and hardware configs — keep this if migrating accounts |

---

## Setup on Ubuntu (RDP)

### Prerequisites

Your Ubuntu machine must be running with a **real display** (X11 or Wayland).  
This project uses headful (visible) browser windows — an RDP/VNC session is required.

### Step 1 — Clone the repo

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

### Step 2 — Install Python 3.11+

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip
```

Verify:
```bash
python3 --version
```

### Step 3 — Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 5 — Fetch the Camoufox browser binary

Camoufox requires a patched Firefox binary. Download it with:

```bash
python -m camoufox fetch
```

This downloads the Linux build automatically.

### Step 6 — Configure your profiles directory

Open `launch_new_profile.py` and set your profiles path:

```python
PROFILES_DIR = "/home/youruser/camofox_profiles"
```

Or leave it as `"profiles"` to store them next to the script.

### Step 7 — Run

```bash
python launch_new_profile.py
```

> **Note**: The browser will open as a visible window in your RDP session. Make sure your RDP is connected to a desktop session (not just a terminal).

---

## Setup on Windows (RDP)

### Step 1 — Install Python 3.11+

Download from [python.org](https://www.python.org/downloads/). Check **"Add Python to PATH"** during install.

### Step 2 — Install dependencies

Open PowerShell in the project folder:

```powershell
pip install -r requirements.txt
```

### Step 3 — Fetch the browser binary

```powershell
python -m camoufox fetch
```

### Step 4 — Run

```powershell
python launch_new_profile.py
```

---

## Proxy Configuration

Edit the `PROXY` dict at the top of `launch_new_profile.py`:

```python
PROXY = {
    "server":   "http://your-proxy-ip:port",
    "username": "your-username",
    "password":  "your-password",
}
```

---

## Pushing to GitHub

```bash
git add .
git commit -m "Cross-platform: Ubuntu + Windows support"
git remote add origin https://github.com/<your-username>/<your-repo>.git
git branch -M main
git push -u origin main
```

Then on Ubuntu:

```bash
git clone https://github.com/<your-username>/<your-repo>.git
# or if already cloned:
git pull origin main
```

---

## Notes

- `profiles/` is **git-ignored** — your login sessions and cookies will never be pushed.
- `vc_redist.exe` is Windows-only and is also git-ignored.
- The script auto-detects the OS (`platform.system()`) and passes the correct value to Camoufox — no manual changes needed when switching between Windows and Linux.
