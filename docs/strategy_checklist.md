# Strategy Add / Remove Checklist

Living document for Scalp Terminal. Written 2026-08-03 after the TSG_V1
integration, which surfaced every item on this list the hard way — including
three that only broke on *other people's machines* (license allowlist) or
*one minute before EOD* (paper sweep). Update this file whenever a new
integration point is created.

**The Golden Rule:** the most recently added strategy IS the checklist.
Before adding `NEW_V1`, run:

```bash
grep -rln "TSG_V1" backend frontend license_server \
  --include="*.py" --include="*.js" --include="*.jsx" --include="*.html" \
  | grep -v node_modules
```

Every file in that output needs a decision for `NEW_V1`: add, or consciously
skip with a reason. After integrating, run the **gap sweep** (bottom of this
file) — it must come back empty.

---

## Part 1 — Design decisions BEFORE any code

Lock these as a numbered D-sheet (D1..Dn / LD1..LDn) in conversation before
writing a line. Contradictions are surfaced, not silently resolved.

| Decision | Options seen in the codebase |
|---|---|
| **Trade storage** | Generic `paper_trades` (TSG — everything downstream is free) vs private table (TMA `tma_trades`, SCALP_V3 — requires unions in `paper_trades_routes`, `trade_history_routes`, `telegram_summary_data`, plus a migration) |
| **Exit machinery** | Per-leg GTT SL/TP (SCALP/BB/TMA — needs a GTTMonitor, abort-before-flatten kill) vs basket-level engine exits at 1m closes (TSG — no GTT layer at all, simplest kill) |
| **Launch style** | StrategyRuntimeManager slots vs **standalone async runtime** in `api_server` (IC/PST/TMA/TSG — `slots: []`, own engine thread) |
| **Overnight** | Intraday-only vs carry (IC ONE_NIGHT_MAX). Any strategy that owns its own EOD (even intraday, e.g. TSG's 15:26 vs the generic 15:25 sweep) needs the squareoff exemption — see 2.10 |
| **Backtest parity contract** | What live evaluates and when (e.g. TSG LD2: decisions only at 1m closes) + a written divergence ledger (entry fill, exit slippage, data inputs) |
| **Kill doctrine** | Adapter registered with `app/execution/kill_switch.py`; verified-flat before mode flip; LIVE-only gating |

House rules that apply to every strategy: pure decision core + tests before
any wrapper (`*_live_core.py` pattern); paper validation gates live; no
live-path deploys on trading days; BB_V1 never touched without explicit
confirmation; fenced `── NEW_V1 BEGIN/END ──` comment markers around every
edit for grep-ability and clean revert.

---

## Part 2 — Backend integration points

| # | File | What to add |
|---|---|---|
| 2.1 | `backend/app/strategy/strategy_registry.py` | `STRATEGIES["NEW_V1"]` entry (enabled, broker, timeframe, slots). **This gates the runtime launch — miss it and the strategy silently never starts** (the TSG invisible-panel bug). Include the removal recipe in the comment block. |
| 2.2 | `backend/app/config/strategy_loader.py` | Full default config. Miss it → mode reads OFF everywhere. Defaults ship the *validated* backtest parameters. |
| 2.3 | `backend/app/engine/new_v1/` | Package: `__init__.py` (zero-byte, **load-bearing** — PyInstaller drops the package without it), `new_live_core.py` + `test_new_live_core.py` (pure, no app imports), `new_manager.py`, `new_engine.py`, `new_runtime.py`. Verify every imported API against the real source — signatures drift (`insert_paper_trade` is keyword-only; executor methods are `place_sell_entry/place_buy/place_buy_exit/place_market_sell/get_order_fill`). |
| 2.4 | `backend/app/api/new_v1_state_routes.py` | Panel state GET + square_off POST. Isolated try/except; must return a sane payload when the runtime never launched. |
| 2.5 | `backend/app/jobs/new_live_eod.py` | Scheduled EOD backstop. |
| 2.6 | `backend/app/api_server.py` | Router import + `include_router`; deferred-launch guard in the slot loop; standalone launch behind `STRATEGIES` flag **and** `license_state.license_allows_strategy`; scheduler cron with a **UNIQUE job id** — a cloned id with `replace_existing=True` silently replaces the donor strategy's job (caught in review for TSG). |
| 2.7 | `backend/app/execution/kill_switch.py` | Add to `KILL_STRATEGIES`; runtime registers the adapter via `register_adapter` at boot. |
| 2.8 | `backend/app/api/telegram_api.py` | Strategy filter list (~line 107). |
| 2.9 | `backend/app/api/telegram_summary_data.py` | **Nothing** if rows live in `paper_trades` (generic read). Private table → dedicated card block. |
| 2.10 | `backend/app/db/paper_trade_squareoff.py` | If the strategy owns its own EOD lifecycle (carry, or an exit time ≥ the generic 15:25 sweep), add to `OVERNIGHT_EXEMPT_STRATEGIES` with a dated rationale. Otherwise the sweep force-closes its paper rows and corrupts paper-vs-backtest parity. |
| 2.11 | `backend/app/db/migrations/runner.py` | Only if a private table exists. |
| 2.12 | `backend/app/api/paper_trades_routes.py`, `trade_history_routes.py`, `db/trades_repo.py` | **Nothing** for `paper_trades` strategies (verify the generic SELECT has no whitelist). Private table → isolated union blocks. Live-history union can be deferred until LIVE is enabled. |
| 2.13 | `backend/app/backtest/report/report_engine.py` | Short-code map entry (`"NEW_V1": "NEW"`). |
| 2.14 | `license_server/admin_ui.html` | `ALL_STRATEGIES` list. **Miss it and every friend on a named-strategy license silently never launches the strategy — an ADMIN `*` license masks this on the dev machine.** Deploys with the license server, not the app. |

### Backtest side (if the strategy has a backtest — it should, first)
`backend/app/backtest/new/` runner + tests + `__init__.py` · dispatch in
`api/backtest_routes.py` · `backtest/queue_worker.py`.

---

## Part 3 — Frontend integration points

| # | File | What to add |
|---|---|---|
| 3.1 | `frontend/src/strategies/new_v1/NEWV1Panel.jsx` | Dashboard panel. Two-tap arm/confirm for destructive actions — **`window.confirm`/`alert` are silently blocked in Tauri's webview.** |
| 3.2 | `frontend/src/components/StrategyHost.jsx` | Import, `ACTIVE_STRATEGY_IDS`, META (name + accent), `renderPanel` case. |
| 3.3 | `frontend/src/api.js` | `getNEWV1State` (with offline fallback object) + action helpers. |
| 3.4 | `frontend/src/strategies/registry.js` | Entry with capabilities flags. |
| 3.5 | `frontend/src/pages/Settings.jsx` | The big one, 9 grafts: DEFAULT config const (mirrors 2.2) · state hooks · `loadX/updateX/saveX` · load call in the boot `useEffect` · loading gate condition · RAIL entry · `detailProps` entry · META name/sub · full `case "NEW_V1"` settings form. Merge nested/array config defensively so partial saved configs never render undefined inputs. |
| 3.6 | `frontend/src/pages/PaperTrades.jsx` | Name maps (both `NEW_V1` and `NEW V1` spellings) + CE/PE side list if legs carry option type. |
| 3.7 | `frontend/src/pages/Analytics.jsx` | STRATEGY card (id, label, color, desc). |
| 3.8 | `frontend/src/pages/Connections.jsx` | Color map + filter option. |
| 3.9 | Backtest pages (if applicable) | `Backtest.jsx` panel/form/chips · `RunComparison.jsx` param rows + `EXIT_REASON_KEYS` · `BacktestQueue.jsx` tokens · `SweepBuilder.jsx` axes. |

Stale-closure discipline: any new state read by `buildConfig` lands in its
dep array **in the same commit**; anchor dep-array edits on a neighboring
unique line, never a bare closing bracket.

---

## Part 4 — Build & release plumbing

1. `desktop/build-scalp.sh`: new module dotted names into **Gate-2**
   `REQUIRED_MODULES` and **Gate-3** `REQUIRED` (both lists). A module a
   gate doesn't know about can be silently dropped by PyInstaller and ship
   anyway — this exact failure shipped twice before the gates existed.
2. `.github/workflows/build-release.yml`: same names into the CI REQUIRED
   lists (macOS + Windows) and the Windows marker-verify directory list.
3. Dual-tree: every backend edit lands in `backend/app/...` **and**
   `desktop/src-tauri/backend/app/...`; `diff -r` to prove parity. Frontend
   syncs via the build script.
4. Full `npm run tauri build` — the running app is a frozen bundle; source
   edits are invisible until rebuilt. Launch from `target/release`.

---

## Part 5 — Verification gauntlet (all must pass before build)

```bash
# 1. Reference-count sanity — every touched file:
for f in <touched files>; do printf "%-55s %s\n" "$f" "$(grep -c NEW_V1 $f)"; done

# 2. Compile + syntax:
python3 -m compileall -q backend/app          # both trees
npx esbuild <each .jsx/.js> --loader:.jsx=jsx --outfile=/dev/null

# 3. Pure-core tests (backtest + live) green.

# 4. Integration smoke: drive the real manager with stubbed config/chain/
#    quotes through entry → MID-DAY RESTART → each exit path → flat.
#    The restart leg is mandatory: it caught TSG's unpersisted chain meta
#    (IV checks silently dead after resume).

# 5. GAP SWEEP — must print nothing:
for f in $(grep -rl "TSG_V1\|TMA_V1\|IC_V1" backend frontend license_server \
    --include="*.py" --include="*.js" --include="*.jsx" --include="*.html" \
    | grep -v node_modules | grep -v "/tsg/\|/tma/\|/ic_v1/\|/pst/\|test_\|e2e_"); do
  grep -q "NEW_V1" "$f" || echo "GAP: $f"
done
```

Post-build acceptance: `[NEW][RUNTIME] up` in `~/.scalp-app/logs/backend.log`
(the backend takes 40–45 s to LISTEN) · Settings rail entry showing PAPER ·
dashboard panel rendering · first paper action at the scheduled time.
Then the promotion gate: **paper validation (≥ 2 expiry cycles for
options strategies) with paper-vs-backtest parity on overlapping days**
before `trade_execution_mode` ever reads LIVE.

---

## Part 6 — Removing a strategy

Mirror image, informed by the SCALP_V2/V4 removals:

1. **Flatten first.** Confirm no open live/paper positions and no GTTs;
   set mode OFF; wait a session.
2. Delete in reverse dependency order: frontend panel + all Part-3 list
   entries → api_server registration (router, launch block, cron) →
   routes/jobs files → engine package → registry + loader entries →
   kill_switch list → telegram list → squareoff exemption → report code →
   `admin_ui.html`.
3. The fenced markers make this mechanical:
   `grep -rn "── NEW_V1 BEGIN" ` finds every block; delete marker-to-marker.
4. Build gates + CI: remove the module names (Gate 2/3 fail on *missing*
   modules, not extras — but stale entries rot).
5. Data: decide explicitly — keep historical `paper_trades`/backtest rows
   (harmless; name maps in PaperTrades.jsx can stay for display) or purge.
   Private tables: `DROP` via a migration, never by hand on one machine.
6. Licenses: remove from `ALL_STRATEGIES`; existing tokens carrying the id
   are harmless (the gate just never matches).
7. Run the gap sweep **inverted**: `grep -rn "NEW_V1"` across the repo must
   return only this checklist's history section, if anything.

---

## Part 7 — Scar tissue (why specific items exist)

- **2.1/2.2 both missing** → runtime never launches AND mode reads OFF: the
  strategy is invisible with zero errors anywhere (TSG, 2026-08-02).
- **2.14** → works on the dev machine (ADMIN `*`), dead on every licensed
  machine (TSG, 2026-08-03).
- **2.10** → generic 15:25 sweep vs strategy-owned 15:26 EOD: double-closed
  rows + 1-minute systematic parity corruption (TSG, 2026-08-03).
- **Unique cron ids** → cloned `replace_existing=True` id evicted the donor
  strategy's EOD job (caught pre-ship, TSG 2026-08-02).
- **Restart-path smoke** → unpersisted chain meta = IV breaker silently dead
  after any backend restart (TSG, 2026-08-02).
- **Gate lists** → PyInstaller silently drops syntactically-broken or
  unreferenced modules; the bundle ships and fails at runtime (TSG backtest
  runner, twice, 2026-07-31).
- **Zero-byte `__init__.py` untracked** → module present on disk, absent
  from every frozen bundle (corpus sanitizer, 2026-08-02).
