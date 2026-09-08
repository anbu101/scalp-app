#!/usr/bin/env python3
"""
apply_FLEET_EOD_ALERT_FIX_20260908.py — three 2026-09-08 production findings.

FENCE: FLEET_EOD_ALERT_FIX_20260908

1. TSG_V1 (Tigris) — rejection reason never reaches the phone.
   09:16:07 L2 SELL 23800PE REJECTED "Insufficient funds. Margin required
   147073.33 / available 111340.10". Telegram said only "entry failed at L2 —
   unwound". Root cause: get_order_fill() never returned Kite's
   status_message, and the REJECTED branch discarded everything but the
   status. Fix: both executors surface status_message (Kite:
   status_message/status_message_raw, Angel: text); every failure dict from
   _place_and_confirm carries a `reason`; the TSG_ENTRY_UNWIND alert names
   the leg (action + symbol), the reason, the legs unwound and any partial
   residual flattened.

2. VET_V1 (Velvet) — paper trades closed today, absent from the EOD card.
   vet_manager.close_position() writes pnl (gross) but never charges/net_pnl,
   so the row's net_pnl stays NULL. telegram_summary_data._merge_tma reads
   SUM(net_pnl) per group and SKIPS groups where it is NULL — Velvet got no
   row at all. Fix: compute charges per leg with the SAME charges_model the
   VET backtest uses (charges_for_short_trade for the SELL main,
   charges_for_long_trade otherwise) and persist charges + net_pnl. Best
   effort: a charges failure logs and stores net = gross, it never blocks
   the close.

3. ORB_V1 (Outrider) — zero paper trades since launch.
   orb_engine._chain_snapshot() imports snapshot_weekly_chain from
   app.marketdata.chain_snapshot — a module that DOES NOT EXIST (the
   function lives in app.engine.ic.ic_selection, where BRK/TSG/GC import it).
   Every SIGNAL → ImportError (swallowed, audit-log only) → {} → NO_CANDIDATE
   → on_entry_abandoned. Second defect behind it: the real function returns a
   (expiry, rows, ltp_by_symbol) TUPLE and the ORB code called .items() on it
   as a dict. test_orb_manager never exercised _chain_snapshot, so both
   shipped. Fix: correct import, correct unpacking, meta cached from rows.
   CHAIN_FAIL and NO_CANDIDATE now also raise an in-app alert (they were
   audit-log-only, i.e. invisible from the UI).
   ORB_LATEBOOT (same file, separate sub-fence): _warm_replay ran only when a
   position was being resumed. A boot/restart after 09:15 with no position
   started the prefix at the current minute, so compute_orb (fail-closed:
   ALL 3 buckets from 09:15) refused every such day. Now the day is
   warm-replayed from kite historical whenever it is armed late, position or
   not (adopt_resumed_position is a no-op with no row).

Patches (fenced, anchored, uniqueness-asserted):
  backend/app/execution/zerodha_executor.py   get_order_fill +status_message
  backend/app/execution/angel_executor.py     get_order_fill +status_message
  backend/app/engine/tsg/tsg_manager.py       reason plumbing + alert text
  backend/app/engine/vet/vet_manager.py       charges + net_pnl on close
  backend/app/engine/orb/orb_engine.py        chain snapshot + late-boot replay

Safety: fence presence check (abort if applied), py_compile gate before any
write, behavioural simulation suite on the PATCHED text before any write,
staged all-or-nothing writes with .bak-FENCE backups, dual-tree mirror
(desktop/src-tauri/backend) when present. Backend-only → PyInstaller rebuild.

Run from the repo root:  python3 apply_FLEET_EOD_ALERT_FIX_20260908.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile, types

FENCE = "FLEET_EOD_ALERT_FIX_20260908"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")
if not os.path.isdir(os.path.join(BACKEND, "app", "engine", "orb")):
    sys.exit("ABORT: run from the scalp-app repo root")

REL = {
    "zerodha": "app/execution/zerodha_executor.py",
    "angel":   "app/execution/angel_executor.py",
    "tsg":     "app/engine/tsg/tsg_manager.py",
    "vet":     "app/engine/vet/vet_manager.py",
    "orb":     "app/engine/orb/orb_engine.py",
}
PATH = {k: os.path.join(BACKEND, v) for k, v in REL.items()}


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


for k, p in PATH.items():
    if not os.path.isfile(p):
        die(f"missing {p}")
    if FENCE in read(p):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")

# ═══════════════════════════════════════════════════════════════════════
# 1a. zerodha_executor.get_order_fill  → + status_message
# ═══════════════════════════════════════════════════════════════════════
z = read(PATH["zerodha"])
z = sub1(z,
    '            "filled_qty": 0, "pending_qty": 0, "found": False,\n        }',
    '            "filled_qty": 0, "pending_qty": 0, "found": False,\n'
    '            "status_message": "",   # ── FLEET_EOD_ALERT_FIX_20260908 ──\n        }',
    "zerodha empty dict")
z = sub1(z,
    '                        "pending_qty": int(o.get("pending_quantity") or 0),\n'
    '                        "found":       True,\n',
    '                        "pending_qty": int(o.get("pending_quantity") or 0),\n'
    '                        "found":       True,\n'
    '                        # ── FLEET_EOD_ALERT_FIX_20260908 ── the broker\'s\n'
    '                        # rejection text (e.g. "Insufficient funds. Margin\n'
    '                        # required: ..."). Additive: every existing caller\n'
    '                        # reads status/avg_price/filled_qty only.\n'
    '                        "status_message": str(o.get("status_message")\n'
    '                                              or o.get("status_message_raw")\n'
    '                                              or ""),\n',
    "zerodha found dict")

# ═══════════════════════════════════════════════════════════════════════
# 1b. angel_executor.get_order_fill  → + status_message (Angel: `text`)
# ═══════════════════════════════════════════════════════════════════════
a = read(PATH["angel"])
a = sub1(a,
    '        empty = {"status": None, "avg_price": 0.0,\n'
    '                 "filled_qty": 0, "pending_qty": 0, "found": False}',
    '        empty = {"status": None, "avg_price": 0.0,\n'
    '                 "filled_qty": 0, "pending_qty": 0, "found": False,\n'
    '                 "status_message": ""}   # ── FLEET_EOD_ALERT_FIX_20260908 ──',
    "angel empty dict")
a = sub1(a,
    '                        "pending_qty": max(0, total - filled),\n'
    '                        "found": True,\n',
    '                        "pending_qty": max(0, total - filled),\n'
    '                        "found": True,\n'
    '                        # ── FLEET_EOD_ALERT_FIX_20260908 ── Angel puts the\n'
    '                        # rejection text in `text`.\n'
    '                        "status_message": str(o.get("text") or ""),\n',
    "angel found dict")

# ═══════════════════════════════════════════════════════════════════════
# 1c. tsg_manager — reason plumbing + alert text
# ═══════════════════════════════════════════════════════════════════════
t = read(PATH["tsg"])

# helper: put a module-level truncator right before the class' _alert docs
# (module-level function, no class coupling; inserted before `class`).
t = sub1(t,
    '\n\nclass TsgManager',
    '\n\n# ── FLEET_EOD_ALERT_FIX_20260908 ── one-line, Telegram-sized reason text.\n'
    'def _reason_text(status, msg, limit=220) -> str:\n'
    '    s = " ".join(str(msg or "").split())\n'
    '    out = f"{status}: {s}" if s else str(status or "")\n'
    '    return out if len(out) <= limit else out[:limit - 1] + "…"\n'
    '\n\nclass TsgManager',
    "tsg class anchor")

# (i) REJECTED/CANCELLED/LAPSED during the wait loop — carry the broker text
t = sub1(t,
    '                    if status in ("REJECTED", "CANCELLED", "LAPSED"):\n'
    '                        write_audit_log(\n'
    '                            f"[TSG][ENTRY][{leg.leg_id}] order {status} "\n'
    '                            f"broker-side — no re-peg possible")\n'
    '                        return {"ok": False, "avg": None,\n'
    '                                "filled_qty": filled}\n',
    '                    if status in ("REJECTED", "CANCELLED", "LAPSED"):\n'
    '                        # ── FLEET_EOD_ALERT_FIX_20260908 ── keep the\n'
    '                        # broker\'s text (2026-09-08: "Insufficient funds …"\n'
    '                        # never reached the alert).\n'
    '                        reason = _reason_text(status, st.get("status_message"))\n'
    '                        write_audit_log(\n'
    '                            f"[TSG][ENTRY][{leg.leg_id}] order {status} "\n'
    '                            f"broker-side — no re-peg possible ({reason})")\n'
    '                        return {"ok": False, "avg": None,\n'
    '                                "filled_qty": filled, "reason": reason}\n',
    "tsg wait-loop rejected branch")

# (ii) placement exception path → reason
t = sub1(t,
    '                    write_audit_log(\n'
    '                        f"[TSG][ENTRY][{leg.leg_id}][TEARDOWN_FAIL] {e2!r}")\n'
    '            return fail\n',
    '                    write_audit_log(\n'
    '                        f"[TSG][ENTRY][{leg.leg_id}][TEARDOWN_FAIL] {e2!r}")\n'
    '            fail["reason"] = _reason_text("PLACE_FAIL", repr(e))   # ── FLEET_EOD_ALERT_FIX_20260908 ──\n'
    '            return fail\n',
    "tsg place-fail return")

# (iii) cancel/readback path → capture status_message, report status
t = sub1(t,
    '        filled, avg, status = 0, 0.0, ""\n'
    '        cancel_t0 = time.time()\n',
    '        filled, avg, status = 0, 0.0, ""\n'
    '        smsg = ""                        # ── FLEET_EOD_ALERT_FIX_20260908 ──\n'
    '        cancel_t0 = time.time()\n',
    "tsg readback init")
t = sub1(t,
    '                avg = float(st.get("avg_price") or 0.0)\n'
    '                if status == "COMPLETE":\n'
    '                    # cancel raced a full fill — the leg is actually ours\n',
    '                avg = float(st.get("avg_price") or 0.0)\n'
    '                smsg = st.get("status_message") or smsg   # ── FLEET_EOD_ALERT_FIX_20260908 ──\n'
    '                if status == "COMPLETE":\n'
    '                    # cancel raced a full fill — the leg is actually ours\n',
    "tsg readback loop")
t = sub1(t,
    '        return {"ok": False,\n'
    '                "avg": avg if filled > 0 else None,\n'
    '                "filled_qty": filled}\n'
    '    # ── TSG_ENTRY_TEARDOWN_20260825 END ──\n',
    '        return {"ok": False,\n'
    '                "avg": avg if filled > 0 else None,\n'
    '                "filled_qty": filled,\n'
    '                # ── FLEET_EOD_ALERT_FIX_20260908 ── REJECTED carries the\n'
    '                # broker text; a timeout says so instead of nothing.\n'
    '                "reason": (_reason_text(status, smsg) if status == "REJECTED"\n'
    '                           else _reason_text(status or "UNRESOLVED",\n'
    '                                             "unfilled after re-peg window"))}\n'
    '    # ── TSG_ENTRY_TEARDOWN_20260825 END ──\n',
    "tsg readback return")

# (iv) the alert itself
t = sub1(t,
    '                if res["filled_qty"] > 0:\n'
    '                    self._flatten_entry_residual(leg, res["filled_qty"])\n'
    '                for open_id in self._core.leg_entry_dead(lid):\n'
    '                    self._market_close(self._core.legs[open_id], "UNWIND")\n'
    '                self._alert("TSG_ENTRY_UNWIND",\n'
    '                            f"TSG_V1 LIVE entry failed at {lid} — unwound",\n'
    '                            severity="error", mode="live")\n',
    '                if res["filled_qty"] > 0:\n'
    '                    self._flatten_entry_residual(leg, res["filled_qty"])\n'
    '                unwound = list(self._core.leg_entry_dead(lid))\n'
    '                for open_id in unwound:\n'
    '                    self._market_close(self._core.legs[open_id], "UNWIND")\n'
    '                # ── FLEET_EOD_ALERT_FIX_20260908 ── say WHICH order died\n'
    '                # and WHY (2026-09-08: an "Insufficient funds" rejection\n'
    '                # surfaced on the phone as a bare "entry failed at L2").\n'
    '                _why = res.get("reason") or "no broker reason"\n'
    '                _part = (f" · partial {res[\'filled_qty\']}/{leg.qty} flattened"\n'
    '                         if res["filled_qty"] > 0 else "")\n'
    '                _unw = ", ".join(unwound) if unwound else "nothing open"\n'
    '                self._alert("TSG_ENTRY_UNWIND",\n'
    '                            f"TSG_V1 LIVE entry failed at {lid} "\n'
    '                            f"({leg.action} {leg.symbol} x{leg.qty}) — {_why}"\n'
    '                            f"{_part} · unwound: {_unw}",\n'
    '                            severity="error", mode="live")\n',
    "tsg unwind alert")

# ═══════════════════════════════════════════════════════════════════════
# 2. vet_manager — charges + net_pnl on close (EOD card reads net_pnl)
# ═══════════════════════════════════════════════════════════════════════
v = read(PATH["vet"])
v = sub1(v,
    '        if main.get("db_id"):\n'
    '            self.repo.close_leg(main["db_id"], exit_ts=ts, exit_price=mpx,\n'
    '                                exit_reason=reason, pnl=round(gross, 2),\n'
    '                                exit_order_id=moid)\n'
    '        total = gross\n',
    '        # ── FLEET_EOD_ALERT_FIX_20260908 ── persist charges + net_pnl.\n'
    '        # The EOD card (_merge_tma over vet_trades) sums net_pnl per group\n'
    '        # and skips groups where it is NULL — Velvet\'s paper day vanished\n'
    '        # from the summary on 2026-09-08 for exactly that reason.\n'
    '        m_ch = self._leg_charges(self.is_sell, main["entry_price"], mpx)\n'
    '        if main.get("db_id"):\n'
    '            self.repo.close_leg(main["db_id"], exit_ts=ts, exit_price=mpx,\n'
    '                                exit_reason=reason, pnl=round(gross, 2),\n'
    '                                charges=round(m_ch, 2),\n'
    '                                net_pnl=round(gross - m_ch, 2),\n'
    '                                exit_order_id=moid)\n'
    '        total = gross\n'
    '        total_net = gross - m_ch\n',
    "vet main close")
v = sub1(v,
    '            total += wg\n'
    '            if wing.get("db_id"):\n'
    '                self.repo.close_leg(wing["db_id"], exit_ts=ts, exit_price=wpx,\n'
    '                                    exit_reason=reason, pnl=round(wg, 2),\n'
    '                                    exit_order_id=woid)\n'
    '        self.pos = None\n'
    '        write_audit_log(f"[VET][MGR] CLOSE {pos[\'group_id\']} {reason} "\n'
    '                        f"gross {total:,.0f}")\n'
    '        return {"group_id": pos["group_id"], "reason": reason,\n'
    '                "gross": round(total, 2)}\n',
    '            total += wg\n'
    '            w_ch = self._leg_charges(False, wing["entry_price"], wpx)   # ── FLEET_EOD_ALERT_FIX_20260908 ──\n'
    '            total_net += wg - w_ch\n'
    '            if wing.get("db_id"):\n'
    '                self.repo.close_leg(wing["db_id"], exit_ts=ts, exit_price=wpx,\n'
    '                                    exit_reason=reason, pnl=round(wg, 2),\n'
    '                                    charges=round(w_ch, 2),\n'
    '                                    net_pnl=round(wg - w_ch, 2),\n'
    '                                    exit_order_id=woid)\n'
    '        self.pos = None\n'
    '        write_audit_log(f"[VET][MGR] CLOSE {pos[\'group_id\']} {reason} "\n'
    '                        f"gross {total:,.0f} net {total_net:,.0f}")\n'
    '        return {"group_id": pos["group_id"], "reason": reason,\n'
    '                "gross": round(total, 2), "net": round(total_net, 2)}\n',
    "vet wing close + return")
v = sub1(v,
    '    def _mark(self, sym: str, fallback: float) -> float:\n',
    '    # ── FLEET_EOD_ALERT_FIX_20260908 ── same charges model as the VET\n'
    '    # backtest runner (charges_for_short_trade for the SELL main leg,\n'
    '    # charges_for_long_trade otherwise). Best-effort: any failure is logged\n'
    '    # and charged as 0 so the close is never blocked; net then equals gross\n'
    '    # (still NOT NULL, so the EOD card counts the trade).\n'
    '    def _leg_charges(self, is_short: bool, entry: float, exit_px: float) -> float:\n'
    '        try:\n'
    '            from app.backtest.charges.charges_model import (\n'
    '                charges_for_long_trade, charges_for_short_trade)\n'
    '            fn = charges_for_short_trade if is_short else charges_for_long_trade\n'
    '            cr = fn(entry_price=float(entry), exit_price=float(exit_px),\n'
    '                    qty=int(self.qty))\n'
    '            return float(getattr(cr, "total_charges", 0.0) or 0.0)\n'
    '        except Exception as e:\n'
    '            write_audit_log(f"[VET][MGR] charges calc failed ({e!r}) — "\n'
    '                            f"booking net = gross")\n'
    '            return 0.0\n'
    '\n'
    '    def _mark(self, sym: str, fallback: float) -> float:\n',
    "vet _leg_charges helper")

# ═══════════════════════════════════════════════════════════════════════
# 3. orb_engine — chain snapshot (import + contract) and late-boot replay
# ═══════════════════════════════════════════════════════════════════════
o = read(PATH["orb"])
o = sub1(o,
    '    def _chain_snapshot(self) -> Dict[str, float]:\n'
    '        """symbol -> premium for the EXPECTED weekly expiry (LD7); caches\n'
    '        token/type meta for selection."""\n'
    '        kite = self._kite()\n'
    '        if kite is None:\n'
    '            return {}\n'
    '        try:\n'
    '            from app.marketdata.chain_snapshot import snapshot_weekly_chain\n'
    '            api_key = getattr(kite, "api_key", None)\n'
    '            access_token = getattr(kite, "access_token", None)\n'
    '            snap = snapshot_weekly_chain(kite, api_key, access_token) or {}\n'
    '        except Exception as e:\n'
    '            write_audit_log(f"[ORB][CHAIN_FAIL] {e!r}")\n'
    '            return {}\n'
    '        prints: Dict[str, float] = {}\n'
    '        for sym, row in snap.items():\n'
    '            try:\n'
    '                prints[sym] = float(row.get("last_price") or 0)\n'
    '                self._chain_meta[sym] = {\n'
    '                    "token": row.get("instrument_token") or row.get("token"),\n'
    '                    "instrument_type": row.get("instrument_type")\n'
    '                    or ("CE" if sym.endswith("CE") else "PE")}\n'
    '            except Exception:\n'
    '                continue\n'
    '        return prints\n',
    '    def _chain_snapshot(self) -> Dict[str, float]:\n'
    '        """symbol -> premium for the EXPECTED weekly expiry (LD7); caches\n'
    '        token/type meta for selection.\n'
    '\n'
    '        ── FLEET_EOD_ALERT_FIX_20260908 ── ORB never traded since launch:\n'
    '        (1) the import pointed at app.marketdata.chain_snapshot, a module\n'
    '        that does not exist (BRK/TSG/GC import it from ic_selection), so\n'
    '        every SIGNAL raised ImportError → {} → NO_CANDIDATE; (2) the real\n'
    '        function returns (expiry, rows, ltp_by_symbol), not a dict, so\n'
    '        `.items()` would have failed next. Both fixed; failures now also\n'
    '        raise an in-app alert instead of an audit-log-only line.\n'
    '        """\n'
    '        kite = self._kite()\n'
    '        if kite is None:\n'
    '            self.gm._alert("CHAIN_FAIL", "data kite unavailable (broker not "\n'
    '                           "connected / not logged in) — signal forfeited",\n'
    '                           "error")\n'
    '            return {}\n'
    '        try:\n'
    '            from app.engine.ic.ic_selection import snapshot_weekly_chain\n'
    '            api_key = getattr(kite, "api_key", None)\n'
    '            access_token = getattr(kite, "access_token", None)\n'
    '            expiry, rows, ltp = snapshot_weekly_chain(kite, api_key,\n'
    '                                                      access_token)\n'
    '        except Exception as e:\n'
    '            self.gm._alert("CHAIN_FAIL", f"{e!r} — signal forfeited", "error")\n'
    '            return {}\n'
    '        if not rows:\n'
    '            self.gm._alert("CHAIN_FAIL", "empty weekly chain snapshot "\n'
    '                           "(fail-closed) — signal forfeited", "error")\n'
    '            return {}\n'
    '        prints: Dict[str, float] = {}\n'
    '        for row in rows:\n'
    '            try:\n'
    '                sym = str(row.get("tradingsymbol") or "")\n'
    '                if not sym:\n'
    '                    continue\n'
    '                prints[sym] = float((ltp or {}).get(sym) or 0)\n'
    '                self._chain_meta[sym] = {\n'
    '                    "token": row.get("instrument_token") or row.get("token"),\n'
    '                    "instrument_type": row.get("instrument_type")\n'
    '                    or ("CE" if sym.endswith("CE") else "PE")}\n'
    '            except Exception:\n'
    '                continue\n'
    '        write_audit_log(f"[ORB][CHAIN] expiry={expiry} symbols={len(prints)} "\n'
    '                        f"quoted={sum(1 for p in prints.values() if p > 0)}")\n'
    '        return prints\n',
    "orb _chain_snapshot")
