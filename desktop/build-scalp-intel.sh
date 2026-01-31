#!/usr/bin/env bash
set -euo pipefail

echo "================================"
echo "   Scalp Intel Build Script"
echo "   (x86_64 for Intel Macs)"
echo "================================"
echo

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[BUILD]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

# --- Check we're on Apple Silicon ---
ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
    error "This script must run on Apple Silicon (M1/M2/M3) to cross-compile for Intel"
fi

success "Running on Apple Silicon - can build for Intel"

# --- Step 0: Move to script directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_SRC="$PROJECT_ROOT/frontend"
BACKEND_SRC="$PROJECT_ROOT/backend"
FRONTEND_DEST="$SCRIPT_DIR/src-tauri/frontend"
BACKEND_DEST="$SCRIPT_DIR/src-tauri/backend"

cd "$SCRIPT_DIR"

# --- Step 1: Prerequisites Check ---
log "Checking prerequisites..."

for cmd in node npm rustc; do
  if ! command -v $cmd >/dev/null 2>&1; then
    error "$cmd not found. Please install it."
  fi
done

success "Tooling OK"

# --- Step 2: Read Version ---
log "Reading version from tauri.conf.json..."

TAURI_CONF="$SCRIPT_DIR/src-tauri/tauri.conf.json"
VERSION=$(grep -m 1 '"version"' "$TAURI_CONF" | sed 's/.*"version": *"\([^"]*\)".*/\1/')

if [[ -z "$VERSION" ]]; then
    error "Could not read version from tauri.conf.json"
fi

log "Building version: $VERSION (Intel x86_64)"

# --- Step 3: Install desktop dependencies ---
log "Installing desktop dependencies..."
npm install
success "Desktop dependencies installed"

# --- Step 4: Sync and Build Frontend ---
if [ -d "$FRONTEND_SRC" ]; then
  log "Syncing frontend from $FRONTEND_SRC to $FRONTEND_DEST..."
  
  rm -rf "$FRONTEND_DEST"
  
  rsync -av --exclude='node_modules' \
            --exclude='build' \
            --exclude='.git' \
            --exclude='.env' \
            --exclude='*.log' \
            "$FRONTEND_SRC/" "$FRONTEND_DEST/"
  
  success "Frontend synced to $FRONTEND_DEST"
  
  cd "$FRONTEND_DEST"
  
  log "Installing frontend dependencies..."
  npm install
  
  log "Building frontend..."
  npm run build
  
  success "Frontend built at $FRONTEND_DEST/build"
  cd "$SCRIPT_DIR"
  # NEW: Copy frontend to where Tauri expects it
  log "Copying frontend for Tauri bundling..."
  mkdir -p "$SCRIPT_DIR/frontend/build"
  cp -r "$FRONTEND_DEST/build/"* "$SCRIPT_DIR/frontend/build/"
  success "Frontend copied to $SCRIPT_DIR/frontend/build/"
  
else
  error "Frontend folder not found at $FRONTEND_SRC"
fi

# --- Step 5: Sync Backend ---
if [ -d "$BACKEND_SRC" ]; then
  log "Syncing backend from $BACKEND_SRC to $BACKEND_DEST..."
  
  rm -rf "$BACKEND_DEST"
  
  rsync -av --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='.pytest_cache' \
            --exclude='venv' \
            --exclude='venv-x86' \
            --exclude='dist' \
            --exclude='build' \
            --exclude='.git' \
            --exclude='.env' \
            --exclude='*.log' \
            "$BACKEND_SRC/" "$BACKEND_DEST/"
  
  success "Backend synced to $BACKEND_DEST"
else
  error "Backend folder not found at $BACKEND_SRC"
fi

cd "$SCRIPT_DIR"

# --- Step 5.5: Build Backend Binary for INTEL (x86_64) ---
log "Building backend binary for Intel (x86_64) using Rosetta..."

cd "$BACKEND_DEST"

# Remove old x86 venv if exists
rm -rf venv-x86

# Check if x86 homebrew python exists
if [ ! -f "/usr/local/bin/python3" ]; then
    warn "Intel Python not found at /usr/local/bin/python3"
    warn "Installing Homebrew and Python for x86_64..."
    
    # Install x86 Homebrew if not present
    if [ ! -f "/usr/local/bin/brew" ]; then
        log "Installing x86_64 Homebrew..."
        arch -x86_64 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    
    # Install x86 Python
    log "Installing x86_64 Python..."
    arch -x86_64 /usr/local/bin/brew install python@3.10
fi

# Create x86 venv using arch -x86_64
log "Creating x86_64 Python virtual environment..."
arch -x86_64 /usr/local/bin/python3 -m venv venv-x86

# Activate venv
source venv-x86/bin/activate

# CRITICAL: Install everything under arch -x86_64 to get x86_64 packages
log "Installing backend dependencies (x86_64) - this may take 5-10 minutes..."
arch -x86_64 pip install --no-cache-dir -r requirements.txt
arch -x86_64 pip install --no-cache-dir pyinstaller==6.3.0

