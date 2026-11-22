# test_broker_truth_mutsafety.py — ── BT_MUTSAFETY_20261122 ── (TD-043) TC-EXEC-0234…0243
# Tests for mutants that SURVIVED mutmut on broker_truth.py (blind stop 40.5 %,
# kill list 40.8 %, exit once 48.7 % on the 21–22 Nov run): the edges the 70
# suites that reach the module never pinned. Each check fails on its mutants
# and passes on main (scripts/mutation/prove_kills.py).
#   0234 blind_stop_orders: price band edges, statuses, quantities, timing, claims, vwap
#   0235 foreign_exit_fill: want_qty slicing, earliest first, stop once full, claims
#   0236 fired_gtt_orders: listed order ids, the order-book fallback and its filters
#   0237 app orders: note / claim / reset / ids
#   0238 exit intents: note, today's only, done, survives a restart; find_tagged
#   0239 send_exit_once: adopt, settled (net-based), pending, the 60 s edge, place under tag
#   0240 GttKillList: add / ids / len / persistence; spent stops
#   0241 GttKillList.retry: gap, verified cancel, tries, page + 10-min repage, texts
#   0242 GttKillList.retry: a GTT that fired (resting / unfilled / filled) and _settle_fired
#   0243 the live (non-test) paths: ~/.scalp-app/state files, the app-order day file across a
#        restart; defaults; several GTTs in one retry pass; spent-stop statuses
#   PYTHONPATH=backend python3 backend/app/execution/test_broker_truth_mutsafety.py
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SCALP_TEST_MODE", "1")

from app.execution import broker_truth as BT                          # noqa: E402
from app.execution import instance_guard as IG                         # noqa: E402