o = sub1(o,
    '        if sym is None:\n'
    '            write_audit_log(f"[ORB][NO_CANDIDATE] {side} band "\n'
    '                            f"{cfg.get(\'premium_min\')}-{cfg.get(\'premium_max\')}")\n',
    '        if sym is None:\n'
    '            # ── FLEET_EOD_ALERT_FIX_20260908 ── in-app bell, not just the log\n'
    '            self.gm._alert("NO_CANDIDATE",\n'
    '                           f"{side} signal — no option in premium band "\n'
    '                           f"{cfg.get(\'premium_min\')}-{cfg.get(\'premium_max\')} "\n'
    '                           f"({len(sided)} {side} quotes) — signal forfeited")\n',
    "orb NO_CANDIDATE alert")
o = sub1(o,
    '        write_audit_log(f"[ORB][DAY] armed {key} cfg_target="\n'
    '                        f"{cfg.get(\'target_value\')}")\n'
    '        if self.gm.pos is not None:\n'
    '            self._warm_replay(now)\n',
    '        write_audit_log(f"[ORB][DAY] armed {key} cfg_target="\n'
    '                        f"{cfg.get(\'target_value\')}")\n'
    '        # ── FLEET_EOD_ALERT_FIX_20260908 / ORB_LATEBOOT ── a day armed\n'
    '        # after the first session minute has already missed bars; compute_orb\n'
    '        # is fail-closed on ANY missing 09:15–09:29 bucket, so without a\n'
    '        # replay every post-09:15 boot/restart refused the whole day.\n'
    '        # _warm_replay rebuilds the prefix from kite historical 1m and\n'
    '        # adopt_resumed_position() is a no-op when there is no row.\n'
    '        _hm = now.hour * 60 + now.minute\n'
    '        if self.gm.pos is not None or _hm > SESSION_OPEN_MIN:\n'
    '            self._warm_replay(now)\n',
    "orb late-boot replay")

