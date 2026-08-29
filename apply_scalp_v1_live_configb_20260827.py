#!/usr/bin/env python3
# apply_scalp_v1_live_configb_20260827.py
#
# CONFIG B — LIVE/PAPER WIRING — fence: SCALP_V1_LIVE_CONFIGB_20260827
#
# Two halves, both needed before Config B can run outside the backtest:
#
# A. VWAP TOLERANCE — UNCLAMP THE SETTINGS FIELD.
#    SCALP_V1_LIVE_SETTINGS_20260825 deliberately clamped min_below_pts to
#    >= 0 on the live surface, with the reason recorded in that script: the
#    negative "tolerance band" was an unvalidated backtest exploration and
#    had no business reaching live. Config B seals -10 as a real parameter,
#    so that reason no longer holds and the clamp is removed. The field now
#    accepts negatives, and the helper text states what a negative MEANS so
#    nobody has to remember the sign convention.
#
# B. ATM SKEW — WIRE IT TO THE LIVE PATH.
#    In the backtest the gate lives in the RUNNER, not strategy_engine, so
#    live had no equivalent. It is added at the exact live analogue: in
#    zerodha_tick_engine, immediately after strategy.on_candle() returns a
#    sell signal and BEFORE any routing/order call.
#
#    PRICE SOURCE: LTPStore only holds SUBSCRIBED symbols, and the ATM pair
#    is frequently outside the premium-band selection universe (near expiry
#    especially), so a one-shot kite_data.ltp() quote for the two ATM legs is
#    used instead. ~6 signals/day makes the rate-limit and latency cost
#    negligible, and it is the same source the warmup backfill already uses
#    for spot.
#
#    FAIL-CLOSED, matching the backtest: no spot, no ATM pair, or a failed
#    quote BLOCKS the entry and writes an audit line. On a live seller that
#    is the safe direction — a missed trade, never an unfiltered one.
#
#    PARITY NOTE (deliberate, and worth knowing): the backtest samples the
#    ATM pair from 1-minute CLOSES at the signal candle; live samples a REST
#    quote a few hundred ms later. On a fast tick the two can disagree near
#    the threshold. Config B uses min_diff 0, where disagreements are
#    smallest and least consequential; a large min_diff would widen this gap.
#    Watch it in the paper month rather than assuming it away.
#
# DEPLOYMENT CLASS: live-shared path (tick engine + Settings). NON-TRADING
# DAY ship. Dual-tree. Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_LIVE_CONFIGB_20260827"
ROOT = Path(__file__).resolve().parent
TE_REL = "app/marketdata/zerodha_tick_engine.py"
SET_JSX = ROOT / "frontend" / "src" / "pages" / "Settings.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / TE_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ═══ B. live ATM skew gate ════════════════════════════════════════════════

TE_OLD = """                    is_option = symbol.endswith("CE") or symbol.endswith("PE")
"""
TE_NEW = '''                    is_option = symbol.endswith("CE") or symbol.endswith("PE")

                    # ── SCALP_V1_LIVE_CONFIGB_20260827 ── ATM skew gate, the
                    # live analogue of the backtest runner's gate. Runs only
                    # on a sell signal, before ANY routing. Fail-closed.
                    if signal.is_sell and is_option:
                        if not self._atm_skew_ok(symbol):
                            return

'''

TE_HELPER_OLD = """    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)"""
TE_HELPER_NEW = '''    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)

    # ── SCALP_V1_LIVE_CONFIGB_20260827 ─────────────────────────────────────
    def _atm_skew_ok(self, symbol: str) -> bool:
        """Config B's ATM skew gate for the LIVE/PAPER path.

        Sell the side the ATM pair prices as DEARER (invert=True): a CE sell
        needs the ATM CE dearer than the ATM PE by >= min_diff_pts, and a PE
        sell the mirror. Disabled -> always True.

        FAIL-CLOSED: any missing input (config, spot, ATM pair, quote) blocks
        the entry and audits. A blocked entry costs one trade; an unfiltered
        one costs whatever the filter existed to prevent.
        """
        try:
            from app.config.strategy_loader import load_strategy_config
            _sk = (load_strategy_config("SCALP_V1") or {}).get("atm_skew_filter") or {}
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] config read failed ({e!r}) — BLOCKED")
            return False
        if not bool(_sk.get("enabled", False)):
            return True
        try:
            min_diff = float(_sk.get("min_diff_pts", 0) or 0)
        except (TypeError, ValueError):
            min_diff = 0.0
        invert = bool(_sk.get("invert", False))

        spot = None
        try:
            from app.marketdata.market_indices_state import MarketIndicesState
            spot = (MarketIndicesState.snapshot().get("NIFTY") or {}).get("ltp")
        except Exception:
            spot = None
        if spot is None:
            write_audit_log(f"[SCALP_V1][SKEW] no NIFTY spot — BLOCKED {symbol}")
            return False

        # ATM strike on the SAME expiry as the contract being sold, taken from
        # the instrument master so the strike step is never hardcoded.
        try:
            import pandas as pd  # noqa: F401  (df ops only)
            df = self.instruments_df
            row = df[df["tradingsymbol"] == symbol]
            if row.empty:
                write_audit_log(f"[SCALP_V1][SKEW] {symbol} not in master — BLOCKED")
                return False
            exp = row.iloc[0]["expiry"]
            same = df[(df["expiry"] == exp) & (df["name"] == "NIFTY")]
            ce = same[same["instrument_type"] == "CE"]
            pe = same[same["instrument_type"] == "PE"]
            common = set(ce["strike"]).intersection(set(pe["strike"]))
            if not common:
                write_audit_log(f"[SCALP_V1][SKEW] no complete ATM pair — BLOCKED {symbol}")
                return False
            k = min(common, key=lambda s: (abs(float(s) - float(spot)), float(s)))
            ce_sym = ce[ce["strike"] == k].iloc[0]["tradingsymbol"]
            pe_sym = pe[pe["strike"] == k].iloc[0]["tradingsymbol"]
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] ATM resolve failed ({e!r}) — BLOCKED {symbol}")
            return False

        # One quote for both legs. LTPStore is not usable here: it only holds
        # SUBSCRIBED symbols, and the ATM pair is often outside the
        # premium-band universe (near expiry especially).
        try:
            q = self.kite_data.ltp([f"NFO:{ce_sym}", f"NFO:{pe_sym}"])
            ce_px = float(q[f"NFO:{ce_sym}"]["last_price"])
            pe_px = float(q[f"NFO:{pe_sym}"]["last_price"])
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] ATM quote failed ({e!r}) — BLOCKED {symbol}")
            return False

        sk = pe_px - ce_px                      # same sign convention as the backtest
        diff = sk if symbol.endswith("CE") else -sk
        if invert:
            diff = -diff
        ok = diff > min_diff
        write_audit_log(
            f"[SCALP_V1][SKEW] {symbol} atm={k} ce={ce_px} pe={pe_px} sk={sk:.2f} "
            f"diff={diff:.2f} min={min_diff} invert={invert} -> "
            f"{'ALLOW' if ok else 'BLOCK'}")
        return ok'''

