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

for cmd in node npm rustc lipo; do
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

# Remove old x86 venv + build artifacts if they exist
rm -rf venv-x86 build dist

# ============================================================================
# x86_64 INTERPRETER RESOLUTION  (root-cause fix for the arm64 contamination)
# ----------------------------------------------------------------------------
# /usr/local/bin/python3 is a python.org *universal2* build. A venv created from
# a universal2 binary on Apple Silicon defaults its launcher to the NATIVE arm64
# slice, so every `pip install` inside that venv silently pulls arm64 wheels
# (cryptography's _rust.abi3.so, etc). PyInstaller then aborts at the PKG step:
#   "IncompatibleBinaryArchError: ... is incompatible with target arch x86_64
#    (has arch: arm64)".
#
# FIX: physically THIN the interpreter to an x86_64-only binary with `lipo`, and
# build the venv from THAT. With no arm64 slice present, the venv launcher cannot
# fall back to arm64, so all wheels resolve x86_64. If thinning fails (some
# framework builds re-pull the universal dylib via @rpath), fall back to a real
# Intel-only Homebrew python under Rosetta, which has no arm64 slice at all.
#
# Either way, we GATE on `platform.machine() == x86_64` before installing a
# single package, so a bad interpreter fails in 2 seconds instead of 100s later
# inside PyInstaller.
# ============================================================================

PY_FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10"
PY_X86_DIR="/tmp/py310-x86"
PY_X86="$PY_X86_DIR/python3.10-x86"

build_x86_interpreter() {
  rm -rf "$PY_X86_DIR"
  mkdir -p "$PY_X86_DIR"

  # Strategy A: lipo-thin the python.org universal2 framework python to x86_64.
  if [ -f "$PY_FRAMEWORK" ]; then
    log "Thinning $PY_FRAMEWORK to x86_64-only..."
    if lipo "$PY_FRAMEWORK" -thin x86_64 -output "$PY_X86" 2>/dev/null; then
      local m
      m=$(arch -x86_64 "$PY_X86" -c "import platform; print(platform.machine())" 2>/dev/null || echo "fail")
      if [[ "$m" == "x86_64" ]]; then
        success "Thinned x86_64 interpreter ready: $PY_X86"
        return 0
      fi
      warn "Thinned interpreter did not run as x86_64 (got: $m) — trying Homebrew fallback"
    else
      warn "lipo thinning failed — trying Homebrew fallback"
    fi
  else
    warn "python.org framework python not found at $PY_FRAMEWORK — trying Homebrew fallback"
  fi

  # Strategy B: real Intel-only Homebrew python under Rosetta (no arm64 slice).
  local brew_py="/usr/local/opt/python@3.10/bin/python3.10"
  if [ ! -f "$brew_py" ]; then
    if [ -f "/usr/local/bin/brew" ]; then
      log "Installing x86_64 Homebrew python@3.10 under Rosetta..."
      arch -x86_64 /usr/local/bin/brew install python@3.10 || true
    else
      error "No x86_64 Homebrew at /usr/local/bin/brew and thinning failed. Install x86 Homebrew, or fix /Library/Frameworks Python."
    fi
  fi
  if [ -f "$brew_py" ]; then
    cp "$brew_py" "$PY_X86"
    local m
    m=$(arch -x86_64 "$PY_X86" -c "import platform; print(platform.machine())" 2>/dev/null || echo "fail")
    if [[ "$m" == "x86_64" ]]; then
      success "Homebrew x86_64 interpreter ready: $PY_X86"
      return 0
    fi
    error "Even the Homebrew python did not run as x86_64 (got: $m). Cannot build Intel binary."
  fi

  error "Could not produce an x86_64-only Python interpreter."
}

build_x86_interpreter

# Sanity: the thinned interpreter must be x86_64-only (no arm64 line).
log "Verifying thinned interpreter architecture..."
file "$PY_X86"
if file "$PY_X86" | grep -qi "arm64"; then
  error "$PY_X86 still contains an arm64 slice — venv would default to arm64. Aborting."
