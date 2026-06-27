#!/usr/bin/env bash
set -euo pipefail

echo "================================"
echo "   Scalp Build Script"
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

log "Building version: $VERSION"

# --- Step 3: Install desktop dependencies ---
log "Installing desktop dependencies..."
npm install
success "Desktop dependencies installed"

# --- Step 4: Sync and Build Frontend ---
if [ -d "$FRONTEND_SRC" ]; then
  log "Syncing frontend from $FRONTEND_SRC to $FRONTEND_DEST..."

  # Remove old frontend copy
  rm -rf "$FRONTEND_DEST"

  # Copy frontend (rsync excludes common ignored files)
  rsync -av --exclude='node_modules' \
            --exclude='build' \
            --exclude='.git' \
            --exclude='.env' \
            --exclude='*.log' \
            "$FRONTEND_SRC/" "$FRONTEND_DEST/"

  success "Frontend synced to $FRONTEND_DEST"

  # Build frontend in the copied location
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

  # Remove old backend copy
  rm -rf "$BACKEND_DEST"

  # Copy backend (rsync excludes common ignored files).
  # ONEDIR NOTE: also exclude dist/, build/, _internal/, and a stray
  # scalp-backend launcher in the SOURCE tree. These are build outputs — if a
  # prior build ever wrote them into backend/ source, they must NOT travel into
  # $BACKEND_DEST, or Tauri's resource walker will trip over a duplicate
  # _internal / a scalp-backend dir-vs-file collision.
  rsync -av --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='.pytest_cache' \
            --exclude='venv' \
            --exclude='dist' \
            --exclude='build' \
            --exclude='_internal' \
            --exclude='scalp-backend' \
            --exclude='.git' \
            --exclude='.env' \
            --exclude='*.log' \
            "$BACKEND_SRC/" "$BACKEND_DEST/"

  success "Backend synced to $BACKEND_DEST"
else
  error "Backend folder not found at $BACKEND_SRC"
fi

cd "$SCRIPT_DIR"

# --- Step 5.5: Build Backend Binary with PyInstaller (ONEDIR) ---
log "Building backend binary with PyInstaller (onedir)..."

cd "$BACKEND_DEST"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    log "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
log "Installing backend dependencies..."
pip install -q -r requirements.txt
pip install -q pyinstaller==6.3.0

# Build with PyInstaller (spec is now ONEDIR: emits dist/scalp-backend/ as a
# DIRECTORY = launcher + _internal/, no per-launch temp extraction).
log "Running PyInstaller (this takes 2-3 minutes)..."
pyinstaller scalp-backend.spec --clean --noconfirm

# ONEDIR: verify the LAUNCHER inside the output directory (not a single file).
if [ ! -f "dist/scalp-backend/scalp-backend" ]; then
    error "PyInstaller build failed - launcher not found in dist/scalp-backend/"
fi

# Clear any stale launcher/_internal from a previous run, then copy the whole
# onedir tree's CONTENTS into the backend root so the launcher lands at
# ./scalp-backend with _internal/ beside it (Tauri bundles backend/ as resources).
rm -rf scalp-backend _internal
cp -R dist/scalp-backend/. ./
chmod +x scalp-backend

# CRITICAL (Fix A): Tauri bundles the ENTIRE backend/ dir (resources:["backend"]).
# The PyInstaller working trees (dist/, build/) must NOT be bundled — dist/
# contains a SECOND _internal and a 'scalp-backend' DIRECTORY that collides with
# the 'scalp-backend' launcher FILE, causing Tauri to fail with
# "Not a directory (os error 20)". Remove them after the copy.
rm -rf dist build

# Guard: the collision paths must be gone before Tauri bundles backend/.
if [ -e "$BACKEND_DEST/dist" ] || [ -d "$BACKEND_DEST/scalp-backend" ]; then
    error "backend/ still contains dist/ or a scalp-backend DIRECTORY — Tauri will fail. Clean it."
fi

success "Backend onedir copied: $BACKEND_DEST/scalp-backend (+ _internal/)"
ls -la _internal | head -5

# Deactivate venv
deactivate

cd "$SCRIPT_DIR"

# --- Step 6: Clean previous Tauri build ---
log "Cleaning previous Tauri build..."
rm -rf src-tauri/target/universal-apple-darwin/release/bundle || true
success "Previous build cleaned"

# --- Step 7: Build Tauri app (UNIVERSAL) ---
log "Building Scalp UNIVERSAL macOS app..."
cd src-tauri
npm run tauri build -- --target universal-apple-darwin
cd ..

APP_BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/universal-apple-darwin/release/bundle/macos"
DMG_DIR="$SCRIPT_DIR/src-tauri/target/universal-apple-darwin/release/bundle/dmg"
APP_NAME="Scalp.app"

if [[ ! -d "$APP_BUNDLE_DIR/$APP_NAME" ]]; then
    error "Build failed: $APP_NAME not found"
fi

success "Universal binary built"

# --- Step 7.5: Verify backend onedir made it into the bundle ---
log "Verifying backend onedir landed in the .app..."
BUNDLED_BACKEND="$APP_BUNDLE_DIR/$APP_NAME/Contents/Resources/backend"
if [[ ! -f "$BUNDLED_BACKEND/scalp-backend" ]]; then
    error "Bundled launcher missing: $BUNDLED_BACKEND/scalp-backend"
fi
if [[ ! -d "$BUNDLED_BACKEND/_internal" ]]; then
    error "Bundled _internal/ missing: $BUNDLED_BACKEND/_internal — onedir libs not shipped."
fi
chmod +x "$BUNDLED_BACKEND/scalp-backend"
success "Backend onedir present in bundle (launcher + _internal/)"

# --- Step 8: Create distributable archive ---
log "Creating distribution archive..."
cd "$APP_BUNDLE_DIR"

# Remove old archives
rm -f Scalp.app.tar.gz

# Create .tar.gz archive for distribution
tar -czf Scalp.app.tar.gz "$APP_NAME"

if [[ ! -f "Scalp.app.tar.gz" ]]; then
    error "Failed to create tar.gz archive"
fi

success "Created Scalp.app.tar.gz"

# --- Summary ---
echo
echo "============================================================"
success "BUILD COMPLETE!"
echo "============================================================"
echo
echo "📦 Output Files:"
echo "   App:       $APP_BUNDLE_DIR/$APP_NAME"
echo "   Archive:   $APP_BUNDLE_DIR/Scalp.app.tar.gz"
if [[ -d "$DMG_DIR" ]]; then
    echo "   DMG:       $DMG_DIR/Scalp_${VERSION}_universal.dmg"
fi
echo
echo "📊 Build Info:"
echo "   Version:   $VERSION"
echo "   Platform:  darwin-universal (Intel + Apple Silicon)"
echo "   Packaging: PyInstaller ONEDIR (launcher + _internal/)"
echo
echo "📝 Synced Content:"
echo "   Frontend:  $FRONTEND_SRC → $FRONTEND_DEST (built)"
echo "   Backend:   $BACKEND_SRC → $BACKEND_DEST"
echo
echo "📤 Next: Run ./release.sh to publish to GitHub"
echo
success "Done!"
echo

read -p "Press Enter to exit..."