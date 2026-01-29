#!/usr/bin/env bash
set -euo pipefail

echo "================================"
echo "   Scalp Release Script"
echo "================================"
echo

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[RELEASE]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v gh &> /dev/null; then
    error "GitHub CLI not found. Install with: brew install gh"
fi

TAURI_CONF="$SCRIPT_DIR/src-tauri/tauri.conf.json"
VERSION=$(grep -m 1 '"version"' "$TAURI_CONF" | sed 's/.*"version": *"\([^"]*\)".*/\1/')

if [[ -z "$VERSION" ]]; then
    error "Could not read version"
fi

log "Release version: v$VERSION"
read -p "Create release? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    error "Cancelled"
fi

BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/universal-apple-darwin/release/bundle"
DMG="$BUNDLE_DIR/dmg/Scalp_${VERSION}_universal.dmg"
APP_TAR="$BUNDLE_DIR/macos/Scalp.app.tar.gz"

if [[ ! -f "$DMG" ]] && [[ ! -f "$APP_TAR" ]]; then
    error "No build artifacts found. Run ./build-scalp.sh first!"
fi

success "Files ready"

log "Enter release notes (Ctrl+D when done):"
RELEASE_NOTES=$(cat)
[[ -z "$RELEASE_NOTES" ]] && RELEASE_NOTES="Release v$VERSION"

log "Creating GitHub release..."

FILES=()
[[ -f "$DMG" ]] && FILES+=("$DMG")
[[ -f "$APP_TAR" ]] && FILES+=("$APP_TAR")

if gh release create "v$VERSION" "${FILES[@]}" \
    --title "Scalp v$VERSION" \
    --notes "$RELEASE_NOTES"; then
    success "Release created!"
else
    error "Failed to create release"
fi

echo
success "DONE! Users can download from:"
echo "https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$VERSION"
echo