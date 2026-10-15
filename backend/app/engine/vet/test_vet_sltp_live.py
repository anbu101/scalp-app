# backend/app/engine/vet/test_vet_sltp_live.py — fence VET_SLTP_LIVE_20261015
#
#   A. levels: SHORT / LONG math, 0 = off, read FRESH from Settings at entry
#   B. entry stamps the levels on the leg (memory + vet_trades) → Trades page
#   C. check_sltp: TP / SL exits at the mark, wing closed too, no-mark and
#      no-level cases never exit
#   D. restart: a legacy row gets levels from current Settings; a stamped row
#      keeps its own levels after Settings change
#   E. restart re-derives a same-day SL/TP re-entry block
#   F. PARITY: 400 randomized sessions — the live manager (check_sltp →
#      plan → sltp_gate → on_decision) produces exactly the runner's
#      (backtest_vet_runner 1c + block_target) exits and entries
#   G. wiring (loop order, route, panel, Settings text)
#
# Run: cd backend && python3 app/engine/vet/test_vet_sltp_live.py

from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())

from app.engine.vet.vet_common import VetRepo  # noqa: E402
from app.engine.vet.vet_live_core import ENTER, plan, want_side_for  # noqa: E402
from app.engine.vet import vet_manager as VM  # noqa: E402

FENCE = "VET_SLTP_LIVE_20261015"
FAILS: list = []
OK = [0]
TMP = tempfile.mkdtemp(prefix="vet_sltp_")
SPOT = 24000.0


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


class Px:
    v = 100.0


def chain(side, ts):
    out = []
    for k in range(23000, 25050, 50):
        far = abs(k - SPOT) >= 900
        out.append({"tradingsymbol": f"NIFTY26O19{k}{side}", "token": 1000 + k, "strike": float(k),
                    "expiry": "2026-10-19", "instrument_type": side,
                    "ltp": (2.0 if far else Px.v)})
    return out


def quote(sym):
    k = int(sym[10:15])
    return 2.0 if abs(k - SPOT) >= 900 else Px.v


def mk(db, sl=0.0, tp=0.0, wing=False, reenter=False, reader=None):
    cfg = {"leg_action": "SELL", "atm_offset": 0, "eod_square": False,
           "hedge_enabled": wing, "hedge_max_premium": 3.0 if wing else 0,
           "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT,
           "sl_pct": sl, "tp_pct": tp, "reenter_after_sltp": reenter}
    repo = VetRepo(db)
    repo.ensure_schema()
    return VM.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=quote, mode="PAPER",
                         cfg_reader=(reader if reader is not None else (lambda: None)))


def db(name):
    return os.path.join(TMP, name + ".db")


def t_levels():
    print("── A. levels ──")
    m = mk(db("a"), sl=20, tp=90)
    sl, tp = m.sltp_levels(182.40)
    check("SHORT: SL 20% → 218.88, TP 90% → 18.24", (round(sl, 2), round(tp, 2)) == (218.88, 18.24), (sl, tp))
    m.cfg["leg_action"] = "BUY"
    sl, tp = m.sltp_levels(100.0)
    check("LONG: SL below, TP above entry", (round(sl, 2), round(tp, 2)) == (80.0, 190.0), (sl, tp))
    m = mk(db("a2"))
    check("0 / 0 (every sealed config) → no levels", m.sltp_levels(182.4) == (None, None))
    m = mk(db("a3"), tp=0, reader=lambda: {"sl_pct": 0, "tp_pct": 90})
    check("read FRESH from Settings: TP 90 saved after boot is used for the next entry",
          round(m.sltp_levels(182.4)[1], 2) == 18.24)
    m = mk(db("a4"), tp=50, reader=lambda: 1 / 0)
    check("Settings read failing → boot config (never crashes)", round(m.sltp_levels(100.0)[1], 2) == 50.0)
    real = VM._fresh_vet_cfg
    try:
        VM._fresh_vet_cfg = lambda: {"sl_pct": 0, "tp_pct": 90}     # the machine's Settings say TP 90
        cfg = {"leg_action": "SELL", "quantity": {"lots": 10, "lot_size": 65}}
        h = VM.VetManager(cfg, repo=VetRepo(db("a5")), chain_fn=chain, quote_fn=quote)
        check("a manager built WITHOUT a reader never reads the machine's Settings (hermetic harnesses)",
              h.sltp_levels(182.4) == (None, None))
    finally:
        VM._fresh_vet_cfg = real


