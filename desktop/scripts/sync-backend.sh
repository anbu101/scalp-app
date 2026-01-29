#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/../backend"
DEST="$ROOT/src-tauri/resources/backend"

echo "[SYNC] $SRC → $DEST"

rm -rf "$DEST"
mkdir -p "$DEST"

rsync -a \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude ".git" \
  --exclude "venv" \
  "$SRC/" "$DEST/"

echo "[SYNC] Done"
