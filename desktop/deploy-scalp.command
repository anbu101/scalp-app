#!/usr/bin/env bash
# desktop/deploy-scalp.command — ── RELEASE_STAMP_GUARD_20261127 ── (DEF-112) RETIRED
#
# This was an older copy (May 2026) of the release script. It bumped only
# tauri.conf.json and never wrote backend/app/version_stamp.txt, so v12.0.22,
# cut with it on 27 Nov, reported v12.0.21 to the backend: every install
# showed a false "newer version is available" banner.
# The release script is ./deploy-scalp.command at the REPO ROOT.
echo
echo "✗ This release script is retired (DEF-112)."
echo "  Use the one at the repo root:  cd ~/dev/scalp-app && ./deploy-scalp.command"
echo
exit 1