PASSED, FAILS = [0], []
TMP = Path(tempfile.mkdtemp(prefix="bt_mutsafety_"))
T0 = 1_795_000_000                          # a real epoch second
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Ex:
    """Order book + GTT list + per-order fills, scripted."""
    def __init__(self, book=None, boom=False, gtts=None, fills=None, status=None,
                 cancel_ok=None, cancel_boom=False, cancel_moves=None):
        self.book, self.boom = book or [], boom
        self.gtts = gtts                         # None = unreadable
        self.fills = dict(fills or {})           # oid → {"status", "filled_qty"}
        self.status = dict(status or {})         # gid → "active" / "triggered" / …
        self.cancel_ok, self.cancel_boom = cancel_ok, cancel_boom
        self.cancel_moves = dict(cancel_moves or {})   # oid → status after cancel_order
        self.calls, self.cancelled, self.cancel_gtt = 0, [], []

    def get_orders(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("orders: timeout")
        return self.book

    def get_gtts_or_none(self):
        return self.gtts

    def get_gtt_status(self, gid):
        return self.status.get(str(gid))

    def get_order_fill(self, oid):
        return self.fills.get(str(oid))

    def cancel_order(self, oid):
        self.cancelled.append(str(oid))
        if self.cancel_boom:
            raise RuntimeError("cancel: 500")
        if str(oid) in self.cancel_moves:
            self.fills[str(oid)] = dict(self.fills.get(str(oid)) or {}, status=self.cancel_moves[str(oid)])

    def cancel_gtt_verified(self, gid, retries=0):
        self.cancel_gtt.append((str(gid), retries))
        return self.cancel_ok


def o(oid, qty, st="COMPLETE", px=141.0, side="BUY", sym="X", ts=T0, filled=None):
    return {"order_id": oid, "tradingsymbol": sym, "transaction_type": side, "quantity": qty,
            "filled_quantity": (qty if st == "COMPLETE" else 0) if filled is None else filled,
            "status": st, "average_price": px if st.upper() == "COMPLETE" else 0, "price": px,
            "exchange_timestamp": ts}


def bso(book, short=True, **kw):
    BT.reset_app_orders()
    for a in kw.pop("app", []):
        BT.note_app_order(a)
    args = dict(qtys=[195], want_qty=195, stop_px=140.0)
    args.update(kw)
    return BT.blind_stop_orders(Ex(book), "X", short, **args)


# ── 0234 blind_stop_orders ────────────────────────────────────────────────
TC = "TC-EXEC-0234"
# short leg, stop 140: a working buy must sit in 126 .. 175 (both ends in)
for px, want in ((126.0, ["W"]), (175.0, ["W"]), (125.99, []), (175.01, []), (150.0, ["W"])):
    r = bso([o("W", 195, st="OPEN", px=px)])
    check(f"{TC} short, working buy at {px}: {'taken' if want else 'not taken'} (band 126..175)",
          r == (want, 0, None), r)
# a filled short buy-back: any price up to 175 (a resting limit fills lower)
for px, q in ((175.0, 195), (175.01, 0), (60.0, 195)):
    r = bso([o("F", 195, px=px)])
    check(f"{TC} short, filled buy at {px}: {q or 'none'} counted", r[1] == q, r)
# long leg, stop 20: a working sell must sit in 15 .. 22; a filled one at 15 or above
for px, want in ((15.0, ["W"]), (22.0, ["W"]), (14.99, []), (22.01, [])):
    r = bso([o("W", 130, st="OPEN", px=px, side="SELL")], short=False, qtys=[130], want_qty=130,
            stop_px=20.0)
    check(f"{TC} long, working sell at {px}: {'taken' if want else 'not taken'} (band 15..22)",
          r == (want, 0, None), r)
for px, q in ((15.0, 130), (14.99, 0), (40.0, 130)):
    r = bso([o("F", 130, px=px, side="SELL")], short=False, qtys=[130], want_qty=130, stop_px=20.0)
    check(f"{TC} long, filled sell at {px}: {q or 'none'} counted", r[1] == q, r)
r = bso([o("B", 130, px=19.0, side="BUY")], short=False, qtys=[130], want_qty=130, stop_px=20.0)
check(f"{TC} long leg: a BUY is the entry side, not taken", r == ([], 0, None), r)
# no usable stop price: no band at all
for sp in (None, 0, -5, "n/a"):
    r = bso([o("W", 195, st="OPEN", px=900.0)], stop_px=sp)
    check(f"{TC} stop_px={sp!r}: no band, a working buy at 900 is taken", r == (["W"], 0, None), r)
r = bso([o("W", 195, st="OPEN", px=5.0)], stop_px=0.5)
check(f"{TC} a stop of 0.5 still sets a band (0.45..0.625): a working buy at 5 is not taken",
      r == ([], 0, None), r)
r = bso([o("W", 195, st="OPEN", px=0)])
check(f"{TC} a working order with no price (market) is not price-filtered: taken",
      r == (["W"], 0, None), r)
# statuses, case, missing fields
for st in ("LAPSED", "", "rejected", "Cancelled"):
    r = bso([o("D", 195, st=st)])
    check(f"{TC} status {st!r} is not taken", r == ([], 0, None), r)
r = bso([o("F", 195, st="complete", side="buy")])
check(f"{TC} lower-case 'complete' / 'buy' are read as COMPLETE / BUY", r == ([], 195, 141.0), r)
r = bso([o("T", 195, st="TRIGGER PENDING", px=150.0)])
check(f"{TC} a TRIGGER PENDING order is working: adopted", r == (["T"], 0, None), r)
r = bso([o("", 195), o("F", 195)])
check(f"{TC} a row without an order id is skipped, the next one counted", r == ([], 195, 141.0), r)
r = bso([o("F", 195)], qtys=None)
check(f"{TC} no leg quantities: nothing can match", r == ([], 0, None), r)
r = bso([o("F", 195)], qtys=[0, None, 195])
check(f"{TC} blank quantities in qtys are ignored, 195 still matches", r == ([], 195, 141.0), r)
r = bso([o("F", 195, filled=0)])
check(f"{TC} COMPLETE with filled_quantity 0 falls back to quantity", r == ([], 195, 141.0), r)
r = bso([o("F", 195, px=0)], stop_px=None)
check(f"{TC} a COMPLETE row with no average price is not counted", r == ([], 0, None), r)
bad = o("Z", 195)
bad["quantity"] = "abc"
r = bso([bad, o("F", 195)])
check(f"{TC} a malformed row is skipped (continue, not break)", r == ([], 195, 141.0), r)
# timing: before the leg opened (more than 5 s) → not taken
for dt, q in ((-5, 195), (-6, 0), (0, 195)):
    r = bso([o("F", 195, ts=T0 + dt)], not_before_ts=T0)
    check(f"{TC} an order {dt:+d} s from the leg's open: {q or 'not'} counted (5 s grace)", r[1] == q, r)
# quantities across several orders
r = bso([o("A", 65, px=141.0), o("B", 65, px=142.0)], qtys=[65], want_qty=130)
check(f"{TC} two fills that make the leg exactly are both counted, vwap 141.5",
      r == ([], 130, 141.5), r)
r = bso([o("A", 130), o("B", 130)], qtys=[130], want_qty=195)
check(f"{TC} a fill that would pass the leg's quantity is skipped", r == ([], 130, 141.0), r)
r = bso([o("A", 65, px=141.0), o("B", 130, px=142.0)], qtys=[65, 130], want_qty=195)
check(f"{TC} vwap is rounded to 4 places: 141.6667", r == ([], 195, 141.6667), r)
r = bso([o("W1", 195, st="OPEN", px=150.0), o("W2", 195, st="OPEN", px=151.0)])
check(f"{TC} every working match is returned (a working one has no quantity yet)",
      r == (["W1", "W2"], 0, None), r)
BT.reset_app_orders()
ex = Ex([o("W", 195, st="OPEN", px=150.0)])
r1 = BT.blind_stop_orders(ex, "X", True, qtys=[195], want_qty=195, stop_px=140.0)
r2 = BT.blind_stop_orders(ex, "X", True, qtys=[195], want_qty=195, stop_px=140.0)
check(f"{TC} an adopted working order is claimed too: not adopted twice",
      r1 == (["W"], 0, None) and r2 == ([], 0, None), (r1, r2))
BT.reset_app_orders()
r = BT.blind_stop_orders(Ex([o("F", 195)], boom=True), "X", True, qtys=[195], want_qty=195)
check(f"{TC} book unreadable → exactly ([], 0, None)", r == ([], 0, None), r)

# every filter skips ONE row and goes on (a rejected row before a good one)
ok = o("F", 195)
for label, first, kw in (
        ("too early", o("E", 195, ts=T0 - 60), dict(not_before_ts=T0)),
        ("working out of the band", o("W", 195, st="OPEN", px=300.0), {}),
        ("filled above the band", o("H", 195, px=300.0), {}),
        ("filled with no price (no band)", o("P", 195, px=0), dict(stop_px=None))):
    r = bso([first, ok], **kw)
    check(f"{TC} {label} first, then a good fill: the good one still counted (continue, not break)",
          r == ([], 195, 141.0), r)
r = bso([o("A", 130), o("B", 130), o("C", 65)], qtys=[130, 65], want_qty=195)
check(f"{TC} a fill that would pass the leg is skipped and a later one that fits is counted: 195",
      r == ([], 195, 141.0), r)
r = bso([o("F", 195, filled=130)])
check(f"{TC} a COMPLETE order counts its filled_quantity (130), not its quantity (195)",
      r == ([], 130, 141.0), r)
r = bso([o("F", 195), o("W", 195, st="OPEN", px=150.0)])
check(f"{TC} once the fills make the leg, a working match is still adopted", r == (["W"], 195, 141.0), r)
r = bso([o("W", 195, st="OPEN", px=0.5)])
check(f"{TC} a working buy at 0.5 is price-checked against the band (126..175): not taken",
      r == ([], 0, None), r)
r = bso([o("F", 1, px=141.0)], qtys=[1], want_qty=1)
check(f"{TC} a 1-lot fill of quantity 1 is counted", r == ([], 1, 141.0), r)
r = bso([o("F", 195, px=0.8)], stop_px=None)
check(f"{TC} a fill at 0.8 (no band) is counted", r == ([], 195, 0.8), r)
nq = o("N", 1)
del nq["quantity"]
r = bso([nq], qtys=[1], want_qty=1)
check(f"{TC} a row with no quantity never matches (even a 1-lot leg)", r == ([], 0, None), r)
r = bso([o("", 195, st="OPEN", px=150.0)])
check(f"{TC} a working row without an order id is not adopted", r == ([], 0, None), r)

# ── 0235 foreign_exit_fill ────────────────────────────────────────────────
TC = "TC-EXEC-0235"


def fef(book, short=True, app=(), **kw):
    BT.reset_app_orders()
    for a in app:
        BT.note_app_order(a)
    ex = Ex(book)
    return ex, BT.foreign_exit_fill(ex, "X", short, **kw)


_, r = fef([o("A", 65, px=141.0), o("B", 130, px=142.0)])
check(f"{TC} want_qty 0: every foreign fill, vwap 141.6667", r == (195, 141.6667), r)
_, r = fef([o("A", 195, px=141.0)], want_qty=65)
check(f"{TC} want 65 of a 195 fill: 65 at its price", r == (65, 141.0), r)
_, r = fef([o("A", 65, px=140.0), o("B", 65, px=142.0), o("C", 65, px=150.0)], want_qty=130)
check(f"{TC} earliest first, up to want: 130 at 141.0", r == (130, 141.0), r)
r2 = BT.foreign_exit_fill(Ex([o("A", 65, px=140.0), o("B", 65, px=142.0), o("C", 65, px=150.0)]),
                          "X", True)
check(f"{TC} …and stops once full: C was not claimed, a later read still finds it",
      r2 == (65, 150.0), r2)
_, r = fef([o("A", 65, px=140.0), o("B", 130, px=142.0)], want_qty=100)
check(f"{TC} the last order is sliced to the rest: 65 + 35 = 100", r == (100, 140.7), r)
_, r = fef([o("A", 195, st="OPEN")])
check(f"{TC} a working order is no fill", r == (0, None), r)
_, r = fef([o("A", 195, px=0)])
check(f"{TC} no average price: not counted", r == (0, None), r)
_, r = fef([o("A", 0, filled=0)])
check(f"{TC} no quantity: not counted", r == (0, None), r)
_, r = fef([o("A", 195, filled=0)])
check(f"{TC} filled_quantity 0 falls back to quantity", r == (195, 141.0), r)
_, r = fef([o("A", 195, side="SELL")])
check(f"{TC} short: a SELL is the entry side", r == (0, None), r)
_, r = fef([o("A", 195, side="SELL", px=19.0)], short=False)
check(f"{TC} long: the SELL is the exit", r == (195, 19.0), r)
_, r = fef([o("A", 195, sym="Y")])
check(f"{TC} another contract is not counted", r == (0, None), r)
_, r = fef([o("A", 195)], app=["A"])
check(f"{TC} this app's own order is not counted", r == (0, None), r)
_, r = fef([o("A", 195, st="complete", side="buy")])
check(f"{TC} lower-case status / side read", r == (195, 141.0), r)
for dt, q in ((-5, 195), (-6, 0)):
    _, r = fef([o("A", 195, ts=T0 + dt)], not_before_ts=T0)
    check(f"{TC} a fill {dt:+d} s from the leg's open: {q or 'not'} counted", r[0] == q, r)
bad = o("Z", 195)
bad["average_price"] = "abc"
_, r = fef([bad, o("A", 195)])
check(f"{TC} a malformed row is skipped, the next counted", r == (195, 141.0), r)
BT.reset_app_orders()
r = BT.foreign_exit_fill(Ex([o("A", 195)], boom=True), "X", True)
check(f"{TC} book unreadable → (0, None)", r == (0, None), r)
BT.reset_app_orders()
ex = Ex([o("A", 195)])
r1, r2 = BT.foreign_exit_fill(ex, "X", True), BT.foreign_exit_fill(ex, "X", True)
check(f"{TC} a counted fill is claimed: never counted for a second strategy",
      r1 == (195, 141.0) and r2 == (0, None), (r1, r2))

_, r = fef([o("A", 195, filled=130)])
check(f"{TC} a fill counts its filled_quantity (130), not its quantity", r == (130, 141.0), r)
_, r = fef([o("A", 65, px=140.0), o("B", 65, px=142.0)], want_qty=66)
check(f"{TC} the last 1 of the leg is still taken from the next fill: 66", r[0] == 66, r)
_, r = fef([o("A", 1, px=141.0)])
check(f"{TC} a fill of quantity 1 is counted", r == (1, 141.0), r)
_, r = fef([o("A", 195, px=0.8)])
check(f"{TC} a fill at 0.8 is counted", r == (195, 0.8), r)
_, r = fef([o("P", 195, px=0), o("A", 195)])
check(f"{TC} a row with no price first, then a good fill: still counted (continue, not break)",
      r == (195, 141.0), r)
_, r = fef([o("E", 195, ts=T0 - 60), o("A", 195)], not_before_ts=T0)
check(f"{TC} a too-early fill first, then a good one: still counted", r == (195, 141.0), r)
ex, r = fef([o("Z", 0, filled=0)])
check(f"{TC} a zero-quantity row is neither counted nor claimed", r == (0, None) and BT.app_order_ids() == set(),
      BT.app_order_ids())
ex, r = fef([o("", 195)])
check(f"{TC} a fill without an order id is counted but nothing bogus is claimed",
      r == (195, 141.0) and BT.app_order_ids() == set(), BT.app_order_ids())

# ── 0236 fired_gtt_orders ─────────────────────────────────────────────────
TC = "TC-EXEC-0236"


def g(gid, *oids, nested=True):
    return {"id": gid, "orders": [{"result": ({"order_result": {"order_id": x}} if nested
                                              else {"order_id": x})} for x in oids]}


BT.reset_app_orders()
check(f"{TC} GTT list unreadable → None", BT.fired_gtt_orders(Ex(gtts=None), "G1") is None)
check(f"{TC} GTT not listed → None", BT.fired_gtt_orders(Ex(gtts=[g("G2", "O1")]), "G1") is None)
r = BT.fired_gtt_orders(Ex(gtts=[g(1, "O1"), g("G2", "O2")]), "1")
check(f"{TC} ids compared as text: GTT 1 == '1' → ['O1']", r == ["O1"], r)
r = BT.fired_gtt_orders(Ex(gtts=[g("G1", "O1", nested=False)]), "G1")
check(f"{TC} result.order_id (no order_result) is read too", r == ["O1"], r)
r = BT.fired_gtt_orders(Ex(gtts=[{"id": "G1", "orders": [{"result": None}, {}]}]), "G1")
check(f"{TC} listed, no order named, no symbol → None", r is None, r)
book = [o("R1", 195, st="OPEN", px=150.0), o("R2", 65, st="OPEN"), o("C1", 195),
        o("S1", 195, st="OPEN", side="SELL"), o("Y1", 195, st="OPEN", sym="Y"),
        o("A1", 195, st="OPEN"), o("L1", 195, st="lapsed"), o("E1", 195, st="")]
BT.reset_app_orders()
BT.note_app_order("A1")
r = BT.fired_gtt_orders(Ex(book, gtts=[g("G1")]), "G1", symbol="X", short=True, qtys=[195])
check(f"{TC} not named yet: the WORKING non-app exit-side order of the leg's quantity is found",
      r == ["R1"], r)
r = BT.fired_gtt_orders(Ex(book, gtts=[g("G1")]), "G1", symbol="X", short=True, qtys=None)
check(f"{TC} …with no quantities given any quantity counts", r == ["R1", "R2"], r)
r = BT.fired_gtt_orders(Ex([o("S9", 130, st="OPEN", side="SELL")], gtts=[g("G1")]), "G1",
                        symbol="X", short=False, qtys=[130])
check(f"{TC} long leg: the working SELL is found", r == ["S9"], r)
r = BT.fired_gtt_orders(Ex(book, gtts=[g("G1")]), "G1", symbol="X", short=None, qtys=[195])
check(f"{TC} short unknown: no book search", r is None, r)
r = BT.fired_gtt_orders(Ex(book, gtts=[g("G1", "O7")]), "G1", symbol="X", short=True, qtys=[195])
check(f"{TC} a named order wins: no book search", r == ["O7"], r)
r = BT.fired_gtt_orders(Ex(book, boom=True, gtts=[g("G1")]), "G1", symbol="X", short=True,
                        qtys=[195])
check(f"{TC} book unreadable → None", r is None, r)

# ── 0237 app orders ───────────────────────────────────────────────────────
TC = "TC-EXEC-0237"
BT.reset_app_orders()
BT.note_app_order(("T1", "extra"))
BT.note_app_order(["L1"])
BT.note_app_order(42)
BT.note_app_order(None)
BT.note_app_order("")
BT.claim_orders(" C1 , C2,,")
BT.claim_orders(["K1", "", None])
BT.claim_orders(None)
ids = BT.app_order_ids()
check(f"{TC} tuple/list → first id, numbers as text, blanks ignored, comma form split + stripped",
      ids == {"T1", "L1", "42", "C1", "C2", "K1"}, ids)
ids.add("NOPE")
check(f"{TC} app_order_ids() is a copy", "NOPE" not in BT.app_order_ids())
BT.reset_app_orders()
check(f"{TC} reset clears", BT.app_order_ids() == set(), BT.app_order_ids())

# ── 0238 exit intents ─────────────────────────────────────────────────────
TC = "TC-EXEC-0238"
BT.INTENT_DIR = TMP / "intent"
BT._INTENTS.clear()
f = BT._intent_file("S1")
check(f"{TC} the note file is <INTENT_DIR>/<sid>_exit_intent.json", f == TMP / "intent" / "S1_exit_intent.json", f)
tag = BT.exit_intent_note("S1", "g1:short", "X", "BUY", "195", "SL", now=T0 + 0.5, run="run-A")
rec = json.loads(f.read_text())["g1:short"]
today = datetime.now(IST).date().isoformat()
check(f"{TC} a note returns the tag and writes every field (qty as int)",
      tag and rec == {"tag": tag, "symbol": "X", "txn": "BUY", "qty": 195, "reason": "SL",
                      "ts": T0 + 0.5, "run": "run-A", "day": today}, rec)
check(f"{TC} no .tmp left behind", not f.with_suffix(".tmp").exists())
BT._INTENTS.clear()                           # a restart: read back from disk
got = BT.exit_intents("S1")
check(f"{TC} read back after a restart", got.get("g1:short", {}).get("tag") == tag, got)
d = json.loads(f.read_text())
d["old"] = dict(d["g1:short"], day="2026-01-02")
f.write_text(json.dumps(d))
BT._INTENTS.clear()
got = BT.exit_intents("S1")
check(f"{TC} only today's notes are listed", set(got) == {"g1:short"}, got)
got["g1:short"]["tag"] = "CHANGED"
check(f"{TC} exit_intents() hands out copies", BT.exit_intents("S1")["g1:short"]["tag"] == tag)
BT.exit_intent_done("S1", "g1:short")
BT._INTENTS.clear()
check(f"{TC} done removes the note on disk", "g1:short" not in json.loads(f.read_text()))
BT.exit_intent_done("S1", "never-noted")
check(f"{TC} done of an unknown key changes nothing", "old" in json.loads(f.read_text()))
bad = TMP / "intent" / "S2_exit_intent.json"
bad.write_text("{not json")
BT._INTENTS.clear()
check(f"{TC} an unreadable note file reads as no notes", BT.exit_intents("S2") == {})
BT.INTENT_DIR = None
p = BT._intent_file("S3")
check(f"{TC} test mode never uses ~/.scalp-app: a temp folder per process",
      ".scalp-app" not in str(p) and f"scalp_intent_{os.getpid()}" in str(p), p)
BT.INTENT_DIR = TMP / "intent"
check(f"{TC} find_tagged: found → (row, True)",
      BT.find_tagged(Ex([{"order_id": "O1", "tag": "T9"}]), "T9") == ({"order_id": "O1", "tag": "T9"}, True))
check(f"{TC} find_tagged: readable, not there → (None, True)",
      BT.find_tagged(Ex([{"order_id": "O1", "tag": "T8"}]), "T9") == (None, True))
check(f"{TC} find_tagged: book raises → (None, False)", BT.find_tagged(Ex(boom=True), "T9") == (None, False))


class NoneBook(Ex):
    def get_orders(self):
        return None


check(f"{TC} find_tagged: book None → (None, False)", BT.find_tagged(NoneBook(), "T9") == (None, False))

# ── 0239 send_exit_once ───────────────────────────────────────────────────
TC = "TC-EXEC-0239"


class Place:
    def __init__(self, ret="NEW1"):
        self.n, self.tags, self.ret = 0, [], ret

    def __call__(self):
        self.n += 1
        self.tags.append(getattr(IG._HINT, "tag", None))
        return self.ret


def fresh(sid):
    BT._INTENTS.pop(sid, None)
    try:
        BT._intent_file(sid).unlink()
    except FileNotFoundError:
        pass
    BT.reset_app_orders()


def note_from_earlier_run(sid, key, ts):
    t = BT.exit_intent_note(sid, key, "X", "BUY", 195, "SL", now=ts, run="run-OLD")
    return t


def seo(sid, ex, pl, logs, net_based=False, now=T0 + 120, key="k"):
    return BT.send_exit_once(sid, key, ex, symbol="X", txn="BUY", qty=195, reason="SL", place=pl,
                             run="run-NEW", now=now, log=logs.append, net_based=net_based)


fresh("E1")
pl, logs = Place(), []
r = seo("E1", Ex([]), pl, logs)
rec = BT.exit_intents("E1").get("k") or {}
check(f"{TC} no earlier note: a note is written and the order placed under its tag",
      r == "NEW1" and pl.n == 1 and pl.tags[0] == rec.get("tag") and rec.get("run") == "run-NEW"
      and rec.get("ts") == T0 + 120, (r, pl.tags, rec))
check(f"{TC} …and the tag is cleared after the place", getattr(IG._HINT, "tag", None) is None)
pl2 = Place("NEW2")
r = seo("E1", Ex(boom=True), pl2, logs)
check(f"{TC} a note from THIS run is not checked: placed again (book never read)",
      r == "NEW2" and pl2.n == 1, r)

fresh("E2")
t = note_from_earlier_run("E2", "k", T0)
pl, logs = Place(), []
ex = Ex([{"order_id": "O5", "tag": t, "status": "OPEN"}])
r = seo("E2", ex, pl, logs)
BT._INTENTS.clear()
check(f"{TC} earlier run, its order in the book → that id is driven, nothing sent",
      r == "O5" and pl.n == 0, (r, pl.n))
check(f"{TC} …this run owns the note now (on disk)", BT.exit_intents("E2")["k"]["run"] == "run-NEW",
      BT.exit_intents("E2"))
check(f"{TC} …the order is claimed", "O5" in BT.app_order_ids())
check(f"{TC} …and logged as INTENT_FOUND with key, symbol, id and status",
      logs == ["[E2][EXIT][INTENT_FOUND] k X: order O5 (OPEN) was sent before the restart — "
               "driving it, not sending another"], logs)

for st in ("COMPLETE", "CANCELLED", "REJECTED", "LAPSED", "complete"):
    fresh("E3")
    t = note_from_earlier_run("E3", "k", T0 + 119)          # 1 s old: would be PENDING if not settled
    pl, logs = Place(), []
    r = seo("E3", Ex([{"order_id": "O6", "tag": t, "status": st}]), pl, logs, net_based=True)
    check(f"{TC} net-based, earlier order {st}: already in the position → a NEW exit is placed",
          r == "NEW1" and pl.n == 1 and "O6" not in BT.app_order_ids(), (r, pl.n))
    check(f"{TC} …logged INTENT_SETTLED ({st})",
          logs[:1] == [f"[E3][EXIT][INTENT_SETTLED] k X: order O6 ({st.upper()}) from before the "
                       f"restart is already in the position"], logs)
fresh("E4")
t = note_from_earlier_run("E4", "k", T0)
pl, logs = Place(), []
r = seo("E4", Ex([{"order_id": "O7", "tag": t, "status": "OPEN"}]), pl, logs, net_based=True)
check(f"{TC} net-based but the earlier order still OPEN → adopted, not placed", r == "O7" and pl.n == 0, r)
fresh("E5")
t = note_from_earlier_run("E5", "k", T0)
pl, logs = Place(), []
r = seo("E5", Ex([{"order_id": "O8", "tag": t, "status": "COMPLETE"}]), pl, logs, net_based=False)
check(f"{TC} not net-based: an earlier COMPLETE order is adopted (its fill is the exit)",
      r == "O8" and pl.n == 0, r)

fresh("E6")
note_from_earlier_run("E6", "k", T0)
pl, logs = Place(), []
try:
    seo("E6", Ex(boom=True), pl, logs)
    raised = False
except BT.ExitCheckPending:
    raised = True
check(f"{TC} earlier note, book unreadable → ExitCheckPending, nothing sent",
      raised and pl.n == 0, (raised, pl.n))
check(f"{TC} …logged INTENT_PENDING",
      logs == ["[E6][EXIT][INTENT_PENDING] k X: an exit sent before the restart cannot be checked "
               "yet — nothing sent"], logs)
for age, pending in ((59.9, True), (60.0, False), (61.0, False)):
    fresh("E7")
    note_from_earlier_run("E7", "k", T0)
    pl, logs = Place(), []
    try:
        r = seo("E7", Ex([]), pl, logs, now=T0 + age)
        raised = False
    except BT.ExitCheckPending:
        raised = True
    check(f"{TC} earlier note {age} s old, its order not in the book: "
          f"{'pending' if pending else 'placed again'} (60 s edge)",
          raised == pending and pl.n == (0 if pending else 1), (raised, pl.n))
fresh("E8")
BT.INTENT_CLOCK = lambda: T0 + 500.0
pl, logs = Place(), []
BT.send_exit_once("E8", "k", Ex([]), symbol="X", txn="BUY", qty=195, reason="SL", place=pl, run="R")
BT.INTENT_CLOCK = None
check(f"{TC} INTENT_CLOCK is the clock when now is not given",
      BT.exit_intents("E8")["k"]["ts"] == T0 + 500.0, BT.exit_intents("E8"))

# ── 0240 GttKillList: the list itself ─────────────────────────────────────
TC = "TC-EXEC-0240"
kp = TMP / "kill" / "S1_gtt_kill.json"
kl = BT.GttKillList("S1", path=kp)
check(f"{TC} a new list is empty", len(kl) == 0 and kl.ids() == [])
kl.add(123, "X", "w" * 200)
kl.add("123", "Y", "again")
kl.add("", "Z")
kl.add(None, "Z")
kl.add("G2", "X2", "own stop", spent=True)
it = json.loads(kp.read_text())
check(f"{TC} ids as text, a repeat and a blank id ignored", kl.ids() == ["123", "G2"] and len(kl) == 2,
      kl.ids())
check(f"{TC} each entry: symbol, why cut to 120, since, paged 0, tries 0, spent",
      it["123"]["symbol"] == "X" and it["123"]["why"] == "w" * 120 and it["123"]["paged"] == 0.0
      and it["123"]["tries"] == 0 and it["123"]["spent"] is False and it["G2"]["spent"] is True
      and abs(it["123"]["since"] - time.time()) < 60, it)
check(f"{TC} saved atomically: no .tmp left", not kp.with_suffix(".tmp").exists())
kl2 = BT.GttKillList("S1", path=kp)
check(f"{TC} a restart loads the list", kl2.ids() == ["123", "G2"], kl2.ids())
kp.write_text("[broken")
check(f"{TC} an unreadable file loads as empty", len(BT.GttKillList("S1", path=kp)) == 0)
d1 = BT._kill_dir()
check(f"{TC} test mode: the default folder is a temp one, the same each time, never ~/.scalp-app",
      d1 == BT._kill_dir() and ".scalp-app" not in str(d1), d1)
check(f"{TC} default file is <kill dir>/<sid>_gtt_kill.json",
      BT.GttKillList("S9")._file() == d1 / "S9_gtt_kill.json")


# ── 0241 / 0242 GttKillList.retry ─────────────────────────────────────────
class Clk:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


_real_time = time.time


def kill(sid, items, ex, now, force=True, **kw):
    path = TMP / "kill" / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items))
    k = BT.GttKillList(sid, path=path)
    logs, pages = [], []
    time.time = Clk(now)
    try:
        n = k.retry(ex, log=logs.append, page=lambda m, s: pages.append((s, m)), force=force, **kw)
    finally:
        time.time = _real_time
    return k, n, logs, pages, json.loads(path.read_text())