# ═══ A. Settings: unclamp VWAP, add skew fields ═══════════════════════════

S1_OLD = '''              {!!scalpConfig.vwap_filter?.enabled && (
                <Field label="Min Pts Below" helper="0 = below by any amount · live surface clamps to ≥ 0">
                  <Input type="number" min="0" step="0.5" value={scalpConfig.vwap_filter?.min_below_pts ?? 0}
                    onChange={(e) => updateScalp(["vwap_filter", "min_below_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              )}'''
S1_NEW = '''              {/* ── SCALP_V1_LIVE_CONFIGB_20260827 ── clamp removed: the
                  negative tolerance band is a sealed Config B parameter now,
                  not an unvalidated exploration. NEGATIVE = allow entries up
                  to that many points ABOVE the session average. */}
              {!!scalpConfig.vwap_filter?.enabled && (
                <Field label="Min Pts Below" helper="0 = below by any amount · NEGATIVE = tolerance band, e.g. -10 allows entries within +10 of the session average (Config B)">
                  <Input type="number" step="0.5" value={scalpConfig.vwap_filter?.min_below_pts ?? 0}
                    onChange={(e) => updateScalp(["vwap_filter", "min_below_pts"], Number(e.target.value))}
                    style={{ maxWidth: 120 }} />
                </Field>
              )}
              {/* ── SCALP_V1_LIVE_CONFIGB_20260827 ── ATM skew (Config B).
                  "Sell dearer side" is the validated direction; the original
                  "cheaper" direction lost money on the full corpus and is
                  kept only so historical configs still render. */}
              <Field label="ATM Skew" helper="Compares the ATM call and put of the traded expiry at signal time. Quote failure blocks the entry (fail-closed).">
                <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: colors.text.secondary, userSelect: "none", cursor: "pointer" }}>
                  <input type="checkbox" checked={!!scalpConfig.atm_skew_filter?.enabled}
                    onChange={(e) => updateScalp(["atm_skew_filter", "enabled"], e.target.checked)}
                    style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0 }} />
                  Enabled
                </label>
              </Field>
              {!!scalpConfig.atm_skew_filter?.enabled && (<>
                <Field label="Skew Direction" helper="Config B: sell the dearer side">
                  <Select value={scalpConfig.atm_skew_filter?.invert ? "dearer" : "cheaper"}
                    onChange={(e) => updateScalp(["atm_skew_filter", "invert"], e.target.value === "dearer")}
                    style={{ maxWidth: 200 }}>
                    <option value="dearer">Sell dearer side (Config B)</option>
                    <option value="cheaper">Sell cheaper side (falsified)</option>
                  </Select>
                </Field>
                <Field label="Min Skew Pts" helper="Config B: 0">
                  <Input type="number" min="0" step="0.5" value={scalpConfig.atm_skew_filter?.min_diff_pts ?? 0}
                    onChange={(e) => updateScalp(["atm_skew_filter", "min_diff_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </>)}'''


def main():
    if not (ROOT / "backend" / TE_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / TE_REL
        t = p.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {p} — already applied")
        t = _ro(t, TE_HELPER_OLD, TE_HELPER_NEW, f"{tree.name}:TE_HELPER")
        t = _ro(t, TE_OLD, TE_NEW, f"{tree.name}:TE_GATE")
        staged.append((p, t))
    st = SET_JSX.read_text()
    if FENCE in st:
        _die("fence already present in Settings.jsx")
    st = _ro(st, S1_OLD, S1_NEW, "Settings:S1")
    staged.append((SET_JSX, st))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. NON-TRADING-DAY ship.")
    print()
    print("SETTINGS > SCALP V1, for Config B:")
    print("  VWAP Filter  : Enabled · Min Pts Below = -10")
    print("  ATM Skew     : Enabled · Direction 'Sell dearer side' · Min 0")
    print("  (Config A = both filters disabled; everything else identical.)")
    print()
    print("FIRST-SESSION ACCEPTANCE: one [SCALP_V1][SKEW] audit line per sell")
    print("signal showing atm/ce/pe/sk/diff and ALLOW or BLOCK. If every line")
    print("says BLOCK, the quote path is failing — check it before assuming")
    print("the filter is simply selective.")


if __name__ == "__main__":
    main()