NEW = {"zerodha": z, "angel": a, "tsg": t, "vet": v, "orb": o}

# ═══════════════════════════════════════════════════════════════════════
# py_compile gate (on temp copies — nothing written yet)
# ═══════════════════════════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix=f"{FENCE}_")
TMPF = {}
for k, text in NEW.items():
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    TMPF[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK (5 files)")

# ═══════════════════════════════════════════════════════════════════════
# behavioural simulation suite (patched text, real app package for deps)
# ═══════════════════════════════════════════════════════════════════════
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)

def load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod

sys.path.insert(0, BACKEND)

# ── sim 1: TSG reason plumbing + alert wording ─────────────────────────
print("sim 1 — TSG_V1 rejection reason reaches the alert")
TSG = load("app.engine.tsg.tsg_manager", TMPF["tsg"])
class _Leg:
    def __init__(self, lid="L2"): self.leg_id, self.symbol, self.qty, self.action = lid, "NIFTY2690823800PE", 650, "SELL"
    is_short = True
class _Exec:
    def __init__(self, msg): self.msg = msg
    def get_order_fill(self, oid):
        return {"status": "REJECTED", "avg_price": 0.0, "filled_qty": 0,
                "pending_qty": 0, "found": True, "status_message": self.msg}
mgr = TSG.TsgManager.__new__(TSG.TsgManager)
mgr.executor = _Exec("Insufficient funds. Margin required: 147073.33. "
                     "Margin available: 111340.10. Add 35733.23 to place this order.")