def t_entry_stamp():
    print("── B. entry stamps the levels ──")
    Px.v = 182.40
    path = db("b")
    m = mk(path, tp=90, wing=True)
    p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    check("position opened with a wing", p is not None and p["wing"] is not None)
    check("main leg carries TP 18.24, no SL", round(p["main"]["tp"], 2) == 18.24 and p["main"]["sl"] is None, p["main"])
    r = sqlite3.connect(path).execute("SELECT sl, tp FROM vet_trades WHERE leg_role='MAIN'").fetchone()
    check("vet_trades MAIN row: sl NULL, tp 18.24", r is not None and r[0] is None and round(r[1], 2) == 18.24, r)
    try:
        from app.api.paper_trades_routes import _load_tma_paper
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        opn, _ = _load_tma_paper(c, table="vet_trades", strategy_name="VET_V1")
        mains = [t for t in opn if t["trade_direction"] == "SHORT"]
        check("Trades page row now shows TP 18.24", mains and round(mains[0]["tp_price"], 2) == 18.24,
              [t.get("tp_price") for t in opn])
    except ImportError as e:
        print(f"  ! paper_trades_routes not importable here ({e}) — skipped")
    return m, path


def t_exits():
    print("── C. check_sltp ──")
    Px.v = 182.40
    m = mk(db("c1"), tp=90, wing=True)
    m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    Px.v = 30.0
    check("premium above TP → no exit", m.check_sltp(1300) is None and m.pos is not None)
    Px.v = 18.20
    r = m.check_sltp(1600)
    check("premium ≤ 18.24 → TP exit, flat", r == "TP" and m.pos is None, r)
    rows = list(sqlite3.connect(db("c1")).execute(
        "SELECT leg_role, exit_reason, exit_price FROM vet_trades ORDER BY id"))
    check("both legs closed with reason TP; main at the mark", sorted(x[1] for x in rows) == ["TP", "TP"]
          and [x for x in rows if x[0] == "MAIN"][0][2] == 18.2, rows)
    Px.v = 100.0
    m = mk(db("c2"), sl=25, tp=90)
    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)
    Px.v = 125.0
    check("SHORT premium +25% → SL", m.check_sltp(1300) == "SL" and m.pos is None)
    Px.v = 100.0
    m = mk(db("c3"), tp=90)
    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)
    m.quote_fn = lambda s: None
    check("no mark → levels not evaluated, position kept", m.check_sltp(1300) is None and m.pos is not None)
    Px.v = 100.0
    m = mk(db("c5"), sl=20, tp=30)
    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)
    Px.v = m.pos["main"]["tp"]
    check("tie: mark exactly AT the TP level → TP (runner: mark <= level)", m.check_sltp(1300) == "TP")
    Px.v = 100.0
    m = mk(db("c6"), sl=20, tp=30)
    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)
    Px.v = m.pos["main"]["sl"]
    check("tie: mark exactly AT the SL level → SL (runner: mark >= level)", m.check_sltp(1300) == "SL")
    Px.v = 100.0
    m = mk(db("c4"))
    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)
    Px.v = 0.05
    check("0/0 config → never exits on price (sealed behaviour unchanged)",
          m.check_sltp(1300) is None and m.pos is not None)


def t_restart():
    print("── D/E. restart ──")
    Px.v = 182.40
    path = db("d")
    m = mk(path)                                      # entered with TP 0 (pre-fix)
    m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    sqlite3.connect(path).execute("UPDATE vet_trades SET sl=NULL, tp=NULL").connection.commit()
    m2 = mk(path, reader=lambda: {"tp_pct": 90, "sl_pct": 0})
    m2.resume_from_db()
    check("legacy open position (no levels) gets TP from current Settings on adopt",
          m2.pos and round(m2.pos["main"]["tp"], 2) == 18.24, m2.pos and m2.pos["main"].get("tp"))
    m3 = mk(path, reader=lambda: {"tp_pct": 50, "sl_pct": 0})
    m3.resume_from_db()
    check("a stamped position KEEPS its levels after a Settings change",
          round(m3.pos["main"]["tp"], 2) == 18.24, m3.pos["main"].get("tp"))
    import time as _t
    now = int(_t.time())
    m3.pos["main"]["entry_ts"] = now - 600
    sqlite3.connect(path).execute("UPDATE vet_trades SET entry_ts=?", (now - 600,)).connection.commit()
    Px.v = 10.0
    m3.check_sltp(now - 60)
    m4 = mk(path)
    check("restart after today's TP exit → same side blocked", m4.restore_sltp_block(now) == "PE")
    m5 = mk(path, reenter=True)
    check("…unless reenter_after_sltp", m5.restore_sltp_block(now) is None)
    check("…and a TP exit on a previous day blocks nothing",
          mk(path).restore_sltp_block(now + 2 * 86400) is None)