def item(sym="X", paged=0.0, tries=0, spent=False):
    return {"symbol": sym, "why": "", "since": T0, "paged": paged, "tries": tries, "spent": spent}


TC = "TC-EXEC-0241"
k, n, logs, pages, disk = kill("R1", {"G1": item()}, Ex(cancel_ok=True), T0)
check(f"{TC} verified cancel → dropped, saved, 0 left", n == 0 and disk == {} and k.ids() == [], (n, disk))
check(f"{TC} …asked with retries=0, logged 'confirmed cancelled', no page (never paged)",
      logs == ["[GTT_KILL][R1] gtt=G1 on X confirmed cancelled"] and pages == [], (logs, pages))
k, n, logs, pages, disk = kill("R1b", {"G1": item(paged=T0 - 30)}, Ex(cancel_ok=True), T0)
check(f"{TC} cancelled after a page → a warning that it is now CANCELLED",
      pages == [("warning", "R1b: GTT G1 on X is now CANCELLED (it had stayed armed after the "
                            "position closed).")], pages)
k, n, logs, pages, disk = kill("R2", {"G1": item()}, Ex(cancel_ok=False), T0)
check(f"{TC} cancel not verified → kept, tries 1, paged now, saved",
      n == 1 and disk["G1"]["tries"] == 1 and disk["G1"]["paged"] == T0, disk)
