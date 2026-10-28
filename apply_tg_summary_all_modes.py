#!/usr/bin/env python3
# apply_tg_summary_all_modes.py — TG_SUMMARY_ALL_MODES_20261028
#
# WHY (2026-10-28): Market Makers (Channel 1) was set to Mode = Paper. The
# daily summary is sent without a mode, and the channel filter reads "no
# mode" as LIVE, so a Paper channel could never receive it. The 15:40 tick
# logged "Daily summary — no eligible channels" and the manual curl was
# skipped the same way.
#
# WHAT: the daily summary is exempt from the Paper / Live / All mode filter.
# It goes to every enabled channel with "Daily Summary" ticked. The same
# toggle drives the expiry-day week/month card and the text fallback, so
# they follow. Everything else is unchanged:
#   * Daily Summary tick, channel on/off and the time window still apply.
#   * Trade activity, position updates, critical and personal alerts keep
#     the mode filter exactly as before.
#
# Touches:
#   backend/app/api/telegram_api.py       (_iter_active_channels, + desktop mirror)
#   frontend/src/pages/Connections.jsx    (Daily Summary hint text)
#   backend/app/jobs/test_tg_summary_all_modes.py   NEW (30 checks)
#
# USAGE (repo root):
#   python3 apply_tg_summary_all_modes.py --dry-run
#   python3 apply_tg_summary_all_modes.py
#   python3 apply_tg_summary_all_modes.py --revert
# Then: ./desktop/build-scalp.sh both
import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "TG_SUMMARY_ALL_MODES_20261028"
BAK = ".bak-" + FENCE
MIRROR = Path("desktop/src-tauri/backend")
TEST_REL = "backend/app/jobs/test_tg_summary_all_modes.py"
F = "# ── " + FENCE + " ──"

TG_EDITS = [
('''        if not _mode_matches(ch, mode):
            continue
        if not _strategy_matches(ch, strategy_id):''',
'''        # ''' + F[2:] + ''' the daily summary (and the expiry
        # card / text fallback on the same toggle) is a whole-day digest: it
        # goes to every channel with Daily Summary ticked, whatever its
        # Paper / Live / All mode filter. Every other type keeps the filter.
        if notif_key != NOTIF_DAILY_SUMMARY and not _mode_matches(ch, mode):
            continue
        if not _strategy_matches(ch, strategy_id):'''),
]

JSX_EDITS = [
('''    desc: "End-of-day P&L card at 15:40" },   /* ── CAS_2026 ── */''',
'''    desc: "End-of-day P&L card at 15:40 — sent whatever the Mode filter" },   /* ── CAS_2026 ── ── ''' + FENCE + ''' ── */'''),
]

# (rel, edits, prerequisite markers, mirrored)
TARGETS = [
    ("backend/app/api/telegram_api.py", TG_EDITS,
     ["TG_PERSONAL_ALERTS_20261027", "def _iter_active_channels"], True),
    ("frontend/src/pages/Connections.jsx", JSX_EDITS, ["TG_PERSONAL_ALERTS_20261027"], False),
]

SUITES = ["app/jobs/test_tg_summary_all_modes.py",
          "app/jobs/test_tg_personal_alerts.py",
          "app/jobs/test_kite_login_nudge.py"]