mgr._cfg = lambda: {"entry_fill_timeout_s": 2, "entry_repeg_max": 0}
res = mgr._confirm_entry_with_repeg(_Leg(), "2097169587274555392", 116.45)
check("rejected fill dict carries reason",
      res["ok"] is False and res.get("reason", "").startswith("REJECTED: Insufficient funds"), str(res))
check("reason is single-line and bounded", "\n" not in res["reason"] and len(res["reason"]) <= 220)
# the alert text itself: drive _enter_live's failure branch with stubs
alerts = []
class _Core:
    def __init__(self): self.legs = {k: _Leg(k) for k in ("L1", "L2", "L3", "L4")}; self.state = None
    def leg_entry_dead(self, lid): return ["L3", "L4", "L1"]
    def leg_filled(self, lid, avg, order_id=None): pass
mgr2 = TSG.TsgManager.__new__(TSG.TsgManager)
mgr2.executor = type("E", (), {m: (lambda *a, **k: None) for m in
    ("place_buy", "place_sell_entry", "get_order_fill", "cancel_order", "modify_order",
     "fresh_buy_entry_limit", "fresh_sell_entry_limit", "place_market_sell", "place_buy_exit")})()
mgr2._core = _Core()
mgr2._place_and_confirm = lambda leg: (
    {"ok": True, "avg": 100.0, "filled_qty": 650} if leg.leg_id != "L2"
    else {"ok": False, "avg": None, "filled_qty": 0, "reason": res["reason"]})
