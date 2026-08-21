#!/usr/bin/env bash
#
# deploy-scalp.command
# ----------------------------------------------------------------------
# Double-click to cut a new Scalp release.
#  - Shows current version from tauri.conf.json
#  - Prompts for the new version
#  - Prompts which TARGETS to build this release (ARM / Windows / Intel)
#    via explicit [arm] [intel] [win] markers
#  - Updates tauri.conf.json + writes the version stamp (both trees)
#  - Commits ALL changes (git add -A) + pushes to main
#  - Handles tag re-use (offers to overwrite a failed build)
#  - Tags + pushes -> triggers the GitHub Actions build/release workflow
#
# Safe to store and run from anywhere; it cd's into REPO_DIR itself.
# ----------------------------------------------------------------------

# --- CONFIG: edit these two if your paths ever change -----------------
REPO_DIR="/Users/anbu/dev/scalp-app"
CONF_REL="desktop/src-tauri/tauri.conf.json"
DEPLOY_BRANCH="main"
# ----------------------------------------------------------------------

set -uo pipefail

# Colors
BOLD='\033[1m'; RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

say()   { echo -e "${BLUE}▸${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}!${NC} $1"; }
err()   { echo -e "${RED}✗${NC} $1"; }

# Keep the Terminal window open on exit so you can read the result
hold() {
  echo
  echo -e "${BOLD}Press Enter to close this window...${NC}"
  read -r _
}
trap hold EXIT

clear
echo -e "${BOLD}=====================================${NC}"
echo -e "${BOLD}     Scalp Release Deployer${NC}"
echo -e "${BOLD}=====================================${NC}"
echo

# --- Sanity checks ----------------------------------------------------
if [[ ! -d "$REPO_DIR" ]]; then
  err "Repo not found: $REPO_DIR"
  exit 1
fi

CONF="$REPO_DIR/$CONF_REL"
if [[ ! -f "$CONF" ]]; then
  err "tauri.conf.json not found: $CONF"
  exit 1
fi

for cmd in git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "'$cmd' not found in PATH."
    exit 1
  fi
done

cd "$REPO_DIR" || { err "Could not cd into $REPO_DIR"; exit 1; }
export GIT_PAGER=cat
ok "Working in $REPO_DIR"


# --- Read current version ---------------------------------------------
# Match the top-level "version": "x.y.z" line only.
CURRENT_VERSION=$(grep -m1 -E '"version"[[:space:]]*:' "$CONF" \
  | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')

if [[ -z "$CURRENT_VERSION" ]]; then
  err "Could not read current version from $CONF_REL"
  exit 1
fi

echo
echo -e "Current version:  ${BOLD}${GREEN}v${CURRENT_VERSION}${NC}"
echo

# --- Prompt for new version -------------------------------------------
read -r -p "$(echo -e "Enter ${BOLD}new${NC} version number (e.g. 6.0.5): ")" NEW_VERSION

# Strip a leading 'v' if the user typed one
NEW_VERSION="${NEW_VERSION#v}"

# Validate: must be x.y.z (numbers)
if [[ ! "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  err "Invalid version '$NEW_VERSION'. Expected format: number.number.number (e.g. 6.0.5)"
  exit 1
fi

if [[ "$NEW_VERSION" == "$CURRENT_VERSION" ]]; then
  warn "New version is the SAME as current (v$CURRENT_VERSION)."
  warn "This is a re-deploy / retry of an existing version."
fi

TAG="v${NEW_VERSION}"

# --- Prompt: which targets to build this release? ---------------------
# The workflow understands three opt-in markers: [arm] [intel] [win].
# RULE in the workflow: NO markers => builds ALL THREE. So this script must
# ALWAYS emit explicit markers for the chosen targets — never rely on the
# bare default, or "ARM only" would silently become "all three".
#
# Intel is the single most expensive CI job (macOS bills 10x; the Intel job
# alone is ~270 billed minutes). ARM + Windows together are the common case.
echo
echo -e "${BOLD}Select build targets for this release${NC}"
echo -e "  1) ARM + Windows           ${YELLOW}(default — the usual release)${NC}"
echo -e "  2) ARM + Windows + Intel   (full set; ~270 extra CI minutes)"
echo -e "  3) ARM only"
echo -e "  4) Windows only"
echo -e "  5) Intel only"
echo -e "  6) Custom (pick each individually)"
read -r -p "$(echo -e "Choice ${BOLD}[1-6]${NC} (Enter = 1): ")" TARGET_CHOICE
TARGET_CHOICE="${TARGET_CHOICE:-1}"

BUILD_ARM=0; BUILD_INTEL=0; BUILD_WIN=0
case "$TARGET_CHOICE" in
  1) BUILD_ARM=1; BUILD_WIN=1 ;;
  2) BUILD_ARM=1; BUILD_WIN=1; BUILD_INTEL=1 ;;
  3) BUILD_ARM=1 ;;
  4) BUILD_WIN=1 ;;
  5) BUILD_INTEL=1 ;;
  6)
     read -r -p "$(echo -e "Include ${BOLD}ARM${NC} (Apple Silicon)? [Y/n]: ")" A
     [[ ! "$A" =~ ^[Nn]$ ]] && BUILD_ARM=1
     read -r -p "$(echo -e "Include ${BOLD}Windows${NC}? [Y/n]: ")" W
     [[ ! "$W" =~ ^[Nn]$ ]] && BUILD_WIN=1
     read -r -p "$(echo -e "Include ${BOLD}Intel${NC} (expensive)? [y/N]: ")" I
     [[ "$I" =~ ^[Yy]$ ]] && BUILD_INTEL=1
     ;;
  *)
     err "Invalid choice '$TARGET_CHOICE'. Expected 1-6."
     exit 1
     ;;
