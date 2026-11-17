# Scalp Terminal — notes for Claude

## QA Register (keep it current, every time)

The QA Register is the Sheets artifact "Scalp Terminal — QA Register":
https://claude.ai/artifact/MADnNBvTA3C74Y34wPH55i (tabs: Test Cases, Defects, To-Do).

In the same piece of work, without being asked, update it whenever you:

- **add or change a test**: one row per test case on **Test Cases**, in plain English
  with the expected result. IDs are `TC-<AREA>-<NNNN>`. A new case takes the next number
  in its area, and existing IDs never change.
- **find or fix a defect**: one row on **Defects** with columns Defect ID (`DEF-NNN`),
  description, date/time found, app version where it occurred, machine
  (Anbu / Dhivakar / Others), fixed date, fixed-in version, Test Case IDs added,
  Test Case IDs tested.
- **plan or make a change, a feature or a fix**: a row on **To-Do** (`TD-NNN`), with its
  status moved along as the work goes.

The project doc `claude/qa-register-extract-tests.py` lists every test case found in the
code. Use it to check the Test Cases tab against the code.

If the Sheet cannot be reached in a session (no Claude Docs connector), say so in that
session, and list the rows that still need adding.

<!-- REPO_MAP_20261117 -->
## Start here: REPO_MAP.md

Open `REPO_MAP.md` first. Per strategy (by codename) it names the engine, backtest runner, defaults / Settings / Backtest-form line ranges, panel and test suites, plus the dual tree, shared modules and fences. After adding or moving files run `python3 scripts/gen_repo_map.py` (`--check` says if it is stale). Tests: `python3 scripts/run_suites.py PATH` (failures + summary only).

## Rules

- Every change ships as ONE self-contained, idempotent `apply_<name>.py`: dated fence `NAME_YYYYMMDD`, `.bak-<FENCE>` backups, `--rollback`. Deliver the script alone ("run only the script").
- Dual tree: edit `backend/` and `frontend/`; the script also writes the `desktop/src-tauri/` build copies when they exist.
- `git status` on every target before patching (an `M` is a stop sign); assert the parent fence.
- Read the latest `main`, never a file rebuilt from memory.
- No live deploys Mon–Fri 09:00–15:30 IST.

<!-- PRECOMMIT_20261117 -->
- Commits run pre-commit (ruff errors, dual-tree parity, no backup files, REPO_MAP refresh); pushes are blocked Mon–Fri 09:00–15:30 IST unless `SCALP_PUSH_OK=1`.