mgr2._book_live_row = lambda leg: None
mgr2._market_close = lambda leg, why: None
mgr2._start_post_abort_reconcile = lambda: None
mgr2._flatten_entry_residual = lambda leg, q: None
mgr2._alert = lambda code, msg, **k: alerts.append((code, msg))
ok = mgr2._enter_live([], {})
msg = alerts[-1][1] if alerts else ""
check("entry returns False and alerts once", ok is False and len(alerts) == 1, str(alerts))
check("alert names leg + action + symbol", "L2 (SELL NIFTY2690823800PE x650)" in msg, msg)
check("alert carries broker reason", "Insufficient funds. Margin required: 147073.33" in msg, msg)
check("alert lists unwound legs", "unwound: L3, L4, L1" in msg, msg)
# timeout path wording (no status_message) — must say UNRESOLVED/CANCELLED, never blank
class _ExecTO:
    def get_order_fill(self, oid): return {"status": "OPEN", "found": True, "filled_qty": 0, "avg_price": 0.0}
    def cancel_order(self, oid, symbol=""): pass
mgr3 = TSG.TsgManager.__new__(TSG.TsgManager); mgr3.executor = _ExecTO()
mgr3._cfg = lambda: {"entry_fill_timeout_s": 2, "entry_repeg_max": 0}
mgr3._alert = lambda *a, **k: None
TSG.time.sleep = lambda s: None                                # don't actually wait
_t0 = [TSG.time.time()]
TSG.time.time = lambda: (_t0.__setitem__(0, _t0[0] + 3) or _t0[0])   # fast clock
r3 = mgr3._confirm_entry_with_repeg(_Leg(), "X", 100.0)
check("timeout path reports a status, not blank", r3["ok"] is False and r3.get("reason", "").startswith("OPEN:"), str(r3))

