#!/usr/bin/env python3
# apply_ic2_margin_today.py — fence IC2_MARGIN_TODAY_20261015
#
# Backtest page → IC_V1 / IC_V2 form: "Margin (today)" button, TMA_V1 parity.
#
#   * Prices TODAY's weekly chain with the form's own rules (each leg = highest
#     LTP ≤ its premium cap, wings fall back to the cheapest real strike — the
#     runner's select_strike) and asks Zerodha's basket-margin API what the
#     L1–L4 condor blocks: hedged total, shorts-alone (unhedged) and the wing
#     benefit, plus the four picked contracts.
#   * IC_V2 with adjustment on: the two ·ADJ buys are priced in a SEPARATE
#     basket (they are bought after a short stops, never at entry) → premium per
#     side, both sides, and peak need ≈ condor margin + both ADJ premiums.
#   * Read-only (quotes + margin preview, no orders); needs a Kite login. Same
#     present-day-proxy caveat as TMA.
#
# No backend change: the existing /api/backtest/margin-estimate route already
# prices arbitrary legs (GENERIC_LEGS). This script adds a test that pins that
# behaviour for the IC shapes.
#
#  EDIT  frontend/src/pages/Backtest.jsx (2: state/fetch, Capital section)
#  NEW   backend/app/backtest/tma/test_ic2_margin_today.py (fake Kite, 15 checks)
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Gates: esbuild parse of Backtest.jsx, py_compile + run of the new test.
#  Any failure → every file restored. Independent of IC2_SHORT_TP_SKIP_EXP.
#
# Usage: python3 apply_ic2_margin_today.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script (the test file is carried inside it).

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "IC2_MARGIN_TODAY_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
B = "frontend/src/pages/Backtest.jsx"
ME = "backend/app/backtest/tma/margin_estimate.py"
T = "backend/app/backtest/tma/test_ic2_margin_today.py"
JSX = [B]
PREREQ = {B: ["TMA_MARGIN_ESTIMATE", "IC_V2 END"], ME: ["GENERIC_LEGS"]}
SUITES = ["app/backtest/tma/test_ic2_margin_today.py"]

