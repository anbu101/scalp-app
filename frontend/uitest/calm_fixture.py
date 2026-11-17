"""
calm_fixture.py — DASH_CALM_20261117: day scenes for the redesigned Dashboard, built on
dash_fixture (same rows: Live + Paper at 13:05) plus canned meter readings, the shape
/api/replay/meters returns (the real route is exercised in test_ui_calm.py on the
replay scene).
"""
import copy

from dash_fixture import FLEET


def _w(status, headline, side, score, word, items, reason=""):
    return {"ok": True, "watch": side == "entry", "live": {"active": True, "poll_ms": 2000, "stale": False, "no_feed": []},
            "waiting": {"status": status, "headline": headline, "items": items,
                        "meter": {"side": side, "score": score, "level": 3 if score >= 60 else 1, "word": word, "reason": reason}}}


METERS = {
    "IC_V2": _w("holding", "In trade since 09:18 — condor, adjustments on", "exit", 35, "calm",
                [{"ok": True, "text": "Short strikes outside the range", "value": "1.9% away"}]),
    "TMA_V2": _w("near", "In trade since 10:30 — E1 bearish SELL CE + hedge", "exit", 70, "imminent",
                 [{"ok": True, "text": "XOVER: EMA13 must stay below EMA55", "value": "-10.6"},
                  {"ok": True, "text": "SL 117.40 · S 23400CE (LTP 64.12)", "value": "53.28 away · 0% of the way"},
                  {"ok": None, "text": "TP 10.00 · S 23400CE", "value": "54.12 away · 15% of the way"}],
                 "0/1 flipped · edge 10.6 pts"),
    "TSG_V1": _w("holding", "Strangle held to 15:15 or the basket stop", "exit", 20, "calm",
                 [{"ok": True, "text": "Basket MTM above the stop", "value": "+3,920 vs −35,000"}]),
    "EMC_V1": _w("waiting", "Needs an EMA 7/20 cross on a 5-minute close", "entry", 40, "building",
                 [{"ok": False, "text": "EMA7 above EMA20", "value": "-4.2"}], "EMA 7 is 4.2 pts under EMA 20"),
    "BRK_V1": _w("waiting", "Needs a break of the opening range", "entry", 25, "far",
                 [{"ok": False, "text": "Close beyond the range", "value": "38 pts away"}], "range high 25,412 · 38 pts away"),
}


def paper_only():
    f = copy.deepcopy(FLEET)
    f["strategies"] = [r for r in f["strategies"] if r["book"] != "LIVE"]
    f["totals"]["LIVE"] = dict(f["totals"]["LIVE"], rows=0)
    f["paths"] = {"PAPER": f["paths"]["PAPER"]}
    f["active"] = len({r["id"] for r in f["strategies"]})
    return f


def pre_open():
    f = copy.deepcopy(FLEET)
    f["strategies"] = []
    for b in ("LIVE", "PAPER"):
        f["totals"][b] = dict(f["totals"][b], rows=0, gross=0, realised=0, unrealised=0)
    f["paths"] = {}
    f["active"] = 0
    f["idle"] = [{"id": "EMC_V1", "mode": "PAPER"}, {"id": "BRK_V1", "mode": "PAPER"}, {"id": "TMA_V2", "mode": "PAPER_LIVE"}]
    return f


# ── INDEX_PATH_20261117 ── today's index lines (09:15 → 13:05) in the /api/fleet/indices shape,
# and the live badge feed (/market_indices) with the same last price
def _walk(start, end, n, vol, seed):
    import random
    rnd = random.Random(seed)
    x, out = start, []
    for i in range(n):
        tgt = start + (end - start) * i / (n - 1)
        x += (tgt - x) * 0.2 + rnd.gauss(0, vol)
        out.append(round(x, 2))
    out[-1] = end
    return out


NOW_MIN_IDX = 785
_N = NOW_MIN_IDX - 555 + 1
NIFTY_PATH = [[555 + i, v] for i, v in enumerate(_walk(25310.0, 25492.70, _N, 3.0, 5))]
BANK_PATH = [[555 + i, v] for i, v in enumerate(_walk(61640.0, 61982.40, _N, 9.0, 6))]
INDICES = {"ok": True, "day": "2026-11-17", "now_min": NOW_MIN_IDX, "indices": {
    "NIFTY": {"ltp": 25492.70, "prev_close": 25291.10, "path": NIFTY_PATH, "source": "kite+live", "note": None},
    "BANKNIFTY": {"ltp": 61982.40, "prev_close": 61575.85, "path": BANK_PATH, "source": "kite+live", "note": None}}}
MARKET = {"NIFTY": {"ltp": 25492.70, "prev_close": 25291.10, "change": 201.6, "change_pct": 0.8},
          "BANKNIFTY": {"ltp": 61982.40, "prev_close": 61575.85, "change": 406.55, "change_pct": 0.66},
          "SENSEX": {"ltp": None, "prev_close": None, "change": None, "change_pct": None}}
PRE_INDICES = {"ok": True, "day": "2026-11-17", "now_min": 533, "indices": {
    k: dict(v, path=[], source="none", ltp=v["prev_close"]) for k, v in INDICES["indices"].items()}}