fi
success "Interpreter is x86_64-only"

# Create x86 venv FROM the x86-only interpreter.
log "Creating x86_64 Python virtual environment..."
arch -x86_64 "$PY_X86" -m venv venv-x86

# Activate venv
source venv-x86/bin/activate

# ============================================================================
# HARD GATE #1 — venv interpreter MUST be x86_64 before ANY pip install.
# This is the check that was missing; it turns a 100s PyInstaller failure into
# an instant, obvious error.
# ============================================================================
VENV_ARCH=$(python3 -c "import platform; print(platform.machine())")
if [[ "$VENV_ARCH" != "x86_64" ]]; then
    error "venv-x86 interpreter is $VENV_ARCH, not x86_64 — aborting BEFORE install.
       The base interpreter resolved to arm64. pyvenv.cfg home should point at
       $PY_X86_DIR. Check: head -1 venv-x86/pyvenv.cfg ; file venv-x86/bin/python3"
fi
success "✓ venv interpreter is x86_64"

# Belt-and-suspenders: the venv's own python must contain an x86_64 slice.
# (Gate #1 already proved it RUNS as x86_64 via platform.machine(); this just
# guards against a non-x86 binary. A universal2 binary is acceptable.)
if ! lipo -archs venv-x86/bin/python3 2>/dev/null | grep -qw "x86_64"; then
    error "venv-x86/bin/python3 has NO x86_64 slice (archs: $(lipo -archs venv-x86/bin/python3 2>/dev/null)) — venv is not x86-capable."
fi
success "✓ venv python binary has an x86_64 slice"

# CRITICAL: Install everything under arch -x86_64 to get x86_64 packages
log "Upgrading pip..."
arch -x86_64 python3 -m pip install --upgrade pip

# Install cryptography FIRST as a canary — it's the package that broke the build.
# cryptography's Rust extension (_rust.abi3.so) builds for the HOST arch (arm64)
# when compiled FROM SOURCE — `arch -x86_64` does NOT cross-compile Cargo. So we:
#   (a) FORBID source builds with --only-binary=:all:, and
#   (b) pin to "cryptography<43", which ships a prebuilt macOS wheel that carries
#       an x86_64 slice (42.0.8's _rust.abi3.so is universal2: x86_64 + arm64).
# A universal2 .so is FINE for an x86 build — PyInstaller extracts the x86_64
# slice. What must NEVER pass is an arm64-ONLY .so (no x86 slice to extract).
log "Installing cryptography (prebuilt wheel only, no source build) ..."
arch -x86_64 python3 -m pip install --no-cache-dir --only-binary=:all: "cryptography<43"

CRYPTO_SO=$(find venv-x86 -name "_rust.abi3.so" -path "*cryptography*" | head -1)
if [ -n "$CRYPTO_SO" ]; then
  # PASS if the file has an x86_64 slice (whether x86-only or universal2).
  # FAIL only if x86_64 is ABSENT (i.e. an arm64-only build).
  if lipo -archs "$CRYPTO_SO" 2>/dev/null | grep -qw "x86_64"; then
    success "✓ cryptography _rust.abi3.so has an x86_64 slice ($(lipo -archs "$CRYPTO_SO"))"
  else
    error "cryptography _rust.abi3.so has NO x86_64 slice (archs: $(lipo -archs "$CRYPTO_SO" 2>/dev/null)).
       pip built it from source for arm64. Aborting — PyInstaller would fail at PKG.
       Wipe venv-x86 and re-run; ensure --only-binary and the <43 pin are honoured."
  fi
fi

