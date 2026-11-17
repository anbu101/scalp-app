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