check(f"{TC} …the STILL ARMED log and error page",
      logs == ["[GTT_KILL][R2] gtt=G1 on X STILL ARMED on a closed leg"]
      and pages == [("error", "R2: GTT G1 on X is STILL ARMED although its position is closed, "
                              "and could not be cancelled. DELETE IT IN KITE (GTT tab) now. The "
                              "app keeps retrying.")], (logs, pages))
for ago, repaged in ((599.0, False), (600.0, True)):
    k, n, logs, pages, disk = kill("R3", {"G1": item(paged=T0 - ago, tries=3)}, Ex(cancel_ok=False), T0)
    check(f"{TC} still armed, paged {ago:.0f} s ago: {'paged again' if repaged else 'not paged'} "
          f"(10-min repage), tries 4",
          (len(pages) == 1) == repaged and disk["G1"]["tries"] == 4
          and disk["G1"]["paged"] == (T0 if repaged else T0 - ago), (pages, disk))


class RaisingCancel(Ex):
    def cancel_gtt_verified(self, gid, retries=0):
        raise RuntimeError("503")


k, n, logs, pages, disk = kill("R4", {"G1": item()}, RaisingCancel(), T0)
check(f"{TC} a cancel that raises is logged and counted as a try",
      logs[0] == "[GTT_KILL][R4] gtt=G1 cancel raised RuntimeError('503')" and disk["G1"]["tries"] == 1,
      (logs, disk))


