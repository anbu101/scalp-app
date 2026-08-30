#!/usr/bin/env bash
set -euo pipefail

echo "================================"
echo "   Scalp Build Script (ARM64)"
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

# --- Step 0.5: Build mode selection --------------------------------------
# Usage: ./build-scalp.sh [frontend|backend|both]
# No argument → interactive prompt. Either way the Tauri packaging step at
# the end ALWAYS runs: both frontend/build and backend/ are .app RESOURCES,
# so a rebuilt piece is invisible until repackaged (the exact lesson of the
# TSG ModuleNotFoundError hunt — fresh files beside a stale bundle).
BUILD_MODE="${1:-}"
if [[ -z "$BUILD_MODE" ]]; then
  echo "What do you want to build?"
  echo "  1) Frontend only  (sync + npm build, then repackage app)"
  echo "  2) Backend only   (sync + PyInstaller, then repackage app)"
  echo "  3) Both           (full build)"
  read -p "Choice [3]: " choice
  case "${choice:-3}" in
    1) BUILD_MODE="frontend" ;;
    2) BUILD_MODE="backend" ;;
    3) BUILD_MODE="both" ;;
    *) error "Invalid choice: $choice" ;;
  esac
fi
case "$BUILD_MODE" in frontend|backend|both) ;; *) error "Invalid mode '$BUILD_MODE' (use frontend|backend|both)";; esac
log "Build mode: $BUILD_MODE"

DO_FRONTEND=false; DO_BACKEND=false
[[ "$BUILD_MODE" == "frontend" || "$BUILD_MODE" == "both" ]] && DO_FRONTEND=true
[[ "$BUILD_MODE" == "backend"  || "$BUILD_MODE" == "both" ]] && DO_BACKEND=true

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

# --- Step 4: Sync and Build Frontend (mode: frontend / both) ---
if $DO_FRONTEND; then
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

    log "Copying frontend for Tauri bundling..."
    rm -rf "$SCRIPT_DIR/frontend/build"
    mkdir -p "$SCRIPT_DIR/frontend/build"
    cp -r "$FRONTEND_DEST/build/"* "$SCRIPT_DIR/frontend/build/"
    success "Frontend copied to $SCRIPT_DIR/frontend/build/"
  else
    error "Frontend folder not found at $FRONTEND_SRC"
  fi
else
  # Backend-only build still repackages the .app, which bundles the LAST
  # frontend build. Guard that one exists so Tauri doesn't ship an empty UI.
  if [ ! -d "$SCRIPT_DIR/frontend/build" ] || [ -z "$(ls -A "$SCRIPT_DIR/frontend/build" 2>/dev/null)" ]; then
    error "No previous frontend build at $SCRIPT_DIR/frontend/build — run with 'frontend' or 'both' first."
  fi
  warn "Skipping frontend build (reusing existing $SCRIPT_DIR/frontend/build)"
fi