TEST_SRC = r'''# backend/app/jobs/test_tg_summary_all_modes.py
# ── TG_SUMMARY_ALL_MODES_20261028 ── the daily summary (and the expiry-day
# card, which rides the same toggle) is a whole-day digest: it goes to every
# enabled channel with "Daily Summary" ticked, whatever that channel's
# Paper / Live / All mode filter says. Every other alert type keeps the mode
# filter exactly as before. Hermetic: HOME points at a temp folder and all
# Telegram sends are replaced by recorders.
#
#   cd backend && HOME=$(mktemp -d) python3 app/jobs/test_tg_summary_all_modes.py
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.api.telegram_api as TG                    # noqa: E402
import app.services.telegram_scheduler as TS         # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


IST = timezone(timedelta(hours=5, minutes=30))
AT_1540 = datetime(2026, 10, 28, 15, 40, 27, tzinfo=IST)     # real clock, the miss
AT_2000 = datetime(2026, 10, 28, 20, 0, 0, tzinfo=IST)       # manual curl later

GROUP, ME, LIVEONLY = "-1003971039466", "605541748", "-1009999999999"


def chan(i, chat, mode="all", sched=None, enabled=True, **notif):
    n = {"tradeActivity": True, "positionUpdates": False, "dailySummary": True,
         "criticalAlerts": True, "personalAlerts": False}
    n.update(notif)
    return {"id": f"channel_{i}", "name": f"c{i}", "chat_id": chat, "enabled": enabled,
            "strategy_filter": [], "mode_filter": mode, "notifications": n,
            "schedule": sched or {"enabled": False, "start": "09:15", "end": "15:45"}}


def set_cfg(*channels):
    TG.TELEGRAM_CONFIG.clear()
    TG.TELEGRAM_CONFIG.update(TG._normalize_config({"bot_token": "T", "channels": list(channels)}))


def targets(key, mode=None, now=AT_1540, sid=None):
    return sorted(c for _t, c, _ch in TG._iter_active_channels(key, mode=mode, now=now,
                                                               strategy_id=sid))


DS = TG.NOTIF_DAILY_SUMMARY

# ── 1) today's exact config: group = Paper + summary ON, private = summary OFF ──
set_cfg(chan(1, GROUP, mode="paper", positionUpdates=True),
        chan(2, ME, mode="all", sched={"enabled": False, "start": "09:15", "end": "10:00"},
             tradeActivity=False, dailySummary=False, criticalAlerts=False,
             personalAlerts=True))
check("today: Paper group receives the daily summary at 15:40",
      targets(DS) == [GROUP], repr(targets(DS)))
check("today: manual trigger at 20:00 also reaches the group",
      targets(DS, now=AT_2000) == [GROUP], repr(targets(DS, now=AT_2000)))
check("today: private chat (summary OFF) still gets nothing", ME not in targets(DS))

# ── 2) every mode filter receives it ─────────────────────────────────────────
for mf in ("paper", "live", "all"):
    set_cfg(chan(1, GROUP, mode=mf), chan(2, ME, dailySummary=False))
    check(f"mode={mf}: summary delivered", targets(DS) == [GROUP], repr(targets(DS)))
    for passed in (None, "live", "paper"):
        check(f"mode={mf}: summary ignores caller mode={passed}",
              targets(DS, mode=passed) == [GROUP])

# ── 3) the Daily Summary tick, enabled switch and schedule still rule ───────
set_cfg(chan(1, GROUP, mode="paper", dailySummary=False), chan(2, ME, mode="live"))
check("summary OFF on group -> group skipped", targets(DS) == [ME], repr(targets(DS)))
set_cfg(chan(1, GROUP, mode="paper", enabled=False), chan(2, ME, dailySummary=False))
check("disabled channel -> skipped", targets(DS) == [])
set_cfg(chan(1, GROUP, mode="paper", sched={"enabled": True, "start": "09:15", "end": "15:40"}),
        chan(2, ME, dailySummary=False))
check("schedule window ending 15:40 still excludes the 15:40 summary", targets(DS) == [])
set_cfg(chan(1, GROUP, mode="paper", sched={"enabled": True, "start": "09:15", "end": "15:45"}),
        chan(2, ME, dailySummary=False))
check("schedule window to 15:45 includes it", targets(DS) == [GROUP])

# ── 4) everything else keeps the mode filter (no behaviour change) ──────────
set_cfg(chan(1, GROUP, mode="paper"), chan(2, LIVEONLY, mode="live"))
TA, CA, PU = TG.NOTIF_TRADE_ACTIVITY, TG.NOTIF_CRITICAL_ALERTS, TG.NOTIF_POSITION_UPDATES
check("trade: PAPER trade -> paper channel only", targets(TA, mode="paper") == [GROUP])
check("trade: LIVE trade -> live channel only", targets(TA, mode="live") == [LIVEONLY])
check("trade: untagged (None=live) unchanged", targets(TA, mode=None) == [LIVEONLY])
check("critical: LIVE critical not in paper group", targets(CA, mode="live") == [LIVEONLY])
check("position updates: paper snapshot -> paper channel",
      targets(PU, mode="paper") == [])          # positionUpdates OFF in chan() default
set_cfg(chan(1, GROUP, mode="paper", positionUpdates=True), chan(2, LIVEONLY, mode="live"))
check("position updates: live snapshot not in paper group",
      targets(PU, mode="live") == [])

# ── 5) the scheduler path end to end (card + expiry), sends recorded ───────
SENT_CARD, SENT_EXP = [], []
TS.build_card_data_once = lambda: object()
TS.send_daily_summary_card = lambda *, bot_token, chat_id, text_fallback, data: \
    SENT_CARD.append(chat_id)
TS.build_expiry_card_data_once = lambda expiry: object()
TS.send_expiry_summary_card = lambda *, bot_token, chat_id, data: SENT_EXP.append(chat_id)
TS.is_expiry_day = lambda d: True

set_cfg(chan(1, GROUP, mode="paper"),
        chan(2, ME, tradeActivity=False, dailySummary=False, criticalAlerts=False,
             personalAlerts=True))
s = TS.TelegramScheduler()
s._handle_daily_summary(AT_1540)
check("scheduler @15:40: card sent to the Paper group", SENT_CARD == [GROUP], repr(SENT_CARD))
s._handle_daily_summary(AT_1540)
check("scheduler @15:40: second tick does not resend", SENT_CARD == [GROUP], repr(SENT_CARD))
s._handle_expiry_summary(AT_1540)
check("scheduler @15:40 expiry day: expiry card to the Paper group",
      SENT_EXP == [GROUP], repr(SENT_EXP))
SENT_CARD.clear()
s.run_daily_summary_now()
check("manual trigger (debug route): card to the Paper group", SENT_CARD == [GROUP],
      repr(SENT_CARD))

# ── 6) the text fallback fans out the same way ─────────────────────────────
SENT_TXT = []
TG.send_telegram_message = (lambda tok, chat, msg, parse_mode="HTML", reply_markup=None:
                            SENT_TXT.append(chat) or True)
empty = {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}
TG._query_today_live_summary = lambda: dict(empty)
TG._query_today_paper_summary = lambda: dict(empty)
TG.notify_daily_summary()
check("text fallback: reaches the Paper group", SENT_TXT == [GROUP], repr(SENT_TXT))

total = PASSES[0] + len(FAILS)
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")
'''