class OldCancel(Ex):
    def cancel_gtt_verified(self, gid):           # no retries= keyword
        return True


k, n, logs, pages, disk = kill("R5", {"G1": item()}, OldCancel(), T0)
check(f"{TC} an executor whose cancel takes no retries= still cancels", n == 0, (n, logs))


class NoCancel:
    pass


k, n, logs, pages, disk = kill("R6", {"G1": item()}, NoCancel(), T0)
check(f"{TC} no cancel method → kept with a try", n == 1 and disk["G1"]["tries"] == 1, disk)
k = BT.GttKillList("R7", path=TMP / "kill" / "R7.json")
k.add("G1", "X")
ex = Ex(cancel_ok=False)
time.time = Clk(T0)
k.retry(ex, force=True)
time.time = Clk(T0 + 24.9)
k.retry(ex)
n_after_gap = len(ex.cancel_gtt)
time.time = Clk(T0 + 25.0)
k.retry(ex)
time.time = _real_time
check(f"{TC} retries at most every 25 s unless forced (24.9 s: none; 25 s: one)",
      n_after_gap == 1 and len(ex.cancel_gtt) == 2, ex.cancel_gtt)
check(f"{TC} empty list or no executor: nothing asked, count returned",
      BT.GttKillList("R8", path=TMP / "kill" / "R8.json").retry(Ex(), force=True) == 0
      and k.retry(None, force=True) == 1)