# --- Step 5 + 5.5: Sync Backend and Build Binary (mode: backend / both) ---
if $DO_BACKEND; then
  if [ ! -d "$BACKEND_SRC" ]; then
    error "Backend folder not found at $BACKEND_SRC"
  fi

  log "Syncing backend from $BACKEND_SRC to $BACKEND_DEST..."
  rm -rf "$BACKEND_DEST"
  # ONEDIR NOTE: exclude build outputs so they never travel into the dest —
  # dist/'s second _internal + a scalp-backend dir-vs-file collision breaks
  # Tauri's resource walker ("Not a directory (os error 20)").
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

  cd "$BACKEND_DEST"

  if [ ! -d "venv" ]; then
    log "Creating Python virtual environment..."
    python3 -m venv venv
  fi
  source venv/bin/activate

  log "Installing backend dependencies..."
  pip install -q -r requirements.txt
  pip install -q pyinstaller==6.3.0

  # ── GATE 1: SOURCE MUST COMPILE ─────────────────────────────────────────
  # A .py with a syntax error is not a build failure in PyInstaller — the
  # module graph silently DROPS the module (one line in warn-*.txt) and the
  # bundle ships without it, surfacing later as ModuleNotFoundError at
  # runtime. This is exactly how a doubled backtest_tsg_runner.py (two
  # concatenated copies → mid-file `from __future__` = SyntaxError) shipped
  # a bundle missing TSG while every tree-parity check passed. Fail HERE.
  log "GATE 1: byte-compiling entire app/ tree..."
  python3 -m compileall -q app || error "GATE 1 FAILED: syntax error in app/ — fix before building (see output above)"
  success "GATE 1 passed: app/ tree compiles"

  log "Running PyInstaller (this takes 2-3 minutes)..."
  pyinstaller scalp-backend.spec --clean --noconfirm

  if [ ! -f "dist/scalp-backend/scalp-backend" ]; then
    error "PyInstaller build failed - launcher not found in dist/scalp-backend/"
  fi

  # ── GATE 2: REQUIRED MODULES IN THE ANALYSIS TOC ────────────────────────
  # Mirrors CI's hard-fail list + TSG. grep -F with surrounding quotes: an
  # exact fixed string, so dotted names can't wildcard-match slash PATHS in
  # data entries (which is how an earlier "TOC=2" false-positived).
  log "GATE 2: verifying required modules in Analysis TOC..."
  TOC="build/scalp-backend/Analysis-00.toc"
  REQUIRED_MODULES="
    app.api.backtest_routes
    app.backtest.queue_worker
    app.backtest.data.candle_source
    app.backtest.repo.backtest_repo
    app.backtest.scalpv5.backtest_scalpv5_runner
    app.backtest.runner.backtest_runner
    app.backtest.runner.backtest_hedge_runner
    app.backtest.bb.backtest_bb_runner
    app.backtest.ic.backtest_ic_runner
    app.backtest.ic.ic_v1_engine
    app.backtest.tools.corpus_sanitizer
    app.backtest.tsg.backtest_tsg_runner
    app.backtest.gc.backtest_gc_runner
    app.backtest.gc.gc_v1_engine
    app.backtest.tma.backtest_tma_v2_runner
    app.backtest.tma.tma_v2_engine
    app.engine.tma2.tma2_selection_loop
    app.engine.tma2.tma2_trade_manager
    app.jobs.tma2_live_eod
    app.engine.vet.vet_selection_loop
    app.engine.vet.vet_manager
    app.api.vet_state_routes
    app.jobs.vet_live_eod
  "
  MISSING=0
  for m in $REQUIRED_MODULES; do
    if grep -qF "'$m'" "$TOC"; then echo "  OK   $m"; else echo "  MISS $m"; MISSING=1; fi
  done
  [ "$MISSING" = "1" ] && error "GATE 2 FAILED: modules missing from analysis — check build/scalp-backend/warn-scalp-backend.txt"
  # Surface any dropped app.* module the list doesn't know about yet:
  grep -i "app\." "build/scalp-backend/warn-scalp-backend.txt" | grep -iv "excluded module" | head -5 || true
  success "GATE 2 passed: all required modules analyzed"

  # ── GATE 3: GROUND TRUTH — MODULES INSIDE THE SHIPPED LAUNCHER ──────────
  # The TOC is intent; the embedded PYZ is reality. Read the launcher's own
  # archive and assert the required modules actually ship.
  log "GATE 3: verifying modules inside dist launcher's embedded archive..."
  python3 - << 'PYGATE' || error "GATE 3 FAILED: required module absent from the shipped archive"
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
import sys
REQUIRED = [
    "app.api.backtest_routes",
    "app.backtest.queue_worker",
    "app.backtest.ic.backtest_ic_runner",
    "app.backtest.tsg.backtest_tsg_runner",
    "app.backtest.gc.backtest_gc_runner",
    "app.backtest.tools.corpus_sanitizer",
    "app.backtest.tma.backtest_tma_v2_runner",
    "app.engine.tma2.tma2_selection_loop",
    "app.engine.vet.vet_selection_loop",
    "app.engine.vet.vet_manager",
    "app.api.vet_state_routes",
    "app.jobs.vet_live_eod",
]
r = CArchiveReader("dist/scalp-backend/scalp-backend")
names = list(r.toc.keys()) if isinstance(r.toc, dict) else [e[-1] for e in r.toc]
pyz = [n for n in names if str(n).endswith(".pyz")][0]
data = r.extract(pyz)
if isinstance(data, tuple):
    data = data[-1]
open("/tmp/scalp_gate.pyz", "wb").write(data)
mods = set(ZlibArchiveReader("/tmp/scalp_gate.pyz").toc.keys())
bad = [m for m in REQUIRED if m not in mods]
for m in REQUIRED:
    print(("  OK   " if m in mods else "  MISS ") + m)
if bad:
    sys.exit(1)