def die(msg, code=2):
    print("\nABORTED — nothing written.\n  " + msg)
    sys.exit(code)


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path(__file__).resolve().parent]:
        if (c / "backend/app/api/telegram_api.py").exists():
            return c.resolve()
    die("Could not find the repo root. Run from the repo root or pass --repo PATH.")


def dests(root, rel, mirrored):
    out = [root / rel]
    if mirrored and rel.startswith("backend/"):
        m = root / MIRROR / Path(rel).relative_to("backend")
        if m.exists() or (rel == TEST_REL and m.parent.exists()):
            out.append(m)
    return out


def git_dirty(root, rel):
    if not (root / ".git").exists() or not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", rel],
                       capture_output=True, text=True)
    return any(l[:2].strip() and not l.startswith("??") for l in r.stdout.splitlines())


def patch(text, edits, label):
    for i, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            die(f"{label}: anchor #{i} found {n}× (expected 1) — not the version this "
                f"script targets. Push it to GitHub and ask for a rebase.\n  anchor: "
                f"{old.strip().splitlines()[0][:90]}")
        text = text.replace(old, new, 1)
    return text


def run_suite(root, staged):
    tmp = Path(tempfile.mkdtemp(prefix="tgsam_suite_"))
    try:
        ign = shutil.ignore_patterns("__pycache__", "*.bak-*", "*.pyc", "data", "secrets")
        shutil.copytree(root / "backend/app", tmp / "backend/app", ignore=ign)
        if (root / "license_server").exists():
            shutil.copytree(root / "license_server", tmp / "license_server", ignore=ign)
        for rel, txt in staged.items():
            if rel.startswith("backend/"):
                (tmp / rel).write_text(txt, encoding="utf-8")
        results = []
        for t in SUITES:
            if not (tmp / "backend" / t).exists():
                results.append((t, True, "(not present — skipped)"))
                continue
            home = Path(tempfile.mkdtemp(prefix="home_", dir=tmp))
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       SCALP_TEST_MODE="1", PYTHONPATH=str(tmp / "backend"))
            env.pop("LICSRV_DATA_DIR", None)
            r = subprocess.run([sys.executable, t], cwd=tmp / "backend", env=env,
                               capture_output=True, text=True, timeout=300)
            results.append((t, r.returncode == 0, (r.stdout + r.stderr).strip()))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def jsx_check(root, txt):
    fe = root / "frontend"
    eb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not eb.exists():
        return None, "esbuild not installed in frontend/ — JSX parse check skipped"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "Connections.jsx"
        f.write_text(txt, encoding="utf-8")
        r = subprocess.run([str(eb), str(f), "--loader:.jsx=jsx", "--log-level=error",
                            "--outfile=" + str(Path(td) / "o.js")],
                           capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "esbuild parse OK").strip()