esac

# At least one target is required, else the release would be empty.
if [[ "$BUILD_ARM" == "0" && "$BUILD_INTEL" == "0" && "$BUILD_WIN" == "0" ]]; then
  err "No build targets selected — that would produce an empty release. Aborting."
  exit 1
fi

# Assemble the explicit marker string. ALWAYS emit markers for the chosen
# targets so the workflow never falls back to its "no markers = all three".
MARKERS=""
[[ "$BUILD_ARM"   == "1" ]] && MARKERS="${MARKERS} [arm]"
[[ "$BUILD_INTEL" == "1" ]] && MARKERS="${MARKERS} [intel]"
[[ "$BUILD_WIN"   == "1" ]] && MARKERS="${MARKERS} [win]"
MARKERS="${MARKERS# }"   # trim leading space

# Keep INCLUDE_INTEL for the existing summary/footer logic below.
INCLUDE_INTEL=$BUILD_INTEL

ok "Targets: ARM=$([[ $BUILD_ARM == 1 ]] && echo yes || echo no)  Windows=$([[ $BUILD_WIN == 1 ]] && echo yes || echo no)  Intel=$([[ $BUILD_INTEL == 1 ]] && echo yes || echo no)"
warn "Markers to embed: ${MARKERS}"

# --- Prompt for an optional commit description ------------------------
echo
read -r -p "$(echo -e "Enter a short ${BOLD}description${NC} for this release (optional, press Enter to skip): ")" DESC

# Build the COMMIT message: tag alone, or "tag — description", then the
# explicit build markers. The workflow reads markers from the COMMIT message
# (reliable in CI); the tag annotation carries them too as a fallback.
if [[ -n "$DESC" ]]; then
  COMMIT_MSG="${TAG} — ${DESC}"
else
  COMMIT_MSG="${TAG}"
fi
COMMIT_MSG="${COMMIT_MSG} ${MARKERS}"

# Tag annotation message: same content, markers on their own line as fallback.
TAG_MSG="${TAG} ${DESC:+— ${DESC}}
${MARKERS}"

echo
echo -e "About to deploy:  ${BOLD}${YELLOW}${TAG}${NC}   (was v${CURRENT_VERSION})"
echo -e "Commit message:   ${BOLD}${COMMIT_MSG}${NC}"
echo -e "ARM build:        $([[ $BUILD_ARM   == 1 ]] && echo -e "${BOLD}${GREEN}INCLUDED${NC}" || echo -e "${BOLD}${YELLOW}skipped${NC}")"
echo -e "Windows build:    $([[ $BUILD_WIN   == 1 ]] && echo -e "${BOLD}${GREEN}INCLUDED${NC}" || echo -e "${BOLD}${YELLOW}skipped${NC}")"
echo -e "Intel build:      $([[ $BUILD_INTEL == 1 ]] && echo -e "${BOLD}${GREEN}INCLUDED${NC}" || echo -e "${BOLD}${YELLOW}skipped${NC}")"
read -r -p "$(echo -e "Proceed? [y/N]: ")" CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  warn "Cancelled. No changes made."
  exit 0
