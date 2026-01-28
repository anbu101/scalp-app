# Scalp App - Installation Guide

## System Requirements

- **macOS:** 11.0 (Big Sur) or later
- **Architecture:** Universal (Apple Silicon M1/M2/M3 and Intel)
- **No Python required** - fully self-contained

---

## Download

1. Visit the [latest release](https://github.com/anbu101/scalp-app/releases/latest)
2. Download **one** of these files:
   - **Recommended:** `Scalp_x.x.x_universal.dmg` (easiest installation)
   - **Alternative:** `Scalp.app.tar.gz` (manual installation)

---

## Installation

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

## First Launch - Security Settings

macOS will show a security warning for apps not downloaded from the App Store. This is normal and expected.

### If the app is blocked:

1. Go to **System Settings** (or **System Preferences** on older macOS)
2. Navigate to **Privacy & Security**
3. Scroll down to the **Security** section
4. You'll see a message about Scalp being blocked
5. Click **Open Anyway**
6. Confirm by clicking **Open** in the dialog

**Important:** Always use **Right-click → Open** for the first launch. Double-clicking may not work due to macOS security.

---

## What to Expect

✅ App window opens  
✅ Backend starts automatically (About 2 mins on first launch)  
✅ No Python installation needed  
✅ No Docker or additional dependencies required  
✅ Fully self-contained application  

---

## Troubleshooting

### App won't open

**Solution:**
- Ensure you **right-clicked** and chose **Open** (not double-click)
- Check **System Settings** → **Privacy & Security** for any blocks
- Make sure you're running macOS 11.0 or later

### Backend not connecting

**Solution:**
- Wait about 2 mins on first launch (backend initialization)
- Check **Activity Monitor** for "scalp-backend" process
- Restart the app if it doesn't connect after 30 seconds

### "App is damaged" error

**Solution:**
```bash
# Open Terminal and run:
xattr -cr /Applications/Scalp.app
```

This removes the quarantine flag that macOS adds to downloaded files.

### Still having issues?

1. Take a screenshot of any error messages
2. Check the Console app for crash logs:
   - Open **Console.app**
   - Search for "Scalp"
3. Email: **anbu101@gmail.com** with:
   - macOS version
   - Mac model (Intel or Apple Silicon)
   - Screenshot of the error
   - Any relevant Console logs

---

## Uninstallation

1. Quit Scalp if running
2. Move `Scalp.app` from Applications to Trash
3. Optionally delete app data:
```bash
   rm -rf ~/Library/Application\ Support/com.scalp
```

---

## Updates

The app will check for updates automatically. When a new version is available, you'll be notified to download and install it from the releases page.

---

## Privacy & Data

- All data is stored locally on your machine
- No telemetry or analytics are collected
- App requires internet only for trading operations
- Application data location: `~/Library/Application Support/com.scalp/`

---

**Need help?** Contact: anbu101@gmail.com
