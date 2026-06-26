# Camoufox Stealth Browser Setup & Migration Guide

This repository contains the opinionated maximum-stealth launcher and multi-profile manager for Camoufox. It simulates authentic Windows browser sessions, implements dynamic hardware pairing (linking screen resolutions, core counts, GPUs, and DPR), and enforces window-viewport constraints to bypass fingerprinting systems.

---

## Files to Package & Move

To backup or move this setup to another RDP, you only need to zip/transfer the following files and folders:

1. **`camoufox_stealth.py`**: The core stealth launcher containing mouse, typing, scrolling humanization, and the profile manager.
2. **`launch_new_profile.py`**: The automation helper that detects existing profiles and opens a new sequential one headfully.
3. **`requirements.txt`**: The pip package requirements list.
4. **`profiles/`** *(Optional)*: This folder holds your persistent sessions, cookies, history, and cached hardware configurations. Keep this folder if you want to reuse existing accounts/logins.

---

## Deployment on a New RDP

Follow these steps to set up and run this project on a new machine:

### Step 1: Install Python
Ensure **Python 3.11** (or 3.10+) is installed on the target RDP. Ensure "Add Python to PATH" is checked during installation.

### Step 2: Install Dependencies
Open PowerShell in the folder where you unzipped the files and run:
```powershell
pip install -r requirements.txt
```

### Step 3: Fetch the Browser Binary
Camoufox requires a patched Firefox browser. Download it automatically by running:
```powershell
python -m camoufox fetch
```

### Step 4: Run
You are ready to launch! Run the script to start generating new persistent accounts and test on CreepJS:
```powershell
python launch_new_profile.py
```

---

## Uploading to GitHub (Optional)

If you want to keep this on GitHub:
1. Initialize git in the folder:
   ```powershell
   git init
   ```
2. Create a `.gitignore` file to completely exclude profiles and python cache files:
   Create a `.gitignore` file containing:
   ```
   profiles/
   __pycache__/
   *.log
   ```
3. Commit and push:
   ```powershell
   git add .
   git commit -m "Initial commit of stealth camoufox framework"
   git remote add origin <your-repo-url>
   git branch -M main
   git push -u origin main
   ```
