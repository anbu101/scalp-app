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

## Zerodha API Setup (Required)

Before you can use the app for trading, you need to configure Zerodha API credentials.

### Create Zerodha Developer App

1. Go to [https://developers.kite.trade/](https://developers.kite.trade/)
2. Sign in with your Zerodha credentials
3. Click **"Create New App"**
4. Fill in the details:
   - **App name:** Scalp Terminal (or any name you prefer)
   - **Redirect URL:** *(Choose based on your setup below)*
   - **Description:** Personal trading terminal
   - **Publisher:** Your name
5. Click **"Create"**
6. Note down your **API Key** and **API Secret** (you'll need these)

**⚠️ IMPORTANT:** The **Redirect URL** depends on whether you want mobile access or not. See the setup guides below.

---

## Setup Guide: Choose Your Configuration

Choose ONE of the following setup paths based on your needs:

### 📋 Quick Comparison

| Feature | Desktop Only | Desktop + Mobile |
|---------|-------------|------------------|
| **Use on laptop** | ✅ Yes | ✅ Yes |
| **Use on phone** | ❌ No | ✅ Yes |
| **Tailscale required** | ❌ No | ✅ Yes |
| **Setup complexity** | 🟢 Simple | 🟡 Moderate |
| **Recommended for** | Most users | Power users |

---

## 🖥️ Setup Option 1: Desktop Only (Recommended for Most Users)

**Perfect if you only want to use the app on your Mac/Windows laptop.**

### Prerequisites
- ✅ Scalp app installed
- ✅ Zerodha trading account
- ❌ NO Tailscale needed

### Step 1: Configure Zerodha API Redirect URL

When creating your Zerodha API app (see above), set:

**Redirect URL:**
```
http://127.0.0.1:47321/zerodha/callback
```

Click **Save**.

### Step 2: Launch Scalp App

1. Open the Scalp app
2. Wait for backend to start (90 seconds on first launch)
3. Navigate to **Connections** page

### Step 3: Enter API Credentials

1. In the **Connections** page, find **Zerodha Configuration**
2. Enter your **API Key**
3. Enter your **API Secret**
4. Click **Save**

### Step 4: Login to Zerodha

1. Click **"Login to Zerodha"**
2. Enter your Zerodha credentials in the browser
3. Complete 2FA if required
4. You'll be redirected back to the app
5. Status should show **"Connected"** ✅

**✅ Done!** You can now trade from your laptop.

---

## 📱 Setup Option 2: Desktop + Mobile Access

**Perfect if you want to access the app from both your laptop AND your phone.**

### Prerequisites
- ✅ Scalp app installed on Mac/Windows
- ✅ Zerodha trading account
- ✅ Tailscale account (free)
- ✅ iPhone or Android phone

### Part A: Install Tailscale

#### On Mac

1. **Install Tailscale:**
   ```bash
   brew install tailscale
   ```
   
   Or download from [https://tailscale.com/download/mac](https://tailscale.com/download/mac)

2. **Start Tailscale:**
   ```bash
   sudo tailscale up
   ```
   
   Your browser will open for sign-in/sign-up.

3. **Create free Tailscale account** (if you don't have one)

4. **Enable Funnel for HTTPS access:**
   ```bash
   tailscale funnel 47321
   ```
   
   **Output will look like:**
   ```
   Available on the internet:
   
   https://your-machine-name.tail-abc123.ts.net/
   |-- / proxy http://127.0.0.1:47321
   ```
   
   **⚠️ IMPORTANT:** Copy this HTTPS URL! You'll need it.

5. **Get your Tailscale IP:**
   ```bash
   tailscale ip -4
   ```
   
   **Example output:** `100.122.185.95`
   
   **⚠️ IMPORTANT:** Note this IP! You'll need it for mobile access.

#### On Windows

1. **Download Tailscale:**
   - Go to [https://tailscale.com/download/windows](https://tailscale.com/download/windows)
   - Download and run the installer

2. **Install and Sign In:**
   - Follow the installation wizard
   - Sign in with your Tailscale account (or create one)

3. **Enable Funnel:**
   - Open PowerShell as Administrator
   - Run:
     ```powershell
     tailscale funnel 47321
     ```
   
   **⚠️ IMPORTANT:** Copy the HTTPS URL shown!

4. **Get your Tailscale IP:**
   ```powershell
   tailscale ip -4
   ```
   
   **⚠️ IMPORTANT:** Note this IP!

#### On iPhone/Android

1. **Install Tailscale app:**
   - **iPhone:** Download from App Store
   - **Android:** Download from Google Play

2. **Sign in:**
   - Open the Tailscale app
   - Sign in with **the same account** you used on your computer
   - Grant VPN permissions when prompted

3. **Verify connection:**
   - You should see your Mac/Windows computer listed
   - It should show a green dot (online)

**✅ Tailscale setup complete!**

### Part B: Configure Zerodha API with Funnel URL

When creating your Zerodha API app, set:

**Redirect URL:** Use the HTTPS Funnel URL from Part A

**Example:**
```
https://anbu-macbook.tail-abc123.ts.net/zerodha/callback
```

**⚠️ Replace with YOUR actual Funnel URL!**

Click **Save**.

### Part C: Launch Scalp App

1. Open the Scalp app on your Mac/Windows
2. Wait for backend to start (90 seconds on first launch)
3. Frontend dev server will start automatically (you'll see logs)

### Part D: Enter API Credentials

On your **laptop**:

1. Navigate to **Connections** page
2. Enter your **API Key**
3. Enter your **API Secret**
4. Click **Save**

### Part E: Login to Zerodha

You can login from **either** laptop or phone:

**From Laptop:**
1. Go to **Connections** page
2. Click **"Login to Zerodha"**
3. Enter credentials
4. Complete 2FA
5. You'll be redirected back to the app ✅

**From Phone:**
1. Open Safari/Chrome on your phone
2. Go to: `http://YOUR_TAILSCALE_IP:3000`
   
   **Example:** `http://100.122.185.95:3000`
   
   *(Use the IP you noted in Part A)*

3. Navigate to **Connections** page
4. Click **"Login to Zerodha"**
5. Enter credentials
6. Complete 2FA
7. You'll be redirected back to the app ✅

### Part F: Access from Mobile

**On your phone**, open browser and go to:

```
http://YOUR_TAILSCALE_IP:3000
```

**Example:** `http://100.122.185.95:3000`

**✅ Done!** You can now:
- Trade from your laptop
- Monitor and trade from your phone
- Both devices share the same data in real-time

**⚠️ IMPORTANT:** Your Mac/Windows computer **must remain on** for mobile access to work.

---

## Daily Usage

### Desktop Only Setup

1. Open Scalp app on your laptop
2. Start trading ✅

### Desktop + Mobile Setup

**On Mac/Windows:**
1. Open Scalp app
2. Frontend dev server starts automatically
3. Trade from laptop ✅

**On Phone:**
1. Make sure laptop is ON and Scalp app is running
2. Open browser: `http://YOUR_TAILSCALE_IP:3000`
3. Monitor or trade from anywhere ✅

---

## Mobile Access - Additional Notes

### Keeping Your Computer Awake

**macOS:**
1. **System Settings** → **Lock Screen**
2. Set **"Turn display off on battery when inactive"** to **Never**
3. Set **"Turn display off on power adapter when inactive"** to **Never**

**Windows:**
1. **Settings** → **System** → **Power & sleep**
2. Set **"Screen"** to **Never**
3. Set **"Sleep"** to **Never** (when plugged in)

### Tailscale Security

- ✅ End-to-end encrypted VPN
- ✅ Only your devices can access
- ✅ No public exposure
- ✅ Free for personal use
- ✅ Works on any network (home, office, coffee shop)

### Add to Home Screen (iOS)

To make it feel like a native app:

1. Open `http://YOUR_TAILSCALE_IP:3000` in Safari
2. Tap the **Share** button (⬆️)
3. Scroll down, tap **"Add to Home Screen"**
4. Name it: **Scalp Terminal**
5. Tap **"Add"**

Now you have an app icon on your home screen! Tap it to open.

---

## Switching Between Setups

**Want to add mobile access later?**
1. Install Tailscale (Part A above)
2. Update Zerodha redirect URL to Funnel URL
3. Done! Mobile access enabled

**Want to remove mobile access?**
1. Change Zerodha redirect URL back to `http://127.0.0.1:47321/zerodha/callback`
2. Uninstall Tailscale
3. Done! Back to desktop-only

---

## Mobile Access (iPhone / Android)

Scalp Terminal can be monitored from your phone's browser while the desktop app is running. **The desktop app must be active and running for mobile access to work** — your phone connects to the backend on your laptop/desktop. Mobile cannot run independently.

### Requirements

1. **Scalp must be open on your desktop** — the backend must be started (green status indicator visible). If the desktop app is closed, the mobile browser will show a connection error.
2. **Both devices on the same Tailscale network** — install [Tailscale](https://tailscale.com/download) on both your desktop and phone and sign in with the same account.

### One-Time Setup

1. Install Tailscale on your desktop and phone and sign in to both with the same account
2. Note your desktop's Tailscale hostname — visible in the Tailscale menu bar app (e.g. `your-machine.tail.ts.net`)
3. Set your Kite Connect redirect URL to:
   ```
   https://your-machine.tail.ts.net/zerodha/callback
   ```
   Log in at [developers.kite.trade](https://developers.kite.trade) → your app → Redirect URL

### Daily Use

With the desktop app running, open your phone browser and go to:
```
http://your-machine.tail.ts.net:3000
```

The Tailscale Serve tunnel starts automatically each time you launch the desktop app — no manual terminal commands needed after the one-time setup above.

> ⚠️ **The desktop app must be running.** Mobile is a remote viewer only. Closing the desktop app will disconnect your phone immediately.

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

#### Mobile access not working (Desktop + Mobile setup only)

**Can't access from phone:**

**Solution:**
- Verify Tailscale is connected on both computer and phone
- Check your computer is ON and Scalp app is running
- Verify frontend dev server started (check app logs)
- Test: Open `http://YOUR_TAILSCALE_IP:47321/health` on phone
  - Should show: `{"status":"healthy"}`
  - If fails: Backend not accessible, check Tailscale connection

**Frontend dev server didn't start:**

**Solution:**
- Check app logs for "[RUNTIME] Frontend dev server started"
- Manually start it:
  ```bash
  # Mac/Windows
  cd /path/to/scalp-app/frontend
  HOST=0.0.0.0 npm run dev
  ```

**Zerodha login fails from mobile:**

**Solution:**
- Verify you're using the Funnel URL in Kite developer portal
- Check Funnel is running: `tailscale funnel status`
- Make sure redirect URL exactly matches (no trailing slash)
- Test callback URL accessibility: Open `https://your-funnel-url/health` on phone

**Mobile shows different data than laptop:**

**Solution:**
- Both should connect to the same backend (port 47321)
- Refresh mobile browser (pull down to refresh)
- Check both are connected to Zerodha (Connections page)

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