log "Installing backend dependencies (x86_64) - this may take 5-10 minutes..."
# CRITICAL: requirements.txt may pin a NEWER cryptography (>=43) that has no
# prebuilt x86_64 wheel, so a plain install would SOURCE-BUILD its Rust
# extension for the host arch (arm64) and silently re-contaminate the venv —
# exactly what Gate #2 catches. Forbid source builds for the known Rust/C-ext
# offenders so pip must use a prebuilt (x86_64-bearing) wheel or fail loudly.
# cryptography is pinned to <43 in requirements.txt (42.0.8 ships a universal2
# wheel). If you bump it, ensure the new version publishes a macOS x86_64 wheel.
arch -x86_64 python3 -m pip install --no-cache-dir \
    --only-binary=cryptography,cffi,bcrypt,pynacl \
    -r requirements.txt

# Re-assert cryptography after the requirements install (belt): if a transitive
# resolver still pulled a newer source build, force it back to the good wheel.
CRYPTO_SO=$(find venv-x86 -name "_rust.abi3.so" -path "*cryptography*" | head -1)
if [ -n "$CRYPTO_SO" ] && ! lipo -archs "$CRYPTO_SO" 2>/dev/null | grep -qw "x86_64"; then
    warn "cryptography lost its x86_64 slice during requirements install — forcing 42.0.8 wheel"
    arch -x86_64 python3 -m pip install --no-cache-dir --force-reinstall \
        --only-binary=:all: "cryptography==42.0.8"
fi

arch -x86_64 python3 -m pip install --no-cache-dir pyinstaller==6.3.0

# ============================================================================
# HARD GATE #2 — every compiled extension MUST have an x86_64 slice.
# The old script only checked _zope_interface_coptimizations, so an arm64
# cryptography slipped through. We now scan ALL .so files.
#
# IMPORTANT: a UNIVERSAL2 .so (x86_64 + arm64) is VALID — PyInstaller extracts
# the x86_64 slice. So we must FAIL only when x86_64 is ABSENT, not merely when
# arm64 is present. lipo -archs lists the slices; we flag any .so missing x86_64.
# ============================================================================
log "Verifying package architecture (every .so must have an x86_64 slice)..."
BAD_ARCH_FILES=""
while IFS= read -r so; do
    archs=$(lipo -archs "$so" 2>/dev/null || echo "")
    # Skip non-Mach-O files lipo can't read (rare data .so); they aren't loaded as bins.
    [ -z "$archs" ] && continue
    if ! echo "$archs" | grep -qw "x86_64"; then
        BAD_ARCH_FILES+="$so   [archs: $archs]"$'\n'
    fi
done < <(find venv-x86 -name "*.so" 2>/dev/null)

if [ -n "$BAD_ARCH_FILES" ]; then
    echo "$BAD_ARCH_FILES"
    error "Found .so files with NO x86_64 slice (listed above). The x86 venv is contaminated
       (a package was built from source for arm64). Wipe venv-x86 and reinstall, forcing
       prebuilt wheels (--only-binary) for any Rust/C-extension package."
fi
success "✓ All compiled extensions contain an x86_64 slice"

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
log "Verifying frontend exists before Tauri build..."
if [[ ! -f "$SCRIPT_DIR/frontend/build/index.html" ]]; then
    error "Frontend not found at $SCRIPT_DIR/frontend/build/"
fi
ls -la "$SCRIPT_DIR/frontend/build/"
success "Frontend verified"

log "Building Scalp for Intel macOS (x86_64)..."
npm run tauri build -- --target x86_64-apple-darwin

APP_BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/x86_64-apple-darwin/release/bundle/macos"
APP_NAME="Scalp.app"

if [[ ! -d "$APP_BUNDLE_DIR/$APP_NAME" ]]; then
    error "Build failed: $APP_NAME not found"
fi

success "Intel (x86_64) binary built"

# --- FIX BACKEND EXECUTION & GATEKEEPER (REQUIRED) ---
log "Fixing backend executable permission and clearing quarantine..."

BACKEND_BIN="$APP_BUNDLE_DIR/$APP_NAME/Contents/Resources/backend/scalp-backend"

chmod +x "$BACKEND_BIN"
xattr -dr com.apple.quarantine "$APP_BUNDLE_DIR/$APP_NAME"

ls -l "$BACKEND_BIN"
success "Backend permissions and quarantine fixed"


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