def revert(root):
    n = 0
    for rel, _e, _p, mirrored in TARGETS:
        for p in dests(root, rel, mirrored):
            b = p.with_name(p.name + BAK)
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
                n += 1
                print(f"  restored  {p.relative_to(root)}")
    for p in dests(root, TEST_REL, True):
        if p.exists() and FENCE in p.read_text(encoding="utf-8", errors="ignore"):
            p.unlink()
            print(f"  removed   {p.relative_to(root)}")
    print(f"\nRevert done ({n} file(s) restored). Rebuild: ./desktop/build-scalp.sh both")
    return 0


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"{FENCE}\nRepo: {root}")
    if a.revert:
        return revert(root)

    missing = [rel for rel, *_ in TARGETS if not (root / rel).exists()]
    if missing:
        die("missing files: " + ", ".join(missing))
    files = [p for rel, _e, _pa, mi in TARGETS for p in dests(root, rel, mi) if p.exists()]
    fenced = [p for p in files if FENCE in p.read_text(encoding="utf-8")]
    if fenced and len(fenced) == len(files):
        print("Already applied — nothing to do.")
        return 0
    if fenced:
        die("mixed state — already fenced:\n  " + "\n  ".join(
            str(p.relative_to(root)) for p in fenced) + "\n  Run --revert first.")

    staged = {}
    for rel, edits, parents, _mi in TARGETS:
        if git_dirty(root, rel) and not a.allow_dirty:
            die(f"{rel} has uncommitted modifications (git status). Commit/stash first, "
                f"or re-run with --allow-dirty if that is intended.")
        src = (root / rel).read_text(encoding="utf-8")
        for pf in parents:
            if pf not in src:
                die(f"{rel}: prerequisite marker '{pf}' not found "
                    f"(apply_tg_personal_alerts.py must be applied first).")
        staged[rel] = patch(src, edits, rel)
    # the mirror must match the main tree before we overwrite it with the patch
    rel = "backend/app/api/telegram_api.py"
    m = root / MIRROR / Path(rel).relative_to("backend")
    if m.exists() and m.read_text(encoding="utf-8") != (root / rel).read_text(encoding="utf-8"):
        die(f"{m.relative_to(root)} differs from {rel} — sync the trees "
            f"(./desktop/build-scalp.sh backend) and re-run.")
    staged[TEST_REL] = TEST_SRC

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                t = Path(td) / (rel.replace("/", "_"))
                t.write_text(txt, encoding="utf-8")
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    print("  py_compile: OK")
    ok, msg = jsx_check(root, staged["frontend/src/pages/Connections.jsx"])
    print(f"  Connections.jsx: {msg}")
    if ok is False:
        die("Connections.jsx does not parse after the edit.")

    print("  running suites…")
    bad = False
    for name, passed, out in run_suite(root, staged):
        summ = next((l for l in reversed(out.splitlines()) if "checks" in l or "skipped" in l), "")
        print(f"    {'PASS' if passed else 'FAIL'}  {name}: {summ}")
        if not passed:
            bad = True
            print("      " + "\n      ".join(out.splitlines()[-20:]))
    if bad:
        die("a suite failed (see above).")

    plan = []
    for rel, txt in staged.items():
        mirrored = next((t[3] for t in TARGETS if t[0] == rel), True)
        for d in dests(root, rel, mirrored):
            plan.append((d, txt))
    if a.dry_run:
        for d, _ in plan:
            print(f"  would write  {d.relative_to(root)}")
        print("\nDry run OK — nothing written.")
        return 0

    backups = []
    try:
        for d, _ in plan:
            if d.exists():
                b = d.with_name(d.name + BAK)
                shutil.copy2(d, b)
                backups.append((d, b))
        for d, txt in plan:
            d.parent.mkdir(parents=True, exist_ok=True)
            tmpf = d.with_name(d.name + ".tmp-" + FENCE)
            tmpf.write_text(txt, encoding="utf-8")
            os.replace(tmpf, d)
            print(f"  wrote  {d.relative_to(root)}")
    except Exception as e:
        for d, b in backups:
            shutil.copy2(b, d)
        for d, _ in plan:
            if d not in [x for x, _ in backups] and d.exists():
                d.unlink()
        die(f"write failed ({e!r}) — every file restored.")

    print("""
Done. Next:
  1. ./desktop/build-scalp.sh both      (backend + Connections hint text)
  2. Restart the app, then send today's card:
       curl http://127.0.0.1:47321/api/telegram/debug/run-daily-summary
     → arrives in Market Makers (Mode stays Paper).""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
