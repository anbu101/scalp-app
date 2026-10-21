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
   - `Scalp_X.X.X_x64-setup.exe` (the only Windows installer)
   - Note: `.msi` installers were discontinued after v10.4.13. If you previously
     installed Scalp via the MSI, uninstall it before installing the setup.exe —
     see "Migrating from an MSI install" below.

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

1. Double-click the downloaded `-setup.exe` file
2. If Windows Defender SmartScreen appears:
   - Click **More info**
   - Click **Run anyway**
3. Follow the installation wizard
4. Launch **Scalp** from the Start Menu or Desktop shortcut

Once installed, future updates arrive in-app: the update banner downloads and
installs the new version for you. This is the last installer you download by hand.

### Migrating from an MSI install

Only needed once, if you installed a version at or before v10.4.13 using the `.msi`.
Leaving the old MSI copy in place causes two Scalp installations in different
folders, and a stale shortcut can silently launch the old one.

1. Fully quit Scalp. In **Task Manager**, end every `Scalp.exe` and
   `scalp-backend.exe` process.
2. **Settings → Apps → Installed apps** — if more than one Scalp entry is listed,
   uninstall **all** of them.
3. Delete any leftover folders: `C:\Program Files\Scalp` and
   `%LOCALAPPDATA%\Programs\Scalp`.
4. **Do not delete `C:\Users\<you>\.scalp-app`** — that folder holds your
   database, licence, and logs.
5. Delete old Desktop / Start Menu Scalp shortcuts.
6. Install the `-setup.exe` and launch from the new shortcut.

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

**IMPORTANT:** The **Redirect URL** depends on whether you want mobile access or not. See the setup guides below.

---

## Setup Guide: Choose Your Configuration

Choose ONE of the following setup paths based on your needs:

### Quick Comparison

| Feature | Desktop Only | Desktop + Mobile |
|---------|-------------|------------------|
| **Use on laptop** | Yes | Yes |
| **Use on phone** | No | Yes |
| **Tailscale required** | No | Yes |
| **Setup complexity** | Simple | Moderate |
| **Recommended for** | Most users | Power users |

---

## Setup Option 1: Desktop Only (Recommended for Most Users)

**Perfect if you only want to use the app on your Mac/Windows laptop.**

### Prerequisites
- Scalp app installed
- Zerodha trading account
- NO Tailscale needed

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
5. Status should show **"Connected"**

**Done!** You can now trade from your laptop.

---

## Setup Option 2: Desktop + Mobile Access

**Perfect if you want to access the app from both your laptop AND your phone.**

> **What changed:** Mobile access now uses the **same port as the desktop backend — `47321`**. There is no separate frontend server on port 3000 anymore; the desktop app serves the mobile interface directly. If you set this up under an older version that used `:3000`, update your phone's bookmark to use `:47321` (see Part F).

### Prerequisites
- Scalp app installed on Mac/Windows
- Zerodha trading account
- Tailscale account (free)
- iPhone or Android phone

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

4. **Get your Tailscale IP:**
   ```bash
   tailscale ip -4
   ```

   **Example output:** `100.122.185.95`

   **IMPORTANT:** Note this IP! You'll need it for mobile access.

5. **Get your Tailscale HTTPS hostname (for the Zerodha callback):**
   ```bash
   tailscale status
   ```

   Your machine's hostname looks like `your-machine.tail-abc123.ts.net`.

   **IMPORTANT:** Note this hostname! You'll use it as your Zerodha redirect URL.