# ── sim 2: VET net_pnl persisted on close ───────────────────────────────
print("sim 2 — VET_V1 close writes charges + net_pnl (EOD card contract)")
import sqlite3
from app.engine.vet.vet_common import VetRepo
VET = load("app.engine.vet.vet_manager", TMPF["vet"])
SPOT = 24500.0
def chain(side, ts, cheap=True):
    # mirrors engine/vet/test_vet_manager.chain: ladder wide enough that far
    # strikes reach the Rs 3 wing cap (else every hedged entry "skips")
    out = []
    for k in range(int(SPOT) - 1000, int(SPOT) + 1050, 50):
        px = max(0.05, 180 - abs(k - SPOT) * 0.35)
        out.append({"tradingsymbol": f"NIFTY26SEP{k}{side}", "token": 1000 + k,
                    "strike": float(k), "expiry": "2026-09-08",
                    "instrument_type": side, "ltp": round(px, 2)})
    return out
vdb = os.path.join(TMP, "vet_sim.db")
repo = VetRepo(vdb); repo.ensure_schema()
def _mk(extra=None, execu=None):
    cfg = {"leg_action": "BUY", "atm_offset": -1, "eod_square": True,
           "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT}
    cfg.update(extra or {})
    return VET.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=lambda s: 150.0,
                          executor=execu, mode="LIVE" if execu else "PAPER")
