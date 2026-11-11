# backend/app/execution/test_order_latency.py
#
# ── ORDER_LATENCY_20261111 ── the Zerodha executor no longer downloads the
# whole NFO instrument list before an order on a symbol it has not traded
# yet. Replays the 11-Nov 09:20 VET entry and the 09:18 IC condor on a fake
# Kite whose instruments() call takes 1.5 s (as the real one can at the
# open). Run:  PYTHONPATH=backend python3 backend/app/execution/test_order_latency.py
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LOG = []
import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: LOG.append(m)
import app.execution.zerodha_executor as ZE                           # noqa: E402
ZE.write_audit_log = lambda m: LOG.append(m)
ZE._load_relay_config = lambda: None                                  # direct, no relay
import app.config.global_loader as GL                                 # noqa: E402
GL.load_global_config = lambda: {"trade_on": True}
ZE.LTPStore.get = staticmethod(lambda s: None)                        # force REST price

FAILS = []
DOWNLOAD_S = 1.5


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Kite:
    VARIETY_REGULAR, TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL = "regular", "BUY", "SELL"
    ORDER_TYPE_LIMIT, PRODUCT_NRML, EXCHANGE_NFO = "LIMIT", "NRML", "NFO"

    def __init__(self, lots):
        self.lots, self.downloads, self.orders = lots, 0, []

    def instruments(self, exch=None):
        self.downloads += 1
        time.sleep(DOWNLOAD_S)
        return [{"tradingsymbol": s, "lot_size": l, "exchange": "NFO"} for s, l in self.lots.items()]

    def ltp(self, k):
        return {k: {"last_price": 5.0}}

    def quote(self, k):
        return {k: {"last_price": 225.0, "depth": {"buy": [{"price": 223.25}],
                                                    "sell": [{"price": 225.5}]}}}

    def place_order(self, **kw):
        self.orders.append(kw)
        return f"OID{len(self.orders)}"


SYMS = {"NIFTY26N1723400PE": 65, "NIFTY26N1725400PE": 65, "NIFTY26N1726800CE": 65,
        "NIFTY26N1723850PE": 65, "NIFTY26N1725750CE": 65, "NIFTY26N1725000PE": 65}


def fresh(csv_rows=None):
    """New process state: empty caches, optional local instruments.csv."""
    ZE._LOT_CACHE.clear()
    ZE._LOT_SRC.clear()
    ZE._LOT_CSV_DONE.clear()
    d = Path(tempfile.mkdtemp(prefix="lotcsv_"))
    ZE._LOT_CSV_PATH = d / "instruments.csv"
    if csv_rows is not None:
        with open(ZE._LOT_CSV_PATH, "w") as f:
            f.write("instrument_token,tradingsymbol,lot_size,exchange,segment\n")
            for i, (s, l, ex) in enumerate(csv_rows):
                f.write(f"{i},{s},{l},{ex},{ex}-OPT\n")
    kite = Kite(dict(SYMS))
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: kite, get_data_kite=lambda: kite)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ZE._LOT_CSV_DONE.wait(5)
    LOG.clear()
    return ex, kite


def lat(op, sym):
    return next((l for l in LOG if l.startswith(f"[ZERODHA][LAT] {op} {sym} ")), "")


def t_vet_entry_from_local_list():
    ex, kite = fresh([(s, 65, "NFO") for s in SYMS] + [("RELIANCE", 1, "NSE")])
    t0 = time.time()
    ex.place_buy("NIFTY26N1723400PE", 1, 130)
    ex.place_sell_entry("NIFTY26N1725400PE", 2, 130)
    dt = time.time() - t0
    check("VET 11-Nov: wing + short sent with NO instrument download", kite.downloads == 0,
          str(kite.downloads))
    check("VET 11-Nov: both orders in well under a second (was 7 s + 4 s)", dt < 0.5, f"{dt:.2f}s")
    check("orders unchanged: wing BUY @ 5.05, short SELL off bid 223.25 @ 218.8",
          [(o["transaction_type"], o["tradingsymbol"], o["quantity"], o["price"]) for o in kite.orders]
          == [("BUY", "NIFTY26N1723400PE", 130, 5.05), ("SELL", "NIFTY26N1725400PE", 130, 218.8)],
          str(kite.orders))
    l1, l2 = lat("BUY", "NIFTY26N1723400PE"), lat("SELL_ENTRY", "NIFTY26N1725400PE")
    check("[ZERODHA][LAT] line per order with lot / price / send / total",
          all(x and "lot=" in x and "price=" in x and "send=" in x and "total=" in x for x in (l1, l2)),
          f"{l1!r} {l2!r}")