fi

# --- Confirm we're on the right branch --------------------------------
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [[ "$CURRENT_BRANCH" != "$DEPLOY_BRANCH" ]]; then
  warn "You are on branch '$CURRENT_BRANCH', not '$DEPLOY_BRANCH'."
  read -r -p "$(echo -e "Switch to ${DEPLOY_BRANCH} and continue? [y/N]: ")" SW
  if [[ "$SW" =~ ^[Yy]$ ]]; then
    git checkout "$DEPLOY_BRANCH" || { err "Failed to checkout $DEPLOY_BRANCH"; exit 1; }
    ok "Switched to $DEPLOY_BRANCH"
  else
    warn "Cancelled."
    exit 0
  fi
fi

# --- Update tauri.conf.json version (in place, top-level only) --------
say "Updating $CONF_REL -> v${NEW_VERSION}"

# Use a temp file + precise awk. Only the FIRST "version": line is changed.
TMP="$(mktemp)"
awk -v newv="$NEW_VERSION" '
  !done && /"version"[[:space:]]*:/ {
    sub(/"version"[[:space:]]*:[[:space:]]*"[^"]+"/, "\"version\": \"" newv "\"")
    done=1
  }
  { print }
' "$CONF" > "$TMP" && mv "$TMP" "$CONF"

