#!/bin/bash
# =====================================================================
#  vet_diag_20261106.sh  —  READ-ONLY diagnostics, Velvet (VET_V1)
#  Question: why did VET_V1 miss the 09:29 exit/flip on 2026-11-06,
#            and what wrote its config at 09:20 / 09:21?
#
#  Changes NOTHING: no config, DB, log or code is modified.
#  Writes one report:  ~/Desktop/vet_diag_20261106.txt
#  Tokens/keys/chat ids in the report are masked.
#  Run:  bash vet_diag_20261106.sh
# =====================================================================

DAY="2026-11-06"
FROM="$DAY 09:10:00"
TO="$DAY 09:50:00"
ROOT="$HOME/.scalp-app"
REPO="$HOME/dev/scalp-app"
OUT="$HOME/Desktop/vet_diag_20261106.txt"
TMP=$(mktemp -d /tmp/vetdiag.XXXX)

sec()  { printf '\n\n==================== %s ====================\n' "$1"; }
fst()  { stat -f '%Sm  %10z  %N' -t '%Y-%m-%d %H:%M:%S' "$1" 2>/dev/null; }
dbq()  { sqlite3 -readonly -cmd '.timeout 5000' -header -separator ' | ' "$1" "$2" 2>&1; }
dbq0() { sqlite3 -readonly -cmd '.timeout 5000' -noheader "$1" "$2" 2>/dev/null; }

# lines from today: contain the ISO date, or carry no ISO date at all
today_f()  { awk -v d="$DAY" 'index($0,d)>0 || $0 !~ /20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]/'; }
# lines stamped 09:10:00 – 09:50:59
window_f() { awk '/09:(1[0-9]|[2-4][0-9]|50):[0-9][0-9]/'; }