# Verify we have x86_64 packages
log "Verifying package architecture..."
ZOPE_SO=$(find venv-x86 -name "_zope_interface_coptimizations*.so" | head -1)
if [ -n "$ZOPE_SO" ]; then
    ZOPE_ARCH=$(file "$ZOPE_SO" | grep -o "x86_64\|arm64")
    if [[ "$ZOPE_ARCH" == "arm64" ]]; then
        error "Packages are arm64! Need to install under arch -x86_64"
    fi
    success "✓ Packages are x86_64"
fi

# Build with PyInstaller under arch -x86_64
log "Running PyInstaller for x86_64 (this takes 2-3 minutes)..."
arch -x86_64 pyinstaller scalp-backend.spec --clean --noconfirm

# Verify binary was created and is x86_64
if [ ! -f "dist/scalp-backend" ]; then
    error "PyInstaller build failed - binary not found"
fi

# Check architecture
log "Verifying binary architecture..."
file dist/scalp-backend
lipo -info dist/scalp-backend

BINARY_ARCH=$(file dist/scalp-backend | grep -o "x86_64\|arm64")
if [[ "$BINARY_ARCH" != "x86_64" ]]; then
    error "Binary is $BINARY_ARCH, expected x86_64"
fi

success "✓ Verified x86_64 binary"

# Copy binary to backend root
cp dist/scalp-backend scalp-backend
chmod +x scalp-backend

success "Intel (x86_64) backend binary built: $BACKEND_DEST/scalp-backend"

# Deactivate venv
deactivate

cd "$SCRIPT_DIR"

# --- Step 6: Clean previous Tauri build ---
log "Cleaning previous Tauri build..."
rm -rf src-tauri/target/x86_64-apple-darwin/release/bundle || true
success "Previous build cleaned"

# --- Step 7: Build Tauri app for INTEL (x86_64) ---
log "Building Scalp for Intel macOS (x86_64)..."
npm run tauri build -- --target x86_64-apple-darwin

APP_BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/x86_64-apple-darwin/release/bundle/macos"
APP_NAME="Scalp.app"

if [[ ! -d "$APP_BUNDLE_DIR/$APP_NAME" ]]; then
    error "Build failed: $APP_NAME not found"
fi

success "Intel (x86_64) binary built"

# --- Step 8: Create distributable archive ---
log "Creating distribution archive..."
cd "$APP_BUNDLE_DIR"

# Remove old archives
rm -f Scalp-intel.app.tar.gz
rm -f Scalp-${VERSION}-intel.dmg

# Create .tar.gz archive
tar -czf "Scalp-${VERSION}-intel.app.tar.gz" "$APP_NAME"

if [[ ! -f "Scalp-${VERSION}-intel.app.tar.gz" ]]; then
    error "Failed to create tar.gz archive"
fi

success "Created Scalp-${VERSION}-intel.app.tar.gz"

# --- Step 9: Create DMG (optional) ---
if command -v create-dmg >/dev/null 2>&1; then
    log "Creating DMG..."
    create-dmg \
      --volname "Scalp" \
      --window-pos 200 120 \
      --window-size 600 400 \
      --icon-size 100 \
      --app-drop-link 425 120 \
      "Scalp-${VERSION}-intel.dmg" \
      "$APP_NAME" || warn "DMG creation failed (continuing anyway)"
else
    warn "create-dmg not found - skipping DMG creation"
    warn "Install with: brew install create-dmg"
fi

# --- Summary ---
echo
echo "============================================================"
success "INTEL BUILD COMPLETE!"
echo "============================================================"
echo
echo "📦 Output Files:"
echo "   App:       $APP_BUNDLE_DIR/$APP_NAME"
echo "   Archive:   $APP_BUNDLE_DIR/Scalp-${VERSION}-intel.app.tar.gz"
if [[ -f "$APP_BUNDLE_DIR/Scalp-${VERSION}-intel.dmg" ]]; then
    echo "   DMG:       $APP_BUNDLE_DIR/Scalp-${VERSION}-intel.dmg"
fi
echo
echo "📊 Build Info:"
echo "   Version:   $VERSION"
echo "   Platform:  darwin-x86_64 (Intel only)"
echo "   Backend:   $(file $BACKEND_DEST/scalp-backend | grep -o 'x86_64')"
echo
echo "📤 Manual Upload:"
echo "   1. Go to https://github.com/anbu101/scalp-app/releases"
echo "   2. Edit the release for v${VERSION}"
echo "   3. Upload: Scalp-${VERSION}-intel.app.tar.gz"
if [[ -f "$APP_BUNDLE_DIR/Scalp-${VERSION}-intel.dmg" ]]; then
    echo "   4. Upload: Scalp-${VERSION}-intel.dmg"
fi
echo
success "Done!"
echo

read -p "Press Enter to exit..."