vm = _mk()
p = vm.open_position("CE", ts=1000, bar_ts=1000, condition=1)
check("BUY position opened", p is not None, "open_position returned None (chain shape?)")
r = vm.close_position("EOD", ts=2000)
row = sqlite3.connect(vdb).execute(
    "SELECT pnl, charges, net_pnl, status FROM vet_trades ORDER BY id DESC LIMIT 1").fetchone()
check("row CLOSED with net_pnl NOT NULL", row is not None and row[3] == "CLOSED" and row[2] is not None, str(row))
check("charges > 0 and net = gross - charges",
      row is not None and row[1] is not None and row[1] > 0 and abs(row[2] - (row[0] - row[1])) < 0.02, str(row))
check("close_position returns net", r is not None and "net" in r and abs(r["net"] - row[2]) < 0.02, str(r))
# and the summary-card reader now counts the group
from app.api import telegram_summary_data as TSD
c = sqlite3.connect(vdb); c.row_factory = sqlite3.Row
TSD.get_conn = lambda: c
TSD._today_midnight_ts = lambda: 0
out = {}
TSD._merge_tma(out, paper=True, table="vet_trades", sid="VET_V1")
check("_merge_tma now yields a VET_V1 row", "VET_V1" in out and out["VET_V1"]["trades"] == 1, str(out))
# SELL + wing (live-shaped executor) — both legs get net
class _VExec:
    def place_sell_entry(self, sym, tok, qty): return "O1"
    def place_buy(self, sym, tok, qty): return "O2"
    def place_market_sell(self, sym, qty): return "O3"
    def place_buy_exit(self, sym, qty, reason): return "O4"