print(f"  ({len(mods)} modules shipped)")
PYGATE
  success "GATE 3 passed: shipped archive contains required modules"

  # Copy onedir contents into backend root (launcher at ./scalp-backend,
  # _internal/ beside it), then remove the working trees (Fix A: dist/'s
  # duplicate _internal + scalp-backend DIRECTORY break Tauri bundling).
  rm -rf scalp-backend _internal
  cp -R dist/scalp-backend/. ./
  chmod +x scalp-backend
  rm -rf dist build

  if [ -e "$BACKEND_DEST/dist" ] || [ -d "$BACKEND_DEST/scalp-backend" ]; then
      error "backend/ still contains dist/ or a scalp-backend DIRECTORY — Tauri will fail. Clean it."
  fi

  success "Backend onedir copied: $BACKEND_DEST/scalp-backend (+ _internal/)"
  ls -la _internal | head -5
  deactivate
  cd "$SCRIPT_DIR"
else
  # Frontend-only build still bundles backend/ as a resource — guard that a
  # previously built launcher exists so we don't ship a backend-less .app.
  if [ ! -f "$BACKEND_DEST/scalp-backend" ] || [ ! -d "$BACKEND_DEST/_internal" ]; then
    error "No previous backend build at $BACKEND_DEST — run with 'backend' or 'both' first."
  fi
  warn "Skipping backend build (reusing existing $BACKEND_DEST/scalp-backend)"
fi

cd "$SCRIPT_DIR"

# Untracked package-marker check (disk-vs-git divergence)
echo "Checking for untracked __init__.py (disk-vs-git divergence)..."
( cd "$PROJECT_ROOT" && find backend/app -type d -name '__pycache__' -prune -o -type d -print | while read -r d; do
    if ls "$d"/*.py >/dev/null 2>&1 && [ -f "$d/__init__.py" ]; then
      git ls-files --error-unmatch "$d/__init__.py" >/dev/null 2>&1 || echo "  WARN untracked: $d/__init__.py"
    fi
  done )

# --- Step 6: Clean previous Tauri build (ARM64 native) ---
# ARM-ONLY: universal-apple-darwin target removed — this machine and this
# script's output are Apple Silicon only. Intel/universal stays a CI concern.
log "Cleaning previous Tauri build..."
rm -rf src-tauri/target/release/bundle || true
success "Previous build cleaned"

# --- Step 7: Build Tauri app (ARM64 native) ---
log "Building Scalp macOS app (arm64 native)..."
cd src-tauri
npm run tauri build
cd ..

APP_BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/release/bundle/macos"
DMG_DIR="$SCRIPT_DIR/src-tauri/target/release/bundle/dmg"
APP_NAME="Scalp.app"

if [[ ! -d "$APP_BUNDLE_DIR/$APP_NAME" ]]; then
    error "Build failed: $APP_NAME not found"
fi
success "ARM64 app built"

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

# ── GATE 4: the .app must carry the EXACT launcher we just verified ──────
# Byte-identity between src-tauri/backend/scalp-backend and the bundled
# copy. Catches stale-resource packaging: fresh binary beside the app,
# old binary inside it.
SRC_MD5=$(md5 -q "$BACKEND_DEST/scalp-backend")
APP_MD5=$(md5 -q "$BUNDLED_BACKEND/scalp-backend")
if [[ "$SRC_MD5" != "$APP_MD5" ]]; then
    error "GATE 4 FAILED: bundled launcher ($APP_MD5) != built launcher ($SRC_MD5) — Tauri packaged a stale resource"
fi
success "GATE 4 passed: bundled launcher is byte-identical to the verified build"

# --- Step 8: Create distributable archive ---
log "Creating distribution archive..."
cd "$APP_BUNDLE_DIR"
rm -f Scalp.app.tar.gz
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
    echo "   DMG:       $DMG_DIR/Scalp_${VERSION}_aarch64.dmg"
fi
echo
echo "📊 Build Info:"
echo "   Version:   $VERSION"
echo "   Mode:      $BUILD_MODE"
echo "   Platform:  darwin-arm64 (Apple Silicon native)"
echo "   Packaging: PyInstaller ONEDIR (launcher + _internal/)"
echo "   Gates:     compileall ✓  TOC ✓  shipped-archive ✓  bundle-md5 ✓"
echo
echo "📝 Note: the backend takes ~40-45s to reach LISTENING on first launch."
echo "📤 Next: Run ./release.sh to publish to GitHub"
echo
success "Done!"
echo

read -p "Press Enter to exit..."