def boom_log(m):
    raise RuntimeError("log down")


k = BT.GttKillList("R9", path=TMP / "kill" / "R9.json")
k.add("G1", "X")
check(f"{TC} a log / page that raises never breaks retry",
      k.retry(Ex(cancel_ok=False), log=boom_log, page=lambda m, s: 1 / 0, force=True) == 1)

TC = "TC-EXEC-0242"
gt = [g("G1", "O1")]
k, n, logs, pages, disk = kill("F1", {"G1": item()},
                               Ex(gtts=gt, status={"G1": "triggered"},
                                  fills={"O1": {"status": "COMPLETE", "filled_qty": 195}}), T0)
check(f"{TC} fired and filled 195 → dropped, 'FIRED' log, error page naming the fill",
      n == 0 and disk == {} and logs == ["[GTT_KILL][F1] gtt=G1 on X FIRED before it could be "
                                         "cancelled (filled 195)"]
      and pages == [("error", "F1: GTT G1 on X — left armed after its position was closed — has "
                              "TRIGGERED and filled 195. Check Kite Orders and Positions for an "
                              "unintended trade on X NOW.")], (logs, pages))
ex = Ex(gtts=gt, status={"G1": "triggered"}, fills={"O1": {"status": "OPEN", "filled_qty": 0}},
        cancel_moves={"O1": "CANCELLED"})
k, n, logs, pages, disk = kill("F2", {"G1": item()}, ex, T0)
check(f"{TC} fired, order resting → cancelled, filled 0 → dropped with a 'no trade' warning",
      n == 0 and ex.cancelled == ["O1"]
      and pages == [("warning", "F2: GTT G1 on X — left armed after its position was closed — "
                                "TRIGGERED, but its order did not fill and is now cancelled. No "
                                "trade resulted; verify in Kite.")], (ex.cancelled, pages))
ex = Ex(gtts=gt, status={"G1": "triggered"}, fills={"O1": {"status": "OPEN", "filled_qty": 0}},
        cancel_boom=True)
k, n, logs, pages, disk = kill("F3", {"G1": item()}, ex, T0)
check(f"{TC} fired, its order cannot be cancelled → kept, paged to cancel in Kite",
      n == 1 and disk["G1"]["paged"] == T0
      and pages == [("error", "F3: GTT G1 on X — left armed after its position was closed — has "
                              "TRIGGERED and its order O1 is still OPEN and could not be "
                              "cancelled. CANCEL IT IN KITE (Orders → Open) now.")]
      and logs == ["[GTT_KILL] cancel of fired GTT order O1 raised RuntimeError('cancel: 500')"],
      (pages, logs, disk))
for ago, repaged in ((599.0, False), (600.0, True)):
    k, n, logs, pages, disk = kill("F4", {"G1": item(paged=T0 - ago)}, ex, T0)
    check(f"{TC} still resting, paged {ago:.0f} s ago: {'paged again' if repaged else 'quiet'}",
          (len(pages) == 1) == repaged and n == 1, pages)
k, n, logs, pages, disk = kill("F5", {"G1": item()},
                               Ex(gtts=[g("G1")], status={"G1": "triggered"}), T0)
check(f"{TC} fired, no order listed → fill unknown: dropped, 'filled unknown', error page",
      n == 0 and logs == ["[GTT_KILL][F5] gtt=G1 on X FIRED before it could be cancelled (filled "
                          "unknown)"]
      and pages == [("error", "F5: GTT G1 on X — left armed after its position was closed — has "
                              "TRIGGERED. Check Kite Orders and Positions for an unintended trade "
                              "on X NOW.")], (logs, pages))
# _settle_fired directly
S = BT.GttKillList._settle_fired
nolog = [].append
check(f"{TC} settle: GTT list unreadable → ([], None)", S(Ex(gtts=None), "G1", nolog) == ([], None))
r = S(Ex(gtts=[g("G1", "A", "B")], fills={"A": {"status": "COMPLETE", "filled_qty": 65},
                                         "B": {"status": "CANCELLED", "filled_qty": 0}}), "G1", nolog)
check(f"{TC} settle: done orders → nothing resting, fills summed", r == ([], 65), r)
r = S(Ex(gtts=[g("G1", "A", "B")], fills={"A": {"status": "COMPLETE", "filled_qty": 65},
                                         "B": {"status": "OPEN"}}), "G1", nolog)
check(f"{TC} settle: one order still open after the cancel → it is resting", r == (["B"], 65), r)
r = S(Ex(gtts=[g("G1", "A")], fills={}), "G1", nolog)
check(f"{TC} settle: an order whose status cannot be read is assumed resting", r == (["A"], None), r)
for st in ("REJECTED", "LAPSED", "complete"):
    ex = Ex(gtts=[g("G1", "A")], fills={"A": {"status": st, "filled_qty": 0}})
    r = S(ex, "G1", nolog)
    check(f"{TC} settle: {st} is done, not cancelled again", r == ([], 0) and ex.cancelled == [], (r, ex.cancelled))


class FillBoom(Ex):
    def get_order_fill(self, oid):
        raise RuntimeError("x")


check(f"{TC} settle: a fill read that raises → assumed resting",
      S(FillBoom(gtts=[g("G1", "A")]), "G1", nolog) == (["A"], None))
# a spent stop (the leg's own fired stop)
ex = Ex(gtts=[g("G1", "O1")], fills={"O1": {"status": "COMPLETE", "filled_qty": 195}})
k, n, logs, pages, disk = kill("P1", {"G1": item(spent=True)}, ex, T0)
check(f"{TC} spent stop filled normally → dropped silently", n == 0 and logs == [] and pages == [],
      (logs, pages))
ex = Ex(gtts=[g("G1", "O1")], fills={"O1": {"status": "OPEN"}}, cancel_moves={"O1": "CANCELLED"})
k, n, logs, pages, disk = kill("P2", {"G1": item(spent=True)}, ex, T0)
check(f"{TC} spent stop with a resting order → cancelled, dropped, logged and a warning page",
      n == 0 and ex.cancelled == ["O1"]
      and logs == ["[GTT_KILL][P2] gtt=G1 on X: resting order(s) ['O1'] of the fired stop cancelled "
                   "after the close"]
      and pages == [("warning", "P2: a resting order of the fired stop GTT on X was still open after "
                                "the position closed — it is now CANCELLED. Verify in Kite.")],
      (logs, pages))
ex = Ex(gtts=[g("G1", "O1")], fills={"O1": {"status": "OPEN"}}, cancel_boom=True)
k, n, logs, pages, disk = kill("P3", {"G1": item(spent=True)}, ex, T0)
check(f"{TC} spent stop, resting order cannot be cancelled → kept, paged",
      n == 1 and disk["G1"]["paged"] == T0
      and pages == [("error", "P3: the stop GTT G1 on X triggered but its order O1 is still OPEN after "
                              "the position was closed, and could not be cancelled. CANCEL IT IN KITE "
                              "(Orders → Open) now.")], pages)