try:
    vm2 = _mk({"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0}, execu=_VExec())
    p2 = vm2.open_position("PE", ts=3000, bar_ts=3000, condition=1)
    vm2.close_position("FLIP", ts=4000)
    rows2 = sqlite3.connect(vdb).execute(
        "SELECT leg_role, net_pnl FROM vet_trades WHERE entry_ts=3000").fetchall()
    check("SELL main + wing both carry net_pnl",
          len(rows2) == 2 and all(r_[1] is not None for r_ in rows2), str(rows2))
except Exception as e:
    check("SELL + wing path", False, repr(e))

# ── sim 3: ORB chain snapshot import + contract + candidate selection ──
print("sim 3 — ORB_V1 signal → chain snapshot → candidate → open_trade")
ORB = load("app.engine.orb.orb_engine", TMPF["orb"])
import app.engine.ic.ic_selection as ICS
def fake_snapshot(kite, api_key, access_token, **k):
    rows = [{"tradingsymbol": "NIFTY24500CE", "instrument_token": 11, "strike": 24500, "instrument_type": "CE"},
            {"tradingsymbol": "NIFTY24550CE", "instrument_token": 12, "strike": 24550, "instrument_type": "CE"},
            {"tradingsymbol": "NIFTY24500PE", "instrument_token": 13, "strike": 24500, "instrument_type": "PE"}]
    return ("2026-09-08", rows, {"NIFTY24500CE": 185.0, "NIFTY24550CE": 160.0, "NIFTY24500PE": 210.0})
ICS.snapshot_weekly_chain = fake_snapshot
class _K: api_key = "k"; access_token = "t"
class _Broker:
    def get_data_kite(self): return _K()
opened, oalerts = [], []
class _GM:
    def __init__(self): self.day = types.SimpleNamespace(on_entry_abandoned=lambda: oalerts.append("ABANDONED")); self.day_stats = {"signals": 0}
    def cfg(self): return {"premium_min": 150, "premium_max": 200}
    def _alert(self, code, msg, sev="warning"): oalerts.append((code, msg))
    def open_trade(self, **k): opened.append(k); return True
eng = ORB.OrbEngine(_GM(), _Broker())
eng._on_signal("CE", 1_700_000_000, 24510.0)
check("snapshot import resolves (no CHAIN_FAIL)", not any(a[0] == "CHAIN_FAIL" for a in oalerts if isinstance(a, tuple)), str(oalerts))
check("picks highest CE premium strictly below 200 (185 → 24500CE)",
      len(opened) == 1 and opened[0]["symbol"] == "NIFTY24500CE" and opened[0]["token"] == 11, str(opened))
check("PE quotes never leak into a CE pick", opened and opened[0]["side"] == "CE")
# no candidate → in-app alert + slot released
opened.clear(); oalerts.clear()
ICS.snapshot_weekly_chain = lambda *a, **k: ("2026-09-08",
    [{"tradingsymbol": "NIFTY24500CE", "instrument_token": 11, "instrument_type": "CE"}], {"NIFTY24500CE": 90.0})
eng2 = ORB.OrbEngine(_GM(), _Broker()); eng2._on_signal("CE", 1_700_000_000, 24510.0)
check("out-of-band → NO_CANDIDATE alert + slot released",
      not opened and any(isinstance(a, tuple) and a[0] == "NO_CANDIDATE" for a in oalerts) and "ABANDONED" in oalerts, str(oalerts))
# fail-closed snapshot → CHAIN_FAIL alert, nothing opened
opened.clear(); oalerts.clear()
ICS.snapshot_weekly_chain = lambda *a, **k: (None, [], {})
eng3 = ORB.OrbEngine(_GM(), _Broker()); eng3._on_signal("PE", 1_700_000_000, 24510.0)
check("empty snapshot → CHAIN_FAIL alert, no trade",
      not opened and any(isinstance(a, tuple) and a[0] == "CHAIN_FAIL" for a in oalerts), str(oalerts))
# late-boot replay: day armed at 09:20 with NO position must call _warm_replay
called = []
class _GM2(_GM):
    pos = None
    def cfg(self): return {"target_value": 50, "orb_minutes": 15, "timeframe_minutes": 5,
                           "entry_block_time": "12:00", "eod_square_off": "13:00"}
eng4 = ORB.OrbEngine(_GM2(), _Broker()); eng4._warm_replay = lambda now: called.append(now)
late = ORB.now_ist().replace(hour=9, minute=20, second=3)
eng4._roll_day(late)
check("day armed at 09:20 with no position → warm replay", len(called) == 1)
eng5 = ORB.OrbEngine(_GM2(), _Broker()); eng5._warm_replay = lambda now: called.append(now)
eng5._roll_day(ORB.now_ist().replace(hour=9, minute=15, second=4))
check("day armed at 09:15 → no replay (bars built live)", len(called) == 1)

# ── sim 4: executor contracts carry status_message ─────────────────────
print("sim 4 — executor get_order_fill contract")
ZE = load("app.execution.zerodha_executor", TMPF["zerodha"])
class _Kite:
    def orders(self): return [{"order_id": "1", "status": "REJECTED", "average_price": 0,
                               "filled_quantity": 0, "pending_quantity": 0,
                               "status_message": "Insufficient funds."}]
ze = ZE.ZerodhaOrderExecutor.__new__(ZE.ZerodhaOrderExecutor); ze._kite = lambda: _Kite()
f = ze.get_order_fill("1")
check("zerodha: status_message surfaced", f.get("status_message") == "Insufficient funds." and f["status"] == "REJECTED", str(f))
check("zerodha: existing keys untouched", set(("status", "avg_price", "filled_qty", "pending_qty", "found")) <= set(f))
ze._kite = lambda: None
check("zerodha: empty dict still has the key", ze.get_order_fill("1").get("status_message") == "")
AE = load("app.execution.angel_executor", TMPF["angel"])
ae = AE.AngelOneExecutor.__new__(AE.AngelOneExecutor)
ae.get_orders = lambda: [{"orderid": "9", "status": "rejected", "filledshares": "0", "quantity": "65",
                          "averageprice": "0", "text": "Insufficient Margin"}]
ae._f = lambda x: float(x or 0)
g = ae.get_order_fill("9")
check("angel: status_message from `text`, status upper-cased", g.get("status_message") == "Insufficient Margin" and g["status"] == "REJECTED", str(g))

if FAILS:
    die(f"simulation suite failed: {FAILS}")
print("simulation suite: OK")

# ═══════════════════════════════════════════════════════════════════════
# staged all-or-nothing write + dual-tree mirror
# ═══════════════════════════════════════════════════════════════════════
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("primary tree written (5 files, 5 backups)")
if os.path.isdir(DUAL_BACKEND):
    for k, rel in REL.items():
        dst = os.path.join(DUAL_BACKEND, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
else:
    print("dual backend tree absent (build script rsyncs it) — skipped")
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nDONE — {FENCE} applied. Backend-only: ./desktop/build-scalp.sh backend")
