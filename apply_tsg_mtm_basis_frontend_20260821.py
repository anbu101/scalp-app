#!/usr/bin/env python3
# ── TSG_MTM_BASIS_20260821 ── frontend edit script
#
# Wires `mtm_sl_basis` through all four param surfaces (the diverging-copy
# footgun) + the TSG backtest config form:
#   1. pages/Backtest.jsx        state, localStorage, buildConfig + BOTH dep
#                                arrays (stale-closure rule), describeConfig
#                                chip, header line, form <select>
#   2. pages/backtest/BacktestQueue.jsx   KEY PARAMS chip
#   3. pages/backtest/RunComparison.jsx   comparison row
#   4. pages/backtest/SweepBuilder.jsx    sweep field (DAILY, POSITION)
#
# Display rule: rows WITHOUT the key show no suffix (old runs are left
# unlabeled \u2014 they predate the toggle and can't be retro-labeled honestly).
#
# Run from repo root:  python3 apply_tsg_mtm_basis_frontend_20260821.py
# Assert-anchored; any miss aborts that file untouched.

import sys
from pathlib import Path

ROOT = Path("frontend/src")

FILES = {
    ROOT / "pages/Backtest.jsx": [
        # B1 — state (after the tsgMtmSl state line)
        (
            "  const [tsgMtmSl, setTsgMtmSl] = useState(tsgSaved.mtmSl ?? 0);   // \u2500\u2500 TSG_MTM_SL \u2500\u2500 positive \u20b9; 0 = off",
            "  const [tsgMtmSl, setTsgMtmSl] = useState(tsgSaved.mtmSl ?? 0);   // \u2500\u2500 TSG_MTM_SL \u2500\u2500 positive \u20b9; 0 = off\n"
            "  const [tsgMtmSlBasis, setTsgMtmSlBasis] = useState(tsgSaved.mtmSlBasis === \"POSITION\" ? \"POSITION\" : \"DAILY\");   // \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500 SL basis (D1 default DAILY)",
        ),
        # B2 — localStorage payload
        (
            "mtmTarget: tsgMtmTarget, mtmSl: tsgMtmSl, ivSlPct: tsgIvSlPct,",
            "mtmTarget: tsgMtmTarget, mtmSl: tsgMtmSl, mtmSlBasis: tsgMtmSlBasis, ivSlPct: tsgIvSlPct,",
        ),
        # B3 — localStorage effect dep array (ends with "]);")
        (
            "  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult]);",
            "  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult]);   // \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
        # B4 — buildConfig emits the key
        (
            "        mtm_sl: Math.abs(Number(tsgMtmSl)) || 0,",
            "        mtm_sl: Math.abs(Number(tsgMtmSl)) || 0,\n"
            "        mtm_sl_basis: tsgMtmSlBasis === \"POSITION\" ? \"POSITION\" : \"DAILY\",   // \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
        # B5 — buildConfig dep array (the TSG segment of the big useCallback deps)
        (
            "      tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult,   // \u2500\u2500 TSG_V1 / TSG_MTM_SL / TSG_IV_SL(+DELTA) / TSG_IV12 / TSG_IV13 / TSG_TRAIL / TSG_PARALLEL \u2500\u2500",
            "      tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult,   // \u2500\u2500 TSG_V1 / TSG_MTM_SL / TSG_MTM_BASIS_20260821 / TSG_IV_SL(+DELTA) / TSG_IV12 / TSG_IV13 / TSG_TRAIL / TSG_PARALLEL \u2500\u2500",
        ),
        # B6 — describeConfig chip (suffix only when key present)
        (
            "  if (Number(cfg.mtm_sl) > 0) add(\"MTM SL\", `-\u20b9${cfg.mtm_sl}`);   // \u2500\u2500 TSG_MTM_SL \u2500\u2500",
            "  if (Number(cfg.mtm_sl) > 0) add(\"MTM SL\", `-\u20b9${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === \"POSITION\" ? \" \u00b7pos\" : \" \u00b7day\") : \"\"}`);   // \u2500\u2500 TSG_MTM_SL / TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
        # B7 — strategy header line
        (
            "or \u2264 -\u20b9${Math.abs(tsgMtmSl)}` : \"\"}",
            "or \u2264 -\u20b9${Math.abs(tsgMtmSl)}${tsgMtmSlBasis === \"POSITION\" ? \" (open-legs)\" : \"\"}` : \"\"}",
        ),
        # B8 — form: basis <select> after the MTM SL input Field
        (
            "(MTM_SL). Same candle-close evaluation as the target \u2014 no intra-candle touch. 0 disables.\" /></Field>",
            "(MTM_SL). Same candle-close evaluation as the target \u2014 no intra-candle touch. 0 disables.\" /></Field>\n"
            "                <Field label=\"MTM SL basis\"><select style={inputStyle} value={tsgMtmSlBasis} onChange={(e) => setTsgMtmSlBasis(e.target.value === \"POSITION\" ? \"POSITION\" : \"DAILY\")} title=\"TSG_MTM_BASIS_20260821: DAILY = realized + unrealized day MTM (IV6 \u2014 an earlier IV_SL loss counts toward the -SL). POSITION = unrealized MTM of OPEN legs only \u2014 after a partial IV exit the survivors get a fresh runway to -SL. SL only: the MTM target and trailing lock always use day MTM.\"><option value=\"DAILY\">DAILY (realized + unrealized)</option><option value=\"POSITION\">POSITION (open legs only)</option></select></Field>   {/* \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500 */}",
        ),
    ],
    ROOT / "pages/backtest/BacktestQueue.jsx": [
        (
            "    if (Number(cfg.mtm_sl) > 0) p.push(`MTMSL\u20b9${cfg.mtm_sl}`);   // \u2500\u2500 TSG_MTM_SL \u2500\u2500",
            "    if (Number(cfg.mtm_sl) > 0) p.push(`MTMSL\u20b9${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === \"POSITION\" ? \"\u00b7pos\" : \"\u00b7day\") : \"\"}`);   // \u2500\u2500 TSG_MTM_SL / TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
    ],
    ROOT / "pages/backtest/RunComparison.jsx": [
        (
            "  { key: \"tsg_mtm_sl\",       label: \"MTM SL \u20b9\",     get: (r) => Number(r.config?.mtm_sl) > 0 ? `-${r.config.mtm_sl}` : null },   // \u2500\u2500 TSG_MTM_SL \u2500\u2500",
            "  { key: \"tsg_mtm_sl\",       label: \"MTM SL \u20b9\",     get: (r) => Number(r.config?.mtm_sl) > 0 ? `-${r.config.mtm_sl}` : null },   // \u2500\u2500 TSG_MTM_SL \u2500\u2500\n"
            "  { key: \"tsg_mtm_sl_basis\", label: \"SL basis\",     get: (r) => r.config?.mtm_sl_basis || null },   // \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
    ],
    ROOT / "pages/backtest/SweepBuilder.jsx": [
        (
            "  { key: \"tsg_mtm_sl\", label: \"MTM SL \u20b9\", strategies: [TSG],\n"
            "    hint: \"1500, 2500, 4000, 6000\", parse: _num,\n"
            "    apply: (c, v) => { c.mtm_sl = Math.abs(v); }, fmt: (v) => `MTMSL ${Math.abs(v)}` },   // \u2500\u2500 TSG_MTM_SL \u2500\u2500",
            "  { key: \"tsg_mtm_sl\", label: \"MTM SL \u20b9\", strategies: [TSG],\n"
            "    hint: \"1500, 2500, 4000, 6000\", parse: _num,\n"
            "    apply: (c, v) => { c.mtm_sl = Math.abs(v); }, fmt: (v) => `MTMSL ${Math.abs(v)}` },   // \u2500\u2500 TSG_MTM_SL \u2500\u2500\n"
            "  { key: \"tsg_mtm_sl_basis\", label: \"MTM SL basis\", strategies: [TSG],\n"
            "    hint: \"DAILY, POSITION\", parse: (s) => String(s).trim().toUpperCase(),\n"
            "    apply: (c, v) => { c.mtm_sl_basis = (v === \"POSITION\" ? \"POSITION\" : \"DAILY\"); }, fmt: (v) => `SLbasis ${v === \"POSITION\" ? \"pos\" : \"day\"}` },   // \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500",
        ),
    ],
}


def apply(path: Path, edits) -> bool:
    if not path.exists():
        print(f"[ABORT] {path} not found \u2014 run from repo root")
        return False
    text = path.read_text(encoding="utf-8")
    if "TSG_MTM_BASIS_20260821" in text:
        print(f"[SKIP] {path} \u2014 fence already present (idempotent)")
        return True
    for n, (old, new) in enumerate(edits, 1):
        cnt = text.count(old)
        if cnt != 1:
            print(f"[ABORT] {path} \u2014 edit {n}: anchor found {cnt}x "
                  f"(expected exactly 1). File NOT modified.")
            print("        anchor head: " + old.splitlines()[0][:70])
            return False
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"[OK]   {path} \u2014 {len(edits)} edits applied")
    return True


def main() -> int:
    ok = all(apply(p, e) for p, e in FILES.items())
    for p in FILES:
        if p.exists():
            c = p.read_text(encoding="utf-8").count("TSG_MTM_BASIS_20260821")
            print(f"[VERIFY] {p}: fence marker count = {c}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