EDITS = [
    (B, '  const setIcLeg = useCallback((idx, key, val) => {\n    setIcLegs((prev) => prev.map((l, i) => (i === idx ? { ...l, [key]: val } : l)));\n  }, []);\n',
     '  const setIcLeg = useCallback((idx, key, val) => {\n    setIcLegs((prev) => prev.map((l, i) => (i === idx ? { ...l, [key]: val } : l)));\n  }, []);\n  // ── IC2_MARGIN_TODAY_20261015 ── live "today" margin for the IC form (TMA parity).\n  // Two read-only calls to the GENERIC_LEGS margin route: (1) the entry condor\n  // L1–L4 as one basket (wings first → spread benefit), (2) IC_V2 only, the two\n  // ·ADJ buys priced alone — they are bought AFTER a short stops, so they are\n  // never in the entry basket; what they need is free cash for the premium.\n  const [icMargin, setIcMargin] = useState(null);\n  const [icMarginBusy, setIcMarginBusy] = useState(false);\n  const fetchIcMargin = useCallback(async () => {\n    setIcMarginBusy(true); setIcMargin(null);\n    const post = (legs) => apiCall("/api/backtest/margin-estimate", {\n      method: "POST", headers: { "Content-Type": "application/json" },\n      body: JSON.stringify({ legs }),\n    }).catch((e) => ({ ok: false, error: String(e.message || e) }));\n    try {\n      const condor = await post(icLegs\n        .filter((l) => Number(l.lots) > 0)\n        .map((l) => ({ side: l.opt_type, action: l.action, premium_max: Number(l.premium_max) || 0, lots: Number(l.lots) || 0 })));\n      let adj = null;\n      if (isICV2 && icAdjustOn) {\n        const al = [["L1", "CE"], ["L2", "PE"]]\n          .map(([lid, side]) => ({ a: icAdjust[lid] || {}, side }))\n          .filter(({ a }) => a.enabled && Number(a.lots) > 0)\n          .map(({ a, side }) => ({ side, action: "BUY", premium_max: Number(a.premium_max) || 0, lots: Number(a.lots) || 0 }));\n        if (al.length) adj = await post(al);\n      }\n      setIcMargin({ condor, adj });\n    } finally { setIcMarginBusy(false); }\n  }, [icLegs, icAdjust, icAdjustOn, isICV2]);\n', 1),
    (B, '              {/* ── IC_V2 END ── */}\n',
     '              {/* ── IC_V2 END ── */}\n              {/* ── IC2_MARGIN_TODAY_20261015 ── Capital (TMA_MARGIN_ESTIMATE parity) */}\n              <div style={{ marginTop: 14, paddingTop: 10, borderTop: `1px solid ${colors.border.dark}` }}>\n                <div style={{ ...typography.label, color: colors.text.muted, fontSize: 10, letterSpacing: 0.5, textTransform: "uppercase", marginBottom: 6 }}>Capital</div>\n                <div style={{ display: "flex", gap: spacing.md, flexWrap: "wrap", alignItems: "flex-start" }}>\n                  <button onClick={fetchIcMargin} disabled={icMarginBusy}\n                    title="Prices TODAY\'s weekly chain with this form\'s rules (highest premium ≤ each leg\'s cap) and asks Zerodha\'s basket-margin API what the condor would block. Read-only — no orders. Needs a Kite login."\n                    style={{ ...inputStyle, cursor: "pointer", width: "auto", padding: "7px 14px" }}>\n                    {icMarginBusy ? "Fetching…" : "Margin (today)"}</button>\n                  {icMargin && (() => {\n                    const L = (v) => `₹${(Number(v || 0) / 100000).toFixed(2)}L`;\n                    const R = (v) => `₹${Math.round(Number(v || 0)).toLocaleString("en-IN")}`;\n                    const c = icMargin.condor || {};\n                    const a = icMargin.adj;\n                    const legTxt = (x) => `${x.action === "SELL" ? "S" : "B"} ${x.symbol} @ ₹${x.ltp}`;\n                    const order = (xs) => [...xs].sort((p, q) => (p.side === q.side ? (p.action === "SELL" ? -1 : 1) : (p.side === "CE" ? -1 : 1)));\n                    const adjAll = (a && a.ok && a.legs && a.legs.all) || [];\n                    const adjBoth = adjAll.reduce((s, x) => s + x.ltp * x.qty, 0);\n                    return (\n                      <div style={{ fontSize: 12, color: colors.text.secondary, maxWidth: 760, lineHeight: 1.55 }}>\n                        {c.ok ? (\n                          <>\n                            <div>Condor today (exp {c.expiry}): <b>{L(c.hedged_total)} blocked</b> · shorts unhedged {L(c.naked_total)} · wing benefit {L(c.benefit)}{c.note ? ` · ${c.note}` : ""}</div>\n                            <div style={{ color: colors.text.muted, fontSize: 11 }}>{order((c.legs && c.legs.all) || []).map(legTxt).join(" · ")}</div>\n                          </>\n                        ) : <div style={{ color: colors.loss }}>Margin estimate: {c.error || "failed"}</div>}\n                        {a && (a.ok ? (\n                          <div style={{ marginTop: 4 }}>\n                            If a short stops, the ·ADJ buy needs free cash for its premium: {adjAll.map((x) => `${x.side} ${x.symbol} @ ₹${x.ltp} = ${R(x.ltp * x.qty)}`).join(" · ")}\n                            {adjAll.length > 1 ? <> · both sides <b>{R(adjBoth)}</b></> : null}\n                            {c.ok ? <> → peak need ≈ <b>{L(Number(c.hedged_total || 0) + adjBoth)}</b></> : null}\n                          </div>\n                        ) : <div style={{ color: colors.loss, marginTop: 4 }}>ADJ estimate: {a.error || "failed"}</div>)}\n                        <div style={{ color: colors.text.tertiary, fontSize: 11, marginTop: 2 }}>\n                          Present-day proxy (SPAN is point-in-time; at 09:18 the strikes and the margin will differ) — use it to compare configs, not as a historical average.\n                        </div>\n                      </div>\n                    );\n                  })()}\n                </div>\n              </div>\n', 1),
]