> **Note on Funnel:** The Scalp app automatically runs Tailscale **Funnel** on port `47321` each time it launches (this is what lets Zerodha's login callback reach your machine over HTTPS). You do **not** need to run any `tailscale funnel` command yourself — the app handles it. You can confirm it is active with `tailscale funnel status`.

#### On Windows

1. **Download Tailscale:**
   - Go to [https://tailscale.com/download/windows](https://tailscale.com/download/windows)
   - Download and run the installer

2. **Install and Sign In:**
   - Follow the installation wizard
   - Sign in with your Tailscale account (or create one)

3. **Get your Tailscale IP:**
   ```powershell
   tailscale ip -4
   ```

   **IMPORTANT:** Note this IP!

4. **Get your Tailscale HTTPS hostname:**
   ```powershell
   tailscale status
   ```

   **IMPORTANT:** Note the hostname (e.g. `your-machine.tail-abc123.ts.net`).

> **Note on Funnel:** The Scalp app starts Tailscale Funnel on port `47321` automatically on launch. No manual `tailscale funnel` command is required.

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

**Tailscale setup complete!**

### Part B: Configure Zerodha API with Funnel URL

When creating your Zerodha API app, set the **Redirect URL** to your Tailscale HTTPS hostname on the `/zerodha/callback` path:

**Example:**
```
https://your-machine.tail-abc123.ts.net/zerodha/callback
```

**Replace with YOUR actual Tailscale hostname!** (Funnel serves it over standard HTTPS port 443, so no port number is needed in the callback URL.)

Click **Save**.

### Part C: Launch Scalp App

1. Open the Scalp app on your Mac/Windows
2. Wait for backend to start (90 seconds on first launch)
3. The app automatically starts Tailscale Funnel on port `47321` — no manual steps needed

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
5. You'll be redirected back to the app

**From Phone:**
1. Open Safari/Chrome on your phone
2. Go to: `http://YOUR_TAILSCALE_IP:47321`

   **Example:** `http://100.122.185.95:47321`

   *(Use the IP you noted in Part A)*

3. Navigate to **Connections** page
4. Click **"Login to Zerodha"**
5. Enter credentials
6. Complete 2FA
7. You'll be redirected back to the app

### Part F: Access from Mobile

**On your phone**, open browser and go to:

```
http://YOUR_TAILSCALE_IP:47321
```

**Example:** `http://100.122.185.95:47321`

**Done!** You can now:
- Trade from your laptop
- Monitor and trade from your phone
- Both devices share the same data in real-time

**IMPORTANT:** Your Mac/Windows computer **must remain on** for mobile access to work.

---

## Daily Usage

### Desktop Only Setup

1. Open Scalp app on your laptop
2. Start trading

### Desktop + Mobile Setup

**On Mac/Windows:**
1. Open Scalp app
2. The backend (and Tailscale Funnel) start automatically
3. Trade from laptop

**On Phone:**
1. Make sure laptop is ON and Scalp app is running
2. Open browser: `http://YOUR_TAILSCALE_IP:47321`
3. Monitor or trade from anywhere

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

- End-to-end encrypted VPN between your own devices
- Free for personal use
- Works on any network (home, office, coffee shop)

> **Important — public exposure via Funnel:** To let Zerodha's login callback reach your machine, the app enables Tailscale **Funnel** on port `47321`, which exposes that port over the public internet via your Tailscale HTTPS hostname. Anyone who knows your `*.ts.net` hostname could reach the app on that port. Treat the hostname as a secret, do not share it, and avoid posting it anywhere public. (Access over the raw Tailscale IP, e.g. `100.x.y.z:47321`, remains private to your tailnet — only the Funnel hostname is publicly reachable.)

### Add to Home Screen (iOS)

To make it feel like a native app:

1. Open `http://YOUR_TAILSCALE_IP:47321` in Safari
2. Tap the **Share** button
3. Scroll down, tap **"Add to Home Screen"**
4. Name it: **Scalp Terminal**
5. Tap **"Add"**

Now you have an app icon on your home screen! Tap it to open.

---

## Switching Between Setups

**Want to add mobile access later?**
1. Install Tailscale (Part A above)
2. Update Zerodha redirect URL to your Tailscale HTTPS hostname (`https://your-machine.tail-abc123.ts.net/zerodha/callback`)
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
http://YOUR_TAILSCALE_IP:47321
```
**Example:** `http://100.122.185.95:47321`

The Tailscale Funnel tunnel starts automatically each time you launch the desktop app — no manual terminal commands needed after the one-time setup above.

> **The desktop app must be running.** Mobile is a remote viewer only. Closing the desktop app will disconnect your phone immediately.

---

## What to Expect

- App window opens
- Backend starts automatically (90 seconds on first launch)
- No Python installation needed
- No Docker or additional dependencies required
- Fully self-contained application

---

# Setting Up Your Static IP (Required from April 1, 2026)

SEBI regulations require that all Zerodha API orders come from a registered
static IP address. This guide walks you through getting a **free** cloud server
that provides you with a permanent static IP. The whole process takes about
15–20 minutes and you only do it once.

**Important:** Each person must do this with their own account.
Do not share your OCI instance or static IP with others.

---

## What you will need

- A web browser
- The .key file you download in Step 2 (keep it safe)
- About 15–20 minutes

---

## Step 1 — Mandatory - Create a free Digital Ocean Cloud account

1. Sign up in Digital Ocean using the following link https://m.do.co/c/9eabe5ae3d3b
2. Click create and select 'Droplet' and then click 'Get Started'
3. Choose image - Ubuntu 22.04 LTS
4. Choose Region - Bangalore / Singapore
5. Choose Authentication - SSH Key
6. Choose CPU - Regular - 1 GB / 1 CPU
7. Meanwhile in your Mac/Windows open the Terminal/cmd and do the following
    1. Copy paste this command: ssh-keygen -t ed25519 -C "relay-server"
    2. It will ask: "Enter file in which to save the key:" - just press enter
    3. Then it will ask: "Enter passphrase:" - just press enter
    4. Now you have created both private and public key -
        1. Private key → ~/.ssh/id_ed25519
        2. Public key  → ~/.ssh/id_ed25519.pub
    5. Run this command: cat ~/.ssh/id_ed25519.pub
    6. You will see something like: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI.... relay-server
    7. Copy the entire full line (this is your public key)
    8. Run the following to get your private key: cat ~/.ssh/id_ed25519
        1. You will see something like below:
            1. -----BEGIN OPENSSH PRIVATE KEY-----
            2. b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz...
            3. ...
            4. -----END OPENSSH PRIVATE KEY-----
        2. Copy the entire block and save it somewhere - this is your private key which you will use it in UI
8. Come back to Droplet creation, click 'Add SSH key' and paste the public key copied from your machine and save it.
9. Now you will have your Droplet created successfully.
10. Copy the ipv4 displayed. This will be your primary static IP. Save it somewhere safely.
11. Go back to your Mac/Windows terminal/cmd and run: ssh root@<YOUR_STATIC_IP_FROM_ABOVE_STEP>
    1. Example: ssh root@139.59.8.201
    2. It will ask: Are you sure you want to continue connecting (yes/no)?
    3. Type: yes
    4. You should land here: root@scalp-ubuntu-digitalocean:~#
    5. This marks the successful connection to your droplet.


## Step 2 — Optional but will be helpful - Create a free Oracle Cloud account

1. Open your browser and go to **oracle.com/cloud/free**

2. Click the orange **"Start for free"** button

3. Fill in your details:
   - Country
   - Name and email address
   - Choose a password

4. Verify your email address when the confirmation email arrives

5. Enter your mobile number for SMS verification

6. When asked for a **Home Region**, choose the one closest to India:
   - **India South (Hyderabad)** — recommended
   - India West (Mumbai) — also good

   **You cannot change your Home Region later**, so choose carefully.

7. Enter your credit/debit card details when asked.
   **You will NOT be charged** — Oracle requires this only to verify identity.
   The free tier resources we use have no cost.

8. Complete the sign-up. It may take a few minutes to activate your account.

---

Once your account is active and you are logged into Oracle Cloud:

1. In the top-left corner, click the **(hamburger menu)**

2. Go to **Compute → Instances**

3. Click the blue **"Create instance"** button

4. Fill in the form:

   **Name your instance:**
   - Type: `scalp-instance` (or any name you prefer)

   **Image and shape** (this section is important):
   - Click **"Edit"** or **"Change image"**
   - Select **Canonical Ubuntu**
   - Select version **22.04**
   - Click **"Select image"**
   - The shape (size) should auto-select **VM.Standard.E2.1.Micro** — this is the free one

   **Networking:**
   - Leave all defaults. Make sure **"Assign a public IPv4 address"** is set to **Yes**

   **Add SSH keys** (very important):
   - Select **"Generate a key pair for me"**
   - Click **"Save private key"**
   - This downloads a file called `ssh-key-YYYY-MM-DD.key` to your computer
   - **Keep this file safe** — you will need it in Scalp Terminal later
   - On Mac: the file goes to your Downloads folder
   - On Windows: the file goes to your Downloads folder

5. Click **"Create"** at the bottom

6. Wait about 2 minutes for the instance status to change from **Provisioning** to **Running**

---


1. Click on your instance name (`scalp-instance`) to open its details

2. Click the **"Networking"** tab

3. Look for **"Public IPv4 address"** — it will look like `144.24.159.177`

4. Copy this IP address — you will enter it in Scalp Terminal shortly

---


Your cloud server has a firewall that blocks all traffic by default.
You need to open one port for the order relay to work.

1. On the Networking tab, look for **"Subnet"** and click the link (e.g. `scalp-public-subnet`)

2. On the subnet page, look for **"Security List"** and click **"Default Security List for..."**

3. Click **"Add Ingress Rules"**

4. Fill in:
   - **Source CIDR:** `0.0.0.0/0`
   - **IP Protocol:** TCP
   - **Destination Port Range:** `8001`

5. Click **"Add Ingress Rules"** to save

---

## Step 3 — Mandatory - Register your Static IP with Zerodha

Before setting up the relay in the app, register your static IP(s) with Zerodha:

1. Go to **developers.kite.trade** and log in

2. Click on your profile / account name in the top right

3. Look for **"IP Whitelist"** section

4. Enter your Public IP addresses

5. Save

**This must be done before April 1, 2026** — orders from unregistered IPs will be rejected after that date.

---

## Step 4 — Mandatory - Set up the relay in Scalp Terminal

1. Open **Scalp Terminal** and go to the **Connections** page

2. Scroll down to the **"Static IP — Order Relay"** section

3. Click **"Set Up Static IP Relay"**

4. Enter Primary VM details (Use details from Step 1)
    1. "Primary IP" - Input the static IP created in Digital Ocean. Example: 139.59.8.201
    2. "Primary SSH Username (e.g. opc / root)" - Input root
    3. "Primary SSH Private Key" - copy paste the entire block from your private key. Example" -----BEGIN OPENSSH PRIVATE KEY----- b3BlbnNzaC1….. -----END OPENSSH PRIVATE KEY-----

5. Enter Secondary VM details - This is optional but very helpful if the primary VM goes down intermittently. (Use details from Step 2)
    1. "Secondary IP" - Input the static IP created in Oracle Cloud. Example: 140.57.18.123
    2. "Primary SSH Username (e.g. opc / root)" - Input opc
    3. "Primary SSH Private Key" - copy paste the entire block from your private key. Example" -----BEGIN OPENSSH PRIVATE KEY----- c6GDFNzaC1….. -----END OPENSSH PRIVATE KEY-----

6. Click **"Deploy Relay"**

7. You will see a progress log — wait about 60–90 seconds

8. When complete, the status shows **"Relay Active"** with your IP

---

## You are done

From now on, all your order placements go through your Cloud instance.
You do not need to do anything else — the relay runs automatically
in the background on your cloud server.


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
- Confirm you are using port **47321** in the phone URL (older setups used 3000 — that no longer works)
- Test: Open `http://YOUR_TAILSCALE_IP:47321/health` on phone
  - Should show: `{"status":"healthy"}`
  - If fails: Backend not accessible, check Tailscale connection

**Mobile page doesn't load (but /health works):**

**Solution:**
- Make sure your desktop app is the current version (the mobile interface is served by the backend on port 47321 in current versions)
- Try the bare URL `http://YOUR_TAILSCALE_IP:47321` (the interface loads at the root)
- Pull down to refresh the browser

**Zerodha login fails from mobile:**

**Solution:**
- Verify you're using your Tailscale HTTPS hostname in the Kite developer portal (e.g. `https://your-machine.tail-abc123.ts.net/zerodha/callback`)
- Check Funnel is running: `tailscale funnel status`
- Make sure redirect URL exactly matches (no trailing slash)
- Test callback URL accessibility: Open `https://your-funnel-hostname/health` on phone

**Mobile shows different data than laptop:**

**Solution:**
- Both connect to the same backend on port 47321
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