def t_ic_condor_without_csv_downloads_once():
    ex, kite = fresh(None)                                   # no local list on this machine
    t0 = time.time()
    for s in ("NIFTY26N1726800CE", "NIFTY26N1723850PE"):
        ex.place_buy(s, 1, 195)
    for s in ("NIFTY26N1725750CE", "NIFTY26N1725000PE"):
        ex.place_sell_entry(s, 1, 195)
    dt = time.time() - t0
    check("IC 4 legs, no local list: ONE download, not four", kite.downloads == 1, str(kite.downloads))
    check("IC 4 legs: total ≈ one download (was ≈ four)", dt < DOWNLOAD_S + 0.6, f"{dt:.2f}s")
    check("the download is logged with its duration",
          any("[ZERODHA][LOT_SIZE] NFO list downloaded" in l for l in LOG))
    check("the first leg's LAT line shows the lot-size time",
          "lot=1" in lat("BUY", "NIFTY26N1726800CE"), lat("BUY", "NIFTY26N1726800CE"))


def t_stale_local_lot_verified():
    ex, kite = fresh([("NIFTY26N1723400PE", 75, "NFO")])    # e.g. a revision since the dump
    ex.place_buy("NIFTY26N1723400PE", 1, 130)
    check("stale local lot 75 vs qty 130 → verified with Kite, order goes (65)",
          kite.downloads == 1 and len(kite.orders) == 1, f"{kite.downloads} {kite.orders}")
    check("…and it is logged", any("verifying with Kite" in l for l in LOG))


def t_genuine_bad_qty_still_rejected():
    ex, kite = fresh([(s, 65, "NFO") for s in SYMS])
    try:
        ex.place_buy("NIFTY26N1723400PE", 1, 100)
        ok = False
    except RuntimeError as e:
        ok = "INVALID_QTY" in str(e)
    check("a qty that is not a lot multiple is still refused (after one Kite check)",
          ok and not kite.orders and kite.downloads == 1)


def t_unknown_symbol_still_fails():
    ex, kite = fresh([(s, 65, "NFO") for s in SYMS])
    try:
        ex.place_buy("NIFTY26N17NOPE", 1, 65)
        ok = False
    except RuntimeError as e:
        ok = "LOT_SIZE_NOT_FOUND" in str(e)
    check("unknown symbol → LOT_SIZE_NOT_FOUND, no order (unchanged)", ok and not kite.orders)


def t_exits_and_bfo():
    ex, kite = fresh([(s, 65, "NFO") for s in SYMS] + [("SENSEX2611882000CE", 20, "BFO")])
    ex.place_buy_exit("NIFTY26N1725400PE", 130, "TEST")
    ex.place_market_sell("NIFTY26N1723400PE", 130)
    check("exits (buy-back, sell) use the map too and log LAT",
          kite.downloads == 0 and lat("BUY_EXIT", "NIFTY26N1725400PE")
          and any(l.startswith("[ZERODHA][LAT] ") and "NIFTY26N1723400PE" in l and "SELL" in l for l in LOG),
          str([l for l in LOG if "LAT" in l]))
    check("BFO (SENSEX) lot sizes come from the same list",
          ex._get_lot_size(kite, "SENSEX2611882000CE", 40) == 20 and kite.downloads == 0)


if __name__ == "__main__":
    for fn in (t_vet_entry_from_local_list, t_ic_condor_without_csv_downloads_once,
               t_stale_local_lot_verified, t_genuine_bad_qty_still_rejected,
               t_unknown_symbol_still_fails, t_exits_and_bfo):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL ORDER_LATENCY CHECKS PASSED")
