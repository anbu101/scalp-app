#!/usr/bin/env python3
# =====================================================================
#  apply_untrack_backups.py  —  fence UNTRACK_BAK_20261126   (on main 7847c62f)
#  DEF-046 · TD-107
#
#  1,191 apply-script backups (*.bak-<FENCE>) were tracked in git, against
#  the rule that they stay out (.gitignore and the no-backup-files hook have
#  stopped NEW ones since PRECOMMIT_20261117). This untracks them:
#  `git rm --cached` — the files STAY ON DISK, so every apply script's
#  --rollback still works on this Mac.
#  KEPT tracked (12): the backups a test reads as its "original" for a
#  row-for-row comparison (test_tsg_sl_per_lot, test_ic2_hedge_flat,
#  test_vet_dte_lots, …) — without them a fresh clone / CI would skip or
#  fail those comparisons.
#  Repository hygiene only: no file content changes, the app is unchanged.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_untrack_backups.py
#  then commit:  git commit -m 'Untrack apply-script backups (DEF-046)'
#  (the 1,179 removals are already staged; nothing else is staged by it)
#  Options: --repo PATH  --rollback (before you commit: re-stages them; after: git revert the commit)
# =====================================================================
import os, subprocess, sys

FENCE = "UNTRACK_BAK_20261126"
KEEP = set([
 "backend/app/backtest/ic/backtest_ic_runner.py.bak-IC2_EXPIRY_NEXT_20261103",
 "backend/app/backtest/ic/backtest_ic_runner.py.bak-IC2_HEDGE_FLAT_20261117",
 "backend/app/backtest/ic/backtest_ic_runner.py.bak-IC2_SHORT_TP_SKIP_EXP_20261015",
 "backend/app/backtest/ic/backtest_ic_runner.py.bak-IC_ADJ_FILL_OPEN_20261101",
 "backend/app/backtest/ic/ic_v1_engine.py.bak-IC2_HEDGE_FLAT_20261117",
 "backend/app/backtest/tma/backtest_tma_v2_runner.py.bak-TMA2_DTE_LOTS_20260922",
 "backend/app/backtest/tsg/backtest_tsg_runner.py.bak-TSG_SL_PER_LOT_20261015",
 "backend/app/backtest/vet/backtest_vet_runner.py.bak-VET_DTE_LOTS_20261005",
 "backend/app/engine/tma/tma_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015",
 "backend/app/engine/tma2/tma2_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015",
 "backend/app/engine/vet/vet_manager.py.bak-VET_DTE_LIVE_20261005",
 "backend/app/engine/vet/vet_manager.py.bak-VET_WING_FAR_20261101"
])


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")) or not os.path.isdir(os.path.join(repo, ".git")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")
LIST = os.path.join(repo, ".git", FENCE + ".list")


def git(*a, inp=None):
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, input=inp)
    if r.returncode != 0:
        die(f"git {' '.join(a[:3])} … failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def chunks(xs, n=200):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


if "--rollback" in args:
    if not os.path.exists(LIST):
        die(f"no {os.path.relpath(LIST, repo)} — nothing to roll back here (after a commit: git revert it)")
    paths = [p for p in open(LIST, encoding="utf-8").read().split("\n") if p]
    staged = set(git("diff", "--cached", "--name-only", "--diff-filter=D").split("\n"))
    todo = [p for p in paths if p in staged and os.path.exists(os.path.join(repo, p))]
    if not todo:
        die("none of the untracked backups is still staged for removal — already committed? Use git revert")
    for c in chunks(todo):
        git("add", "-f", "--", *c)
    os.remove(LIST)
    say(f"rolled back: {len(todo)} backup(s) tracked again (index restored)")
    sys.exit(0)

ign = open(os.path.join(repo, ".gitignore"), encoding="utf-8").read() if os.path.exists(os.path.join(repo, ".gitignore")) else ""
if "*.bak-*" not in ign.split("\n"):
    die(".gitignore lacks '*.bak-*' (PRECOMMIT_20261117) — pull the latest main first")
tracked = [p for p in git("ls-files", "-z", "--", "*.bak-*").split("\0") if p]
missing_keep = sorted(k for k in KEEP if k not in tracked)
todo = sorted(p for p in tracked if p not in KEEP)
if not todo:
    say(f"✓ already applied: no apply-script backup is tracked apart from the {len(KEEP) - len(missing_keep)} "
        f"a test reads — nothing to do")
    sys.exit(0)
gone = [p for p in todo if not os.path.exists(os.path.join(repo, p))]
if gone:
    die(f"{len(gone)} tracked backup(s) are not on disk (e.g. {gone[0]}) — untracking them would delete them "
        f"from the repo for good. Restore them first (git checkout -- <path>), then run this again")
with open(LIST, "w", encoding="utf-8") as f:
    f.write("\n".join(todo) + "\n")
for c in chunks(todo):
    git("rm", "--cached", "--quiet", "--", *c)
still = [p for p in todo if not os.path.exists(os.path.join(repo, p))]
if still:
    die(f"{len(still)} file(s) vanished from disk after git rm --cached — check {still[0]}")
left = [p for p in git("ls-files", "-z", "--", "*.bak-*").split("\0") if p]
say(f"  untracked {len(todo)} backup(s); all still on disk")
say(f"  kept tracked: {len(left)} (read by tests: {', '.join(sorted(os.path.basename(p) for p in left)[:3])} …)")
if missing_keep:
    say(f"  note: {len(missing_keep)} expected test reference backup(s) were not tracked: {missing_keep[0]} …")
say(f"\n✓ {FENCE} applied (index only; the files stay on disk).")
say("  Commit:  git commit -m 'Untrack apply-script backups (DEF-046)'")
say("  Undo before committing: python3 apply_untrack_backups.py --rollback   (after: git revert the commit)")
