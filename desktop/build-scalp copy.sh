#!/usr/bin/env bash
set -e

echo "================================"
echo "   Scalp One-Click Build Script"
echo "================================"

# --- Step 0: Move to script directory ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Step 1: Sanity checks ---
for cmd in node npm rustc zip; do
  if ! command -v $cmd >/dev/null 2>&1; then
    echo "❌ $cmd not found. Please install it."
    exit 1
  fi
done

echo "✅ Tooling OK"
echo

# --- Step 2: Install desktop dependencies ---
echo "Installing desktop dependencies..."
npm install

# --- Step 3: Build frontend ---
if [ -f "../frontend/package.json" ]; then
  echo "Building frontend..."
  cd ../frontend
  npm install
  npm run build
  cd "$SCRIPT_DIR"
else
  echo "ℹ️ Frontend folder not found, skipping frontend build"
fi

# --- Step 4: Clean previous Tauri build ---
echo "Cleaning previous Tauri build..."
rm -rf src-tauri/target || true

# --- Step 5: Build Tauri app (UNIVERSAL) ---
echo "Building Scalp UNIVERSAL macOS app..."
npm run tauri build -- --target universal-apple-darwin

# --- Step 6: Create OTA ZIP (macOS-safe, NO AppleDouble) ---
APP_BUNDLE_DIR="src-tauri/target/universal-apple-darwin/release/bundle/macos"
APP_NAME="Scalp.app"
ZIP_NAME="Scalp.app.zip"

echo
echo "Creating OTA ZIP archive..."
cd "$APP_BUNDLE_DIR"

# Remove old zip if exists
rm -f "$ZIP_NAME"

# Critical flags:
# -y  : store symlinks
# -r  : recursive
# -X  : strip extra file attributes (kills AppleDouble)
# -q  : quiet
zip -r -y -X "$ZIP_NAME" "$APP_NAME"

echo "ZIP created:"
ls -lh "$ZIP_NAME"

cd "$SCRIPT_DIR"

echo
echo "✅ BUILD + ZIP SUCCESSFUL"
echo
echo "App:"
echo "$APP_BUNDLE_DIR/$APP_NAME"
echo
echo "OTA ZIP:"
echo "$APP_BUNDLE_DIR/$ZIP_NAME"
echo

read -p "Press Enter to exit..."