NEW_FILES = {T: '# backend/app/backtest/tma/test_ic2_margin_today.py — fence IC2_MARGIN_TODAY_20261015\n#\n# The IC "Margin (today)" button reuses the GENERIC_LEGS path of\n# /api/backtest/margin-estimate (estimate_tma_margin). Fake Kite + fake\n# instrument dump; never touches the network or a broker session.\n#\n#   1. condor basket (L1–L4 as the IC form sends them): each leg = highest\n#      LTP ≤ its cap on its side (runner\'s select_strike), wings distinct\n#      from the shorts, BUY legs first in the basket, naked = shorts alone\n#   2. wing cap below every live strike → cheapest real + note (as the runner)\n#   3. ADJ-only basket (two BUYs): total = Σ ltp × qty, no shorts → naked 0\n#   4. a 0-lot leg is dropped; all-zero → clean error\n#\n# Run: cd backend && python3 app/backtest/tma/test_ic2_margin_today.py\n\nfrom __future__ import annotations\n\nimport os\nimport sys\nfrom datetime import date\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[3])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\n\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\ndef fake_world(spot=25000.0):\n    import pandas as pd\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day\n    exp = expected_expiry_for_day(date.today())\n    rows, ltp = [], {}\n    for k in range(-30, 31):\n        K = int(spot) + 50 * k\n        for side in ("CE", "PE"):\n            sym = f"NIFTYTEST{K}{side}"\n            dist = (K - spot) if side == "CE" else (spot - K)\n            px = max(0.0, -dist) + 120.0 * (0.97 ** max(0.0, dist / 10.0))   # intrinsic + decaying TV\n            rows.append({"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": side,\n                         "expiry": exp.isoformat(), "tradingsymbol": sym, "strike": float(K),\n                         "lot_size": 65})\n            ltp[f"NFO:{sym}"] = {"last_price": round(px, 2)}\n    return pd.DataFrame(rows), ltp, exp\n\n\nclass FakeKite:\n    def __init__(self, ltp):\n        self._ltp = ltp\n        self.baskets = []\n\n    def ltp(self, keys):\n        return {k: self._ltp[k] for k in keys if k in self._ltp}\n\n    def basket_order_margins(self, basket, consider_positions=False):\n        self.baskets.append([dict(o) for o in basket])\n        tot, buy_sides = 0.0, set()\n        for o in basket:\n            if o["transaction_type"] == "BUY":\n                tot += self._ltp[f"NFO:{o[\'tradingsymbol\']}"]["last_price"] * o["quantity"]\n                buy_sides.add(o["tradingsymbol"][-2:])\n        for o in basket:\n            if o["transaction_type"] == "SELL":\n                per_lot = 50000.0 if o["tradingsymbol"][-2:] in buy_sides else 150000.0\n                tot += per_lot * o["quantity"] / 65\n        return {"final": {"total": tot}}\n\n\ndef main():\n    import app.fetcher.zerodha_instruments as zi\n    from app.backtest.tma.margin_estimate import estimate_tma_margin\n    from app.backtest.ic.ic_v1_engine import select_strike\n    df, ltp, exp = fake_world()\n    zi.load_instruments_df = lambda *a, **k: df\n\n    def ladder(side):\n        return [(s.split(":")[1], v["last_price"]) for s, v in ltp.items() if s.endswith(side)]\n\n    print("── 1. condor basket ──")\n    k = FakeKite(ltp)\n    legs = [{"side": "CE", "action": "SELL", "premium_max": 100, "lots": 10},\n            {"side": "PE", "action": "SELL", "premium_max": 100, "lots": 10},\n            {"side": "CE", "action": "BUY", "premium_max": 4, "lots": 10},\n            {"side": "PE", "action": "BUY", "premium_max": 4, "lots": 10}]\n    r = estimate_tma_margin(legs=legs, kite=k)\n    check("ok", r.get("ok"), r.get("error"))\n    allp = r["legs"]["all"]\n    want_s_ce = select_strike(ladder("CE"), 100)[0]\n    want_w_ce = select_strike([x for x in ladder("CE") if x[0] != want_s_ce], 4)[0]\n    got = {(x["action"], x["side"]): x["symbol"] for x in allp}\n    check("short CE = highest LTP ≤ 100 (runner rule)", got[("SELL", "CE")] == want_s_ce, (got, want_s_ce))\n    check("wing CE = highest LTP ≤ 4, distinct from the short", got[("BUY", "CE")] == want_w_ce and want_w_ce != want_s_ce)\n    check("4 legs, qty 650 each", len(allp) == 4 and all(x["qty"] == 650 for x in allp))\n    check("basket sends BUY legs first", [o["transaction_type"] for o in k.baskets[0]] == ["BUY", "BUY", "SELL", "SELL"])\n    check("naked basket = the two shorts alone", [o["transaction_type"] for o in k.baskets[1]] == ["SELL", "SELL"])\n    check("hedged < naked, benefit = difference",\n          r["hedged_total"] < r["naked_total"] and abs(r["benefit"] - (r["naked_total"] - r["hedged_total"])) < 0.01)\n    check("expiry = this week\'s expiry", r["expiry"] == exp.isoformat())\n\n    print("── 2. wing cap below every strike ──")\n    r2 = estimate_tma_margin(legs=[dict(l, premium_max=0.01) if l["action"] == "BUY" else l for l in legs], kite=FakeKite(ltp))\n    check("still ok with cheapest real wings + note", r2.get("ok") and r2.get("note"), r2)\n\n    print("── 3. ADJ-only basket ──")\n    k3 = FakeKite(ltp)\n    r3 = estimate_tma_margin(legs=[{"side": "CE", "action": "BUY", "premium_max": 100, "lots": 8},\n                                   {"side": "PE", "action": "BUY", "premium_max": 100, "lots": 8}], kite=k3)\n    a = r3["legs"]["all"]\n    prem = sum(x["ltp"] * x["qty"] for x in a)\n    check("ok, two BUY legs of 520", r3.get("ok") and len(a) == 2 and all(x["qty"] == 520 for x in a), r3)\n    check("total = Σ ltp × qty (premium paid)", abs(r3["hedged_total"] - round(prem, 2)) < 0.01, (r3["hedged_total"], prem))\n    check("no shorts → naked 0", r3["naked_total"] == 0)\n    check("ADJ strike = highest LTP ≤ 100 (runner rule)",\n          {x["side"]: x["symbol"] for x in a}["CE"] == select_strike(ladder("CE"), 100)[0])\n\n    print("── 4. lots ──")\n    r4 = estimate_tma_margin(legs=[dict(legs[0], lots=0)] + legs[1:], kite=FakeKite(ltp))\n    check("0-lot leg dropped (3 legs priced)", r4.get("ok") and len(r4["legs"]["all"]) == 3)\n    r5 = estimate_tma_margin(legs=[dict(l, lots=0) for l in legs], kite=FakeKite(ltp))\n    check("all-zero → clean error", r5.get("ok") is False and "lots" in r5.get("error", ""))\n\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} IC MARGIN-TODAY CHECKS PASSED")\n\n\nif __name__ == "__main__":\n    main()\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        txt = staged.get(rel) or ((repo / rel).read_text(encoding="utf-8") if (repo / rel).exists() else "")
        for f in fences:
            if f not in txt:
                die(f"prerequisite {f!r} not found in {rel}")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in NEW_FILES.items():
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print("  ✓ py_compile (test)")
        esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                    if c.exists()), None) or shutil.which("esbuild")
        if esb:
            for rel in JSX:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print("  ✓ esbuild parsed Backtest.jsx")
        else:
            print("  ! esbuild not found — parse gate skipped (the frontend build will check)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        for suite in SUITES:
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied (frontend only + a test). Rebuild the frontend (./desktop/build-scalp.sh both), log in to Kite,\n"
          f"  then Backtest → IC V2 → 'Margin (today)' under the adjustment table.")


if __name__ == "__main__":
    main()