k, n, logs, pages, disk = kill("P4", {"G1": item(spent=True, paged=T0 - 599)}, ex, T0)
check(f"{TC} …not paged again within 10 minutes", pages == [] and n == 1, pages)
k, n, logs, pages, disk = kill("P5", {"G1": item(spent=True, tries=18)}, Ex(gtts=[g("G1")]), T0)
check(f"{TC} spent stop that never listed its order: kept while tries < 20 (19)",
      n == 1 and disk["G1"]["tries"] == 19, disk)
k, n, logs, pages, disk = kill("P6", {"G1": item(spent=True, tries=19)}, Ex(gtts=[g("G1")]), T0)
check(f"{TC} …dropped at the 20th try, logged",
      n == 0 and logs == ["[GTT_KILL][P6] gtt=G1 on X: fired stop never listed its order — dropped"],
      logs)
k, n, logs, pages, disk = kill("P7", {"G1": item(spent=True)}, Ex(gtts=None), T0)
check(f"{TC} spent stop, GTT list unreadable → kept with a try", n == 1 and disk["G1"]["tries"] == 1, disk)

# ── 0243 live paths, defaults, several GTTs in one pass ──────────────────
TC = "TC-EXEC-0243"
_env_home, _env_tm = os.environ.get("HOME"), os.environ.pop("SCALP_TEST_MODE", None)
LIVE = TMP / "livehome"
os.environ["HOME"] = str(LIVE)
try:
    day, f = BT._app_orders_file()
    check(f"{TC} live: the app-order day file is ~/.scalp-app/state/app_orders_<YYYYMMDD>.txt",
          day == time.strftime("%Y%m%d") and len(day) == 8
          and f == LIVE / ".scalp-app" / "state" / f"app_orders_{day}.txt", (day, f))
    BT._INTENTS.clear()
    _idir = BT.INTENT_DIR
    BT.INTENT_DIR = None
    check(f"{TC} live: intent notes are ~/.scalp-app/state/<sid>_exit_intent.json",
          BT._intent_file("L1") == LIVE / ".scalp-app" / "state" / "L1_exit_intent.json", BT._intent_file("L1"))
    BT.INTENT_DIR = _idir
    _kt = BT.__dict__.pop("_KILL_TMP", None)
    check(f"{TC} live: the kill list lives in ~/.scalp-app/state",
          BT._kill_dir() == LIVE / ".scalp-app" / "state", BT._kill_dir())
    if _kt is not None:
        BT._KILL_TMP = _kt
    # the day file: a restart (a new day marker) reloads today's ids from it
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("D1\n\n  D2  \n")
    BT._APP_ORDERS_DAY[0] = "19990101"
    ids = BT.app_order_ids()
    check(f"{TC} live: after a restart today's ids come back from the day file (blank lines skipped)",
          ids == {"D1", "D2"}, ids)
    BT.note_app_order("D3")
    t = BT._APP_ORDERS_WRITER[0]
    for _ in range(100):
        if "D3" in f.read_text():
            break
        time.sleep(0.05)
    check(f"{TC} live: a new app order is appended to the day file (background writer)",
          f.read_text().splitlines()[-1] == "D3" and t is not None, f.read_text())
    f.write_text("D9\n")
    check(f"{TC} live: the same day is not reloaded (memory wins until the day changes)",
          BT.app_order_ids() == {"D1", "D2", "D3"}, BT.app_order_ids())
    f.unlink()
    BT._APP_ORDERS_DAY[0] = "19990101"
    check(f"{TC} live: a new day with no file starts empty", BT.app_order_ids() == set(), BT.app_order_ids())
finally:
    os.environ["HOME"] = _env_home or ""
    if _env_tm is not None:
        os.environ["SCALP_TEST_MODE"] = _env_tm
    BT._APP_ORDERS_DAY[0] = ""
    BT.reset_app_orders()

# exit intents: nested folder, defaults, the real clock, the pending message
BT.INTENT_DIR = TMP / "deep" / "a" / "b"
BT._INTENTS.clear()
t1 = BT.exit_intent_note("N1", "k", "X", "BUY", 65)
rec = BT.exit_intents("N1").get("k") or {}
check(f"{TC} a note in a folder that does not exist yet (two levels) is written",
      (TMP / "deep" / "a" / "b" / "N1_exit_intent.json").exists(), rec)
check(f"{TC} note defaults: reason '' and run '', ts = now", rec.get("reason") == "" and rec.get("run") == ""
      and abs(rec.get("ts", 0) - time.time()) < 60 and t1 == rec.get("tag"), rec)
BT.INTENT_DIR = TMP / "intent"
fresh("E9")
t = note_from_earlier_run("E9", "k", T0)
pl = Place()
r = BT.send_exit_once("E9", "k", Ex([{"order_id": "O9", "tag": t, "status": "COMPLETE"}]), symbol="X",
                      txn="BUY", qty=195, reason="SL", place=pl, run="run-NEW", now=T0 + 120)
check(f"{TC} send_exit_once is not net-based by default: an earlier COMPLETE order is adopted",
      r == "O9" and pl.n == 0, r)
fresh("E10")
pl = Place()
BT.send_exit_once("E10", "k2", Ex([]), symbol="SYM", txn="SELL", qty="130", reason="TP", place=pl,
                  run="R", now=T0)
rec = BT.exit_intents("E10")["k2"]
check(f"{TC} the note send_exit_once writes holds symbol, txn, qty, reason and run",
      (rec["symbol"], rec["txn"], rec["qty"], rec["reason"], rec["run"]) == ("SYM", "SELL", 130, "TP", "R"), rec)
fresh("E11")
note_from_earlier_run("E11", "kk", T0)
try:
    BT.send_exit_once("E11", "kk", Ex(boom=True), symbol="X", txn="BUY", qty=1, reason="", place=Place(),
                      run="R2", now=T0 + 1)
    msg = ""
except BT.ExitCheckPending as e:
    msg = str(e)
check(f"{TC} ExitCheckPending names the key", msg == "exit for kk sent before the restart not checked yet", msg)

# fired_gtt_orders: book rows it must skip, and keep going
BT.reset_app_orders()
bad = o("Q", 195, st="OPEN")
bad["quantity"] = "abc"
book = [bad, o("R1", 195, st="REJECTED"), o("C1", 195, st="CANCELLED"), o("W1", 195, st="OPEN")]
r = BT.fired_gtt_orders(Ex(book, gtts=[g("G1")]), "G1", symbol="X", short=True, qtys=[195])
check(f"{TC} fired GTT book search skips a malformed row, REJECTED and CANCELLED, finds the working one",
      r == ["W1"], r)
nq = o("N", 1, st="OPEN")
del nq["quantity"]
r = BT.fired_gtt_orders(Ex([nq], gtts=[g("G1")]), "G1", symbol="X", short=True, qtys=[1])
check(f"{TC} a row with no quantity does not match a 1-lot leg", r is None, r)

# kill list: the default temp folder's name; nested save folder
check(f"{TC} test-mode kill folder is named gtt_kill_*", BT._kill_dir().name.startswith("gtt_kill_"),
      BT._kill_dir())