{
sec "0. Machine"
date; sw_vers 2>/dev/null; uname -m
echo "ROOT=$ROOT   REPO=$REPO"
[ -d "$REPO" ] && (cd "$REPO" && echo "git HEAD: $(git rev-parse --short HEAD 2>/dev/null)  branch: $(git branch --show-current 2>/dev/null)")

sec "1. Scalp processes running now (lstart = when this process started)"
ps -axo pid,lstart,etime,command | grep -i -E 'scalp|uvicorn|api_server' | grep -v grep

sec "2. VET json files under ~/.scalp-app (mtime + contents)"
find "$ROOT" -type f -iname '*vet*' -name '*.json' -size -300k ! -path '*/corpus/*' 2>/dev/null > "$TMP/vetjson"
while IFS= read -r f; do
  echo; echo "--- $(fst "$f")"
  cat "$f"; echo
done < "$TMP/vetjson"

sec "3. Every file under ~/.scalp-app last written between 09:10 and 09:50 today"
find "$ROOT" -type f -newermt "$FROM" ! -newermt "$TO" ! -path '*/corpus/*' 2>/dev/null > "$TMP/win"
while IFS= read -r f; do fst "$f"; done < "$TMP/win" | sort

sec "4. Every JSON under ~/.scalp-app modified today (any time)"
find "$ROOT" -type f -name '*.json' -newermt "$DAY 00:00:00" -size -500k ! -path '*/corpus/*' 2>/dev/null > "$TMP/json"
while IFS= read -r f; do fst "$f"; done < "$TMP/json" | sort

sec "5. Log files touched today"
{ find "$ROOT" -type f \( -iname '*.log' -o -iname '*.log.*' -o -iname '*audit*' -o -iname '*.out' \) \
       -newermt "$DAY 00:00:00" ! -path '*/corpus/*' ! -name '*.db*' 2>/dev/null
  find "$HOME/Library" -ipath '*scalp*' -type f \( -iname '*.log' -o -iname '*.log.*' \) \
       -newermt "$DAY 00:00:00" 2>/dev/null
} | sort -u > "$TMP/logs"
while IFS= read -r f; do fst "$f"; done < "$TMP/logs"

sec "6a. 09:10–09:50 — VET / config / restart / login / error lines"
while IFS= read -r f; do
  echo; echo "--- $f"
  today_f < "$f" | window_f \
    | grep -i -E 'vet|velvet|config|setting|read_degraded|startup|starting|started|shutdown|restart|boot|uvicorn|login|token|kite|ticker|websocket|traceback|exception|error|fail' \
    | tail -n 400
done < "$TMP/logs"

sec "6b. VET heartbeat — VET log lines per 5-min bucket today"
while IFS= read -r f; do today_f < "$f"; done < "$TMP/logs" \
  | grep -i -E 'vet|velvet' \
  | awk 'match($0,/(0[89]|1[0-5]):[0-5][0-9]:[0-5][0-9]/){h=substr($0,RSTART,2); m=substr($0,RSTART+3,2); printf "%s:%02d\n", h, int(m/5)*5}' \
  | sort | uniq -c

sec "6c. All VET lines today (first 80 / last 200)"
while IFS= read -r f; do today_f < "$f" | grep -i -E 'vet|velvet' | sed "s|^|[$(basename "$f")] |"; done < "$TMP/logs" > "$TMP/vetlines"
echo "total VET lines today: $(wc -l < "$TMP/vetlines")"
head -n 80 "$TMP/vetlines"; echo "   ....."; tail -n 200 "$TMP/vetlines"

sec "6d. Tracebacks / exceptions today (any strategy, last 150)"
while IFS= read -r f; do today_f < "$f" | grep -i -E -A3 'traceback|exception|read_degraded' | sed "s|^|[$(basename "$f")] |"; done < "$TMP/logs" | tail -n 150

sec "7. Databases"
find "$ROOT" -maxdepth 3 -type f -name '*.db' ! -path '*/corpus/*' 2>/dev/null > "$TMP/dbs"
while IFS= read -r db; do
  base=$(basename "$db")
  echo; echo "########## DB: $(fst "$db")"
  dbq0 "$db" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" > "$TMP/tables"
  echo "tables: $(tr '\n' ' ' < "$TMP/tables")"
  while IFS= read -r t; do
    lt=$(echo "$t" | tr 'A-Z' 'a-z')
    # config / settings / snapshot / parity / shadow tables: schema + latest rows
    if echo "$lt" | grep -q -E 'vet|config|setting|snapshot|parity|shadow'; then
      echo; echo "--- [$base] $t — schema + latest 20 rows"
      dbq0 "$db" "SELECT sql FROM sqlite_master WHERE name='$t';"
      dbq "$db" "SELECT * FROM \"$t\" ORDER BY rowid DESC LIMIT 20;"
    fi
    # log / audit / event tables: latest VET rows by text match
    if echo "$lt" | grep -q -E 'audit|log|event|alert'; then
      cols=$(dbq0 "$db" "SELECT name FROM pragma_table_info('$t') WHERE upper(type) LIKE '%TEXT%' OR upper(type) LIKE '%CHAR%' OR type='';")
      if [ -n "$cols" ]; then
        where=$(echo "$cols" | awk -v q="'" '{printf "%s\"%s\" LIKE %s%%VET%%%s", (NR>1?" OR ":""), $0, q, q}')
        echo; echo "--- [$base] $t — schema + latest 300 VET rows"
        dbq0 "$db" "SELECT sql FROM sqlite_master WHERE name='$t';"
        dbq "$db" "SELECT * FROM \"$t\" WHERE $where ORDER BY rowid DESC LIMIT 300;"
      fi
    fi
    # any table with strategy_id (not the big backtest store): latest VET rows
    if [ "$base" != "backtest.db" ] && dbq0 "$db" "SELECT 1 FROM pragma_table_info('$t') WHERE name='strategy_id';" | grep -q 1; then
      echo; echo "--- [$base] $t — latest 25 VET rows"
      dbq "$db" "SELECT * FROM \"$t\" WHERE strategy_id LIKE 'VET%' ORDER BY rowid DESC LIMIT 25;"
    fi
  done < "$TMP/tables"
done < "$TMP/dbs"

sec "8. Code: who can write a strategy config, and how parity detects 'settings saved'"
if [ -d "$REPO/backend" ]; then
  echo "--- callers of save_strategy_config"
  grep -rn --include='*.py' 'save_strategy_config' "$REPO/backend" | grep -v '/tests/' | grep -v '\.bak'
  echo; echo "--- parity 'saved during the session' source"
  grep -rn --include='*.py' --include='*.jsx' -i -E 'saved during the session|settings were saved|opening config' "$REPO/backend" "$REPO/frontend/src" 2>/dev/null | grep -v '\.bak' | head -n 20 > "$TMP/hits"
  cat "$TMP/hits"
  while IFS=: read -r file line _; do
    [ -f "$file" ] || continue
    s=$((line-60)); [ $s -lt 1 ] && s=1
    echo; echo "----- $file lines $s-$((line+25))"
    sed -n "${s},$((line+25))p" "$file"
  done < <(grep -E '\.py:' "$TMP/hits" | head -n 2)
else
  echo "repo not found at $REPO — skipped"
fi

sec "END"
} 2>&1 | perl -pe 's/((?:access|request|bot)_?token|api_?key|api_?secret|secret|password|chat_?id|totp)(["\x27]?\s*[:=]\s*["\x27]?)[^\s"\x27,}]{4,}/$1$2***MASKED***/gi' > "$OUT"

rm -rf "$TMP"
echo "Report written: $OUT  ($(wc -l < "$OUT") lines)"
open -R "$OUT" 2>/dev/null