# Verify the write
WROTE=$(grep -m1 -E '"version"[[:space:]]*:' "$CONF" \
  | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')
if [[ "$WROTE" != "$NEW_VERSION" ]]; then
  err "Version update failed — file still shows '$WROTE'. Aborting before any git changes."
  exit 1
fi
ok "tauri.conf.json now at v${WROTE}"

# --- Stamp the version into the backend so the app knows its own version --
# version_check.py reads backend/app/version_stamp.txt. Writing it here, at
# build time, means the real version travels INSIDE the bundled backend/
# resources (tauri.conf.json's "resources": ["backend"]). Both trees.
STAMP_REL_SRC="backend/app/version_stamp.txt"
STAMP_REL_TAURI="desktop/src-tauri/backend/app/version_stamp.txt"
for stamp in "$REPO_DIR/$STAMP_REL_SRC" "$REPO_DIR/$STAMP_REL_TAURI"; do
  mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
  if printf "%s\n" "$NEW_VERSION" > "$stamp" 2>/dev/null; then
    ok "Version stamp written: $stamp ($NEW_VERSION)"
  else
    warn "Could not write version stamp at $stamp (non-fatal; nudge falls back to fail-open)"
  fi
done

# --- Show what will be committed, then commit ALL (git add -A) ---------
echo
say "Staging ALL changes (git add -A)"
git add -A

# Show the user exactly what's about to be committed.
if git diff --cached --quiet; then
  warn "No changes to commit (working tree clean apart from version, or nothing changed)."
  warn "Continuing to tagging."
else
  echo
  echo -e "${BOLD}The following changes will be committed:${NC}"
  echo "-----------------------------------------------"
  git --no-pager diff --cached --stat
  echo "-----------------------------------------------"
  echo
  read -r -p "$(echo -e "Commit ALL of the above as '${TAG}'? [y/N]: ")" COMMIT_OK
  if [[ ! "$COMMIT_OK" =~ ^[Yy]$ ]]; then
    warn "Commit cancelled. Unstaging changes (git reset) and exiting."
    warn "Your files are untouched on disk; nothing was committed or pushed."
    git reset >/dev/null 2>&1
    exit 0
  fi
  git commit -m "${COMMIT_MSG}" || { err "git commit failed"; exit 1; }
  ok "Committed: ${COMMIT_MSG}"
fi

say "Pushing to origin/${DEPLOY_BRANCH}"
git push origin "$DEPLOY_BRANCH" || { err "git push failed"; exit 1; }
ok "Pushed to ${DEPLOY_BRANCH}"

# --- Handle existing tag (retry of a failed build) --------------------
TAG_EXISTS_LOCAL=$(git tag --list "$TAG")
TAG_EXISTS_REMOTE=$(git ls-remote --tags origin "refs/tags/$TAG" 2>/dev/null)

if [[ -n "$TAG_EXISTS_LOCAL" || -n "$TAG_EXISTS_REMOTE" ]]; then
  echo
  warn "Tag ${TAG} already exists (local: ${TAG_EXISTS_LOCAL:+yes}${TAG_EXISTS_LOCAL:-no}, remote: ${TAG_EXISTS_REMOTE:+yes}${TAG_EXISTS_REMOTE:-no})."
  warn "This usually means a previous build for ${TAG} failed and you're retrying."
  read -r -p "$(echo -e "Delete existing tag (and release) and re-create? [y/N]: ")" REUSE
  if [[ ! "$REUSE" =~ ^[Yy]$ ]]; then
    warn "Leaving existing tag in place. Nothing tagged. Exiting."
    exit 0
  fi

  if [[ -n "$TAG_EXISTS_LOCAL" ]]; then
    say "Deleting local tag ${TAG}"
    git tag -d "$TAG" || warn "Could not delete local tag (continuing)"
  fi

  if [[ -n "$TAG_EXISTS_REMOTE" ]]; then
    say "Deleting remote tag ${TAG}"
    git push origin ":refs/tags/${TAG}" || warn "Could not delete remote tag (continuing)"
  fi

  # Delete the GitHub release too, if gh is available + authenticated
  # >>> AUTO_UPDATER_20260821 BEGIN (retry also cleans scalp-releases) <
  # The updater endpoint reads anbu101/scalp-releases — a stale release
  # there for this tag could leave old .sig / latest-*.json assets behind
  # on retry, so delete the tag's release in BOTH repos, not just scalp-app.
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    say "Deleting GitHub release ${TAG} in scalp-app (if any)"
    gh release delete "$TAG" --yes >/dev/null 2>&1 && ok "scalp-app release deleted" || warn "No scalp-app release to delete (or delete skipped)"
    say "Deleting GitHub release ${TAG} in scalp-releases (if any)"
    gh release delete "$TAG" --yes -R anbu101/scalp-releases --cleanup-tag >/dev/null 2>&1 && ok "scalp-releases release + tag deleted" || warn "No scalp-releases release to delete (or delete skipped)"
  else
    warn "gh CLI not available/authenticated — skipping GitHub release delete."
    warn "If a release named ${TAG} exists in scalp-app OR scalp-releases, delete it"
    warn "manually on GitHub before retrying, or the new build may publish stale updater assets."
  fi
  # >>> AUTO_UPDATER_20260821 END <
fi

# --- Tag + push -> triggers the workflow ------------------------------
# Annotated tag (-m). The build markers are read primarily from the COMMIT
# message by the workflow (reliable in CI); the tag annotation carries them
# too as a fallback.
echo
say "Creating annotated tag ${TAG}"
git tag -a "$TAG" -m "${TAG_MSG}" || { err "git tag failed"; exit 1; }

say "Pushing tag ${TAG} (this starts the GitHub Actions build)"
git push origin "$TAG" || { err "git push tag failed"; exit 1; }

echo
ok "Tag ${TAG} pushed. The build workflow is now running."
if [[ "$INCLUDE_INTEL" == "1" ]]; then
  ok "Intel build is INCLUDED in this run."
else
  warn "Intel build SKIPPED. To add Intel later: build locally, then drag the"
  warn "  Scalp-${TAG}-intel.dmg onto the GitHub release's edit page."
fi
echo
echo -e "${BOLD}Next steps:${NC}"
echo -e "  • Build takes ~20 min (ARM + Windows), ~30 min if Intel included."
echo -e "  • Watch progress:  GitHub repo → Actions tab"
echo -e "  • Download installers when done:  GitHub repo → Releases"
echo

# Offer to open the Actions page in the browser
REMOTE_URL=$(git config --get remote.origin.url 2>/dev/null)
# Normalise git@github.com:user/repo.git  OR  https://github.com/user/repo.git
SLUG=$(echo "$REMOTE_URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
if [[ -n "$SLUG" ]]; then
  ACTIONS_URL="https://github.com/${SLUG}/actions"
  read -r -p "$(echo -e "Open ${BOLD}${ACTIONS_URL}${NC} in browser? [Y/n]: ")" OPEN
  if [[ ! "$OPEN" =~ ^[Nn]$ ]]; then
    open "$ACTIONS_URL" 2>/dev/null || warn "Could not open browser."
  fi
fi

ok "Done."