kn = BT.GttKillList("N1", path=TMP / "k1" / "k2" / "n1.json")
kn.add("G1", "X")
check(f"{TC} the kill list is saved into a folder that does not exist yet (two levels)",
      (TMP / "k1" / "k2" / "n1.json").exists())
# _settle_fired: flat order_id; CANCELLED is final (no cancel sent)
r = S(Ex(gtts=[g("G1", "A", nested=False)], fills={"A": {"status": "COMPLETE", "filled_qty": 5}}), "G1", nolog)
check(f"{TC} settle reads result.order_id (no order_result)", r == ([], 5), r)
ex = Ex(gtts=[g("G1", "A")], fills={"A": {"status": "CANCELLED", "filled_qty": 0}})
r = S(ex, "G1", nolog)
check(f"{TC} settle: CANCELLED is final, no cancel sent", r == ([], 0) and ex.cancelled == [], (r, ex.cancelled))
ex = Ex(gtts=[g("G1", "A")], fills={"A": {"filled_qty": 0}})
r = S(ex, "G1", nolog)
check(f"{TC} settle: an order with no status is assumed resting, unknown fill, no cancel",
      r == (["A"], None) and ex.cancelled == [], (r, ex.cancelled))

# retry: several GTTs in ONE pass — the first one's path must not stop the rest
two = {"G1": item(), "G2": item(sym="Y")}


class Mixed(Ex):
    def cancel_gtt_verified(self, gid, retries=0):
        self.cancel_gtt.append((str(gid), retries))
        return str(gid) == "G1"


ex = Mixed()
k, n, logs, pages, disk = kill("M1", two, ex, T0)
check(f"{TC} two GTTs: the first cancelled, the second still tried (and kept)",
      n == 1 and list(disk) == ["G2"] and ex.cancel_gtt == [("G1", 0), ("G2", 0)], (disk, ex.cancel_gtt))
ex = Mixed(status={"G1": "triggered"}, gtts=[g("G1", "O1")], fills={"O1": {"status": "COMPLETE", "filled_qty": 5}})
k, n, logs, pages, disk = kill("M2", two, ex, T0)
check(f"{TC} two GTTs: the first had fired, the second still tried", ex.cancel_gtt == [("G2", 0)] and n == 1,
      (ex.cancel_gtt, disk))
ex = Mixed(status={"G1": "triggered"}, gtts=[g("G1", "O1")], fills={"O1": {"status": "OPEN"}}, cancel_boom=True)
k, n, logs, pages, disk = kill("M3", two, ex, T0)
check(f"{TC} two GTTs: the first fired and rests, the second still tried", ex.cancel_gtt == [("G2", 0)], ex.cancel_gtt)
spent2 = {"G1": item(spent=True), "G2": item(sym="Y")}
for label, ex in (("filled", Mixed(gtts=[g("G1", "O1")], fills={"O1": {"status": "COMPLETE"}})),
                  ("resting", Mixed(gtts=[g("G1", "O1")], fills={"O1": {"status": "OPEN"}}, cancel_boom=True)),
                  ("unlisted", Mixed(gtts=[g("G1")]))):
    k, n, logs, pages, disk = kill("M4", spent2, ex, T0)
    check(f"{TC} two GTTs: the first a spent stop ({label}), the second still tried",
          ("G2", 0) in ex.cancel_gtt, ex.cancel_gtt)
ex = Ex(gtts=[g("G1", "O1", "O2")], status={"G1": "triggered"},
        fills={"O1": {"status": "OPEN"}, "O2": {"status": "OPEN"}}, cancel_boom=True)
k, n, logs, pages, disk = kill("J1", {"G1": item()}, ex, T0)
check(f"{TC} two resting orders are named 'O1, O2' in the page", "order O1, O2 is still OPEN" in pages[0][1], pages)
ex = Ex(gtts=[g("G1", "O1", "O2")], fills={"O1": {"status": "OPEN"}, "O2": {"status": "OPEN"}}, cancel_boom=True)
k, n, logs, pages, disk = kill("J2", {"G1": item(spent=True)}, ex, T0)
check(f"{TC} spent stop: two resting orders named 'O1, O2'", "order O1, O2 is still OPEN" in pages[0][1], pages)
check(f"{TC} spent stop: a failed cancel of its order is logged",
      logs[:1] == ["[GTT_KILL] cancel of fired GTT order O1 raised RuntimeError('cancel: 500')"], logs)
k, n, logs, pages, disk = kill("J3", {"G1": item(spent=True, paged=T0 - 600)}, ex, T0)
check(f"{TC} spent stop still resting, paged exactly 600 s ago: paged again", len(pages) == 1, pages)
for st in ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED", "rejected", "cancelled", "lapsed", None):
    fl = {"O1": {"status": st, "filled_qty": 0}} if st else {"O1": {"filled_qty": 0}}
    ex = Ex(gtts=[g("G1", "O1")], fills=fl)
    k, n, logs, pages, disk = kill("SP", {"G1": item(spent=True)}, ex, T0)
    check(f"{TC} spent stop whose order is {st or 'without a status'}: no 'resting order cancelled' page",
          not any("resting order" in m for _s, m in pages), pages)
k, n, logs, pages, disk = kill("SB", {"G1": item(spent=True)}, FillBoom(gtts=[g("G1", "O1")]), T0)
check(f"{TC} spent stop whose fill read raises: retry does not raise, the stop stays listed",
      n == 1 and disk["G1"]["tries"] == 1, disk)
ex = Ex(cancel_ok=False)
k, n, logs, pages, disk = kill("RC", {"G1": item()}, ex, T0)
check(f"{TC} the verified cancel is asked with retries=0 for that GTT", ex.cancel_gtt == [("G1", 0)], ex.cancel_gtt)


class SlowDefault(Ex):
    def cancel_gtt_verified(self, gid, retries=3):          # a broker whose own default retries
        self.cancel_gtt.append((str(gid), retries))
        return False


ex = SlowDefault()
kill("RC3", {"G1": item()}, ex, T0)
check(f"{TC} retries=0 is passed explicitly (a broker defaulting to 3 retries is not slowed)",
      ex.cancel_gtt == [("G1", 0)], ex.cancel_gtt)


class OldCancel2(Ex):
    def cancel_gtt_verified(self, gid):
        self.cancel_gtt.append(str(gid))
        return True


ex = OldCancel2()
kill("RC2", {"G1": item()}, ex, T0)
check(f"{TC} …and an old-style cancel is asked for that GTT", ex.cancel_gtt == ["G1"], ex.cancel_gtt)
k, n, logs, pages, disk = kill("R6b", {"G1": item()}, NoCancel(), T0)
check(f"{TC} no cancel method: no 'cancel raised' line, just STILL ARMED",
      logs == ["[GTT_KILL][R6b] gtt=G1 on X STILL ARMED on a closed leg"], logs)
nos = {"symbol": None}
nos = {k_: v for k_, v in item().items() if k_ != "symbol"}
k, n, logs, pages, disk = kill("NS", {"G1": nos}, Ex(cancel_ok=True), T0)
check(f"{TC} an entry with no symbol is logged with an empty symbol",
      logs == ["[GTT_KILL][NS] gtt=G1 on  confirmed cancelled"], logs)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL BROKER-TRUTH MUTATION-SAFETY CHECKS PASSED")
