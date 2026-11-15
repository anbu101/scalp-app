#!/usr/bin/env bash
# One command per video:   marketing/promo/promo.sh trailer-mass            → out/trailer-mass/trailer-mass.mp4
#                          marketing/promo/promo.sh trailer-mass --stills 1 6 12.5
#                          marketing/promo/promo.sh trailer-mass --check    (redaction check)
# fence: PROMO_TOOLKIT_20261115
set -euo pipefail
cd "$(dirname "$0")"
NAME="${1:?usage: promo.sh <video-name> [--stills t1 t2 …]}"; shift || true
[ -f "src/videos/$NAME.jsx" ] || { echo "no src/videos/$NAME.jsx — pick one of: $(ls src/videos | sed 's/\.jsx//' | tr '\n' ' ')"; exit 1; }
for bin in node python3 ffmpeg; do command -v $bin >/dev/null || { echo "missing: $bin"; exit 1; }; done
[ -d node_modules ] || npm install --no-audit --no-fund
# the landscape promo shows the real Telegram EOD card, rendered from fictional demo data
if grep -q "eod_card.png" "src/videos/$NAME.jsx" && [ ! -f src/assets/eod_card.png ]; then
  mkdir -p src/assets && python3 scripts/make_eod_card.py src/assets/eod_card.png
fi
node build.mjs "$NAME"
mkdir -p "out/$NAME"
if [ -z "${1:-}" ] && [ -f "music/$NAME.py" ]; then python3 "music/$NAME.py" "out/$NAME/music.wav"; fi
python3 scripts/render.py "$NAME" "$@"
