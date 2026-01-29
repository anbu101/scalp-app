# Scalp App - Installation Guide

## System Requirements

### macOS
- **Version:** 11.0 (Big Sur) or later
- **Architecture:** Universal (Apple Silicon M1/M2/M3 and Intel)
- **No Python required** - fully self-contained

### Windows
- **Version:** Windows 10 or later (64-bit)
- **No Python required** - fully self-contained

---

## Download

1. Visit the [latest release](https://github.com/anbu101/scalp-app/releases/latest)
2. Download the appropriate installer for your platform:

### macOS
   - **Recommended:** `Scalp-vX.X.X-universal.dmg` (easiest installation)
   - **Alternative:** `Scalp.app.tar.gz` (manual installation)

### Windows
   - **Recommended:** `Scalp_X.X.X_x64_en-US.msi` (MSI installer)
   - **Alternative:** `Scalp_X.X.X_x64-setup.exe` (NSIS installer)

---

## Installation

## macOS Installation

### Option A: DMG (Recommended)

1. Double-click the downloaded `.dmg` file
2. Drag **Scalp** to the **Applications** folder
3. Eject the DMG
4. Open **Applications** folder
5. **Right-click** on **Scalp** → Select **Open**
6. Click **Open** in the security confirmation dialog

### Option B: TAR.GZ Archive

1. Double-click `Scalp.app.tar.gz` to extract
2. Move the extracted `Scalp.app` to your **Applications** folder
3. **Right-click** on **Scalp.app** → Select **Open**
4. Click **Open** in the security confirmation dialog

---

## Windows Installation

### Option A: MSI Installer (Recommended)

1. Double-click the downloaded `.msi` file
2. If Windows Defender SmartScreen appears:
   - Click **More info**
   - Click **Run anyway**
3. Follow the installation wizard
4. Launch **Scalp** from the Start Menu

### Option B: NSIS Installer

1. Double-click the downloaded `.exe` file
2. If Windows Defender SmartScreen appears:
   - Click **More info**
   - Click **Run anyway**
3. Follow the installation wizard
4. Launch **Scalp** from the Start Menu or Desktop shortcut

---

## First Launch Security

### macOS Security Settings

macOS will show a security warning for apps not downloaded from the App Store. This is normal and expected.

**If the app is blocked:**

1. Go to **System Settings** (or **System Preferences** on older macOS)
2. Navigate to **Privacy & Security**
3. Scroll down to the **Security** section
4. You'll see a message about Scalp being blocked
5. Click **Open Anyway**
6. Confirm by clicking **Open** in the dialog

**Important:** Always use **Right-click → Open** for the first launch. Double-clicking may not work due to macOS security.

### Windows Security Settings

Windows Defender SmartScreen may show a warning for unsigned apps. This is normal for new applications.

**If SmartScreen blocks the installer:**

1. Click **More info** on the SmartScreen warning
2. Click **Run anyway**
3. The installer will proceed normally

**Note:** The app is not yet code-signed. A signed version will be available in future releases.

---

## What to Expect

✅ App window opens  
✅ Backend starts automatically (90 seconds on first launch)  
✅ No Python installation needed  
✅ No Docker or additional dependencies required  
✅ Fully self-contained application  

---

## Troubleshooting

### macOS Issues

#### App won't open

**Solution:**
- Ensure you **right-clicked** and chose **Open** (not double-click)
- Check **System Settings** → **Privacy & Security** for any blocks
- Make sure you're running macOS 11.0 or later

#### "App is damaged" error

**Solution:**
```bash
# Open Terminal and run:
xattr -cr /Applications/Scalp.app
```

This removes the quarantine flag that macOS adds to downloaded files.

---

### Windows Issues

#### Installer blocked by SmartScreen

**Solution:**
- Click **More info** on the warning
- Click **Run anyway**
- The app is safe but not yet code-signed

#### App won't start after installation

**Solution:**
- Check Windows Defender hasn't quarantined the app
- Right-click the installer → **Run as administrator**
- Temporarily disable antivirus during installation

#### "Missing DLL" error

**Solution:**
- Install [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- Restart your computer
- Reinstall Scalp

---

### Common Issues (All Platforms)

#### Backend not connecting

**Solution:**
- Wait 90 seconds on first launch (backend initialization)
- Check Task Manager (Windows) or Activity Monitor (Mac) for "scalp-backend" process
- Restart the app if it doesn't connect after 120 seconds

#### Still having issues?

1. Take a screenshot of any error messages
2. **Windows:** Check Event Viewer for application errors
3. **macOS:** Check Console app for crash logs
4. Email: **anbu101@gmail.com** with:
   - Operating system and version
   - Computer specs
   - Screenshot of the error
   - Any relevant logs

---

## Uninstallation

### macOS

1. Quit Scalp if running
2. Move `Scalp.app` from Applications to Trash
3. Optionally delete app data:
```bash
   rm -rf ~/Library/Application\ Support/com.scalp
```

### Windows

1. **Settings** → **Apps** → **Installed apps**
2. Find **Scalp** in the list
3. Click the three dots → **Uninstall**
4. Optionally delete app data:
   - Open File Explorer
   - Navigate to `%APPDATA%\com.scalp`
   - Delete the folder

---

## Updates

The app checks for updates automatically. When a new version is available, you'll be notified to download and install it from the releases page.

---

## Privacy & Data

- All data is stored locally on your machine
- No telemetry or analytics are collected
- App requires internet only for trading operations
- Application data locations:
  - **macOS:** `~/Library/Application Support/com.scalp/`
  - **Windows:** `%APPDATA%\com.scalp\`

---

## Platform-Specific Notes

### macOS
- Universal binary works on both Intel and Apple Silicon Macs
- Requires macOS 11.0 (Big Sur) or later
- Notarization coming in future releases

### Windows
- 64-bit only (x64 architecture)
- Requires Windows 10 or later
- Code signing coming in future releases

---

**Need help?** Contact: anbu101@gmail.com