def ref_run(targets, marks, sl_pct, tp_pct, reenter):
    """backtest_vet_runner semantics (step 1c SL/TP + block_target + step 2)."""
    ev, pos, block = [], None, None
    for i, (t, mk_) in enumerate(zip(targets, marks)):
        if block is not None and t != block:
            block = None
        if pos is not None and (sl_pct > 0 or tp_pct > 0):
            sl_lvl = pos[1] * (1 + sl_pct / 100.0)
            tp_lvl = pos[1] * (1 - tp_pct / 100.0)
            hit = "SL" if (sl_pct > 0 and mk_ >= sl_lvl) else ("TP" if (tp_pct > 0 and mk_ <= tp_lvl) else None)
            if hit:
                ev.append((i, "X", hit, mk_))
                if not reenter:
                    block = t
                pos = None
        want = want_side_for(t, "SELL")
        if pos is not None and pos[0] != want:
            ev.append((i, "X", "FLIP" if want else "SIGNAL_EXIT", mk_))
            pos = None
            if want:
                block = None
        if pos is None and want is not None and block != t:
            ev.append((i, "E", want, mk_))
            pos = (want, mk_)
    return ev


def live_run(targets, marks, sl_pct, tp_pct, reenter, path):
    m = mk(path, sl=sl_pct, tp=tp_pct, reenter=reenter)
    ev = []
    oc, oo = m.close_position, m.open_position

    def cp(reason, *, ts):
        mark = Px.v
        ev.append((ts, "X", reason, mark))
        return oc(reason, ts=ts)

    def op(side, *, ts, bar_ts, condition):
        r = oo(side, ts=ts, bar_ts=bar_ts, condition=condition)
        if r is not None:
            ev.append((ts, "E", side, Px.v))
        return r
    m.close_position, m.open_position = cp, op
    for i, (t, mk_) in enumerate(zip(targets, marks)):
        Px.v = mk_
        hit = m.check_sltp(i)
        a, side, reason = plan(m.pos["side"] if m.pos else None, t, "SELL")
        dec = {"action": a, "side": side, "reason": reason, "condition": t, "bar_ts": i}
        if not m.sltp_gate(dec, hit):
            continue
        m.on_decision(dec, ts=i)
    return ev


def t_parity():
    print("── F. parity vs the backtest runner ──")
    rnd = random.Random(20261015)
    bad, n_sltp, n_block = 0, 0, 0
    for trial in range(400):
        n = rnd.randint(20, 70)
        t, ts_, marks, px = rnd.choice((-1, 0, 1)), [], [], rnd.uniform(60, 220)
        for _ in range(n):
            if rnd.random() < 0.15:
                t = rnd.choice((-1, 0, 1))
            px = max(0.05, px * (1 + rnd.gauss(0, 0.08)))
            ts_.append(t)
            marks.append(round(round(px / 0.05) * 0.05, 2))
        sl = rnd.choice((0, 0, 10, 25, 40))
        tp = rnd.choice((0, 30, 60, 90))
        re_ = rnd.random() < 0.25
        a = ref_run(ts_, marks, sl, tp, re_)
        b = live_run(ts_, marks, sl, tp, re_, db(f"p{trial}"))
        n_sltp += sum(1 for e in a if e[2] in ("SL", "TP"))
        if a != b:
            bad += 1
            if bad <= 2:
                print("   ref ", a[:12])
                print("   live", b[:12])
    check(f"400 randomized sessions: live events == runner events ({n_sltp} SL/TP exits exercised)",
          bad == 0 and n_sltp > 200, f"{bad} mismatching sessions")


def t_wiring():
    print("── G. wiring ──")
    s = (BACKEND / "app/engine/vet/vet_selection_loop.py").read_text(encoding="utf-8")
    i_c, i_d = s.find("manager.check_sltp(ts)"), s.find("decision = engine.decide(")
    i_b, i_g = s.find('if decision.get("blocked"):'), s.find("manager.sltp_gate(decision, _sltp_hit)")
    check("loop: SL/TP checked at the 5m close BEFORE the signal decision", 0 < i_c < i_d, (i_c, i_d))
    check("loop: re-entry gate applied after the decision, before any action", 0 < i_b < i_g, (i_b, i_g))
    check("loop: restart re-derives the block", "manager.restore_sltp_block()" in s)
    check("loop: the LIVE manager (only) reads SL/TP % from Settings", "cfg_reader=_fresh_vet_cfg" in s)
    r = (BACKEND / "app/api/vet_state_routes.py").read_text(encoding="utf-8")
    check("state route: legs carry sl / tp; cfg carries sl_pct / tp_pct",
          '"sl": leg.get("sl")' in r and '"tp_pct"' in r)
    fe = BACKEND.parent / "frontend/src/strategies/vet/VETPanel.jsx"
    if fe.exists():
        p = fe.read_text(encoding="utf-8")
        check("panel: 'no SL/TP by design' only when both are 0", "sltpHeader" in p and FENCE in p)
        check("panel: TP / SL levels on the track + exits line", "pos.tp != null" in p and "pos.sl != null" in p)


if __name__ == "__main__":
    t_levels()
    t_entry_stamp()
    t_exits()
    t_restart()
    t_parity()
    t_wiring()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} VET SL/TP LIVE CHECKS PASSED")
