# backend/app/slippage/test_slip_view.py — ── SLIP_VIEW_20261031 ── ── PARITY_CARD_20261031 ──
# Slippage report + routes, and the daily Backtest · Paper · Live picture.
#   cd backend && PYTHONPATH=$PWD python3 app/slippage/test_slip_view.py
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


os.environ["HOME"] = tempfile.mkdtemp(prefix="slipview_")
IST = timezone(timedelta(hours=5, minutes=30))
from app.event_bus.audit_logger import audit_muted          # noqa: E402
from app.slippage import slip_store as SS                   # noqa: E402
from app.slippage import slip_report as SR                  # noqa: E402

NOW = int(time.time())
day0 = int(datetime.fromtimestamp(NOW, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
c = SS.connect()
rows = [  # order, op, side, sym, ref, sent, strategy, status, qty, filled, fill, fill_ts
    ("A1", "SELL_ENTRY", "SELL", "NIFTY26N0323000CE", 117.75, day0 + 33360, "TSG_V1", "COMPLETE", 650, 650, 116.95, day0 + 33362),
    ("A2", "BUY", "BUY", "NIFTY26N0323500CE", 6.05, day0 + 33360, "TSG_V1", "COMPLETE", 650, 650, 6.15, day0 + 33361),
    ("A3", "BUY_EXIT", "BUY", "NIFTY26N0322850PE", 57.50, day0 + 55000, "TSG_V1", "COMPLETE", 650, 650, 57.30, day0 + 55003),
    ("B1", "SELL_ENTRY", "SELL", "NIFTY26N0322900PE", 151.0, day0 + 40000, "VET_V1", "REJECTED", 650, 0, None, None),
    ("C1", "BUY", "BUY", "NIFTY26N0322850CE", 201.05, day0 - 86400 + 40000, "EMC_V1", "COMPLETE", 650, 650, 201.55, day0 - 86400 + 40001),
]
for oid, op, side, sym, ref, sent, sid, st, q, fq, fp, fts in rows:
    c.execute("INSERT INTO order_stamps (order_id, op, side, symbol, ref_ltp, ref_age_s, sent_at, ack_at, strategy_id, status, qty, "
              "filled_qty, fill_price, fill_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (oid, op, side, sym, ref, 0.4, sent, sent + 0.08, sid, st, q, fq, fp, fts))
c.commit()
c.close()

print("── 1. slippage report")
f = {r["order_id"]: r for r in SR.fills(10, NOW)}
check("SELL filled below the LTP sent → paid away: (117.75 − 116.95) × 650 = ₹520",
      f["A1"]["slip_per_unit"] == 0.8 and f["A1"]["slip_rs"] == 520.0, f["A1"])
check("BUY filled above → paid away ₹65; BUY filled below → gained ₹130 (negative)",
      f["A2"]["slip_rs"] == 65.0 and f["A3"]["slip_rs"] == -130.0, (f["A2"]["slip_rs"], f["A3"]["slip_rs"]))
check("fill delay and ack time measured", f["A1"]["delay_s"] == 2.0 and f["A1"]["ack_ms"] == 80, f["A1"])
check("a rejected order is listed, never averaged", f["B1"]["slip_rs"] is None)
s = SR.summary(10, NOW)
t = s["strategies"]["TSG_V1"]
check("per strategy: 3 measured, Σ ₹455, per unit 0.23, 2 adverse, avg delay 2.0 s",
      t["measured"] == 3 and t["slip_rs"] == 455.0 and t["per_unit"] == 0.23 and t["adverse"] == 2 and t["avg_delay_s"] == 2.0, t)
check("rejections counted", s["strategies"]["VET_V1"]["rejected"] == 1)
check("today vs history: yesterday's Ember fill is in the 10-day view, not in today's",
      "EMC_V1" in s["strategies"] and "EMC_V1" not in s["today_by_strategy"] and "TSG_V1" in s["today_by_strategy"])
check("1-day view keeps today only", "EMC_V1" not in SR.summary(1, NOW)["strategies"])

print("── 2. routes (admin gate)")
import app.api_server as API                                # noqa: E402
from app.license import license_state as LS                  # noqa: E402


def call(path, level, method="GET"):
    LS.ui_level = lambda: level
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m_):
        sent.append(m_)
    p, q = (path.split("?", 1) + [""])[:2]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "headers": [],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "root_path": ""}
    with audit_muted():
        asyncio.run(API.app(scope, receive, send))
    st_ = next(m_["status"] for m_ in sent if m_["type"] == "http.response.start")
    return st_, b"".join(m_.get("body", b"") for m_ in sent if m_["type"] == "http.response.body")


s_, b_ = call("/api/slippage/summary?days=10", "admin")
check("GET /api/slippage/summary (admin)", s_ == 200 and json.loads(b_)["strategies"]["TSG_V1"]["slip_rs"] == 455.0, b_[:200])
s_, b_ = call("/api/slippage/fills?days=1", "admin")
check("GET /api/slippage/fills", s_ == 200 and len(json.loads(b_)["fills"]) == 4, b_[:200])
s_, _ = call("/api/slippage/summary", "standard")
check("non-admin → 403", s_ == 403, s_)
import app.recon.recon_core as RCC                           # noqa: E402
RCC.trade_kite = lambda: None
s_, b_ = call("/api/slippage/refresh", "admin", "POST")
check("refresh without a Kite session → a clear message", s_ == 200 and json.loads(b_)["error"] == "no Kite session", b_)

print("── 3. Backtest · Paper · Live picture")
from app.parity import parity_core as PC                     # noqa: E402
from app.parity import parity_card as PCARD                  # noqa: E402
from app.recorder import rec_db as RD                        # noqa: E402
D = datetime.fromtimestamp(NOW, IST).date()
RD.connect().close()


def res(sid, book, verdict, bt, app, rows_):
    return {"ok": True, "strategy_id": sid, "name": PC.SUPPORTED[sid], "day": D.isoformat(), "book": book,
            "summary": {"verdict": verdict, "paired": len(rows_), "matched": len(rows_), "bt_pnl": bt, "app_pnl": app,
                        "missed": 0, "extra": 0, "diff_strike": 0},
            "rows": [{"status": "MATCHED", "entry_delta_min": e, "exit_delta_min": x} for e, x in rows_]}


PC.store(res("TSG_V1", "PAPER", "match", 26553, 26130, [(0, 0)] * 4), "daily")
PC.store(res("TSG_V1", "LIVE", "match", 26553, 25480, [(0, 0)] * 4), "daily")
PC.store(res("EMC_V1", "PAPER", "same trades, timing differs", 65000, 68510, [(1.1, 0.7)]), "daily")
PC.store(dict(res("EMC_V1", "PAPER", "match", 30000, 31000, [(0, 0)]), day=(D - timedelta(days=1)).isoformat()), "daily")
cd = PCARD.card_data(D)
tg = next(r for r in cd["today"] if r["name"] == "Tigris")
check("today: Tigris backtest / paper / live P/L and today's live slippage (₹455 over 3 fills)",
      tg["bt"] == 26553 and tg["paper"] == 26130 and tg["live"] == 25480 and tg["slip"] == 455.0 and tg["slip_n"] == 3, tg)
em = next(r for r in cd["today"] if r["name"] == "Ember")
check("today: Ember Δ entry 1.1 min, Δ exit 0.7 min, verdict text", em["d_entry"] == 1.1 and em["d_exit"] == 0.7
      and em["verdict"].startswith("⚠"), em)
eh = next(r for r in cd["hist"] if r["name"] == "Ember")
check("history: Ember over 2 days — 1 matched, Σ backtest 95,000, Σ paper 99,510",
      eh["days"] == 2 and eh["matched"] == 1 and eh["bt"] == 95000 and eh["paper"] == 99510, eh)
th = next(r for r in cd["hist"] if r["name"] == "Tigris")
check("history gaps compare like with like: live − backtest over the live days only",
      th["gap_l"] == 25480 - 26553 and th["gap_p"] == 26130 - 26553, th)
png = PCARD.build_parity_card_png(D)
check("the picture renders (PNG)", png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20000, len(png or b""))
check("nothing stored → no picture (the job falls back to text)", PCARD.build_parity_card_png(D, {"day": D.isoformat(), "today": [],
                                                                                                    "hist": [], "hist_days": 0}) is None)
SENT = []
import app.api.telegram_summary_send as TSS                  # noqa: E402
import app.api.telegram_api as TA                            # noqa: E402
TA._iter_active_channels = lambda key, mode=None, **k: iter([("tok", "123", {})]) if key == TA.NOTIF_PERSONAL_ALERTS else iter([])
TSS._send_photo = lambda tok, chat, png_, cap: SENT.append((chat, len(png_), cap)) or True
check("send_card → one photo to your private chat, the status lines as caption",
      PCARD.send_card(D, ["Tigris ✓ 4/4", "Ember ⚠ entry 1.1 min apart"]) and SENT and SENT[0][0] == "123"
      and "Tigris ✓ 4/4" in SENT[0][2], SENT)
TSS._send_photo = lambda *a: False
check("photo refused → False (the job then sends the text)", PCARD.send_card(D, ["x"]) is False)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL SLIP_VIEW / PARITY_CARD CHECKS PASSED")
