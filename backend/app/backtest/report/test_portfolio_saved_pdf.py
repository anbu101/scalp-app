# backend/app/backtest/report/test_portfolio_saved_pdf.py
#
# ── PF_SAVED_PDF_20261008 ── behavioural suite: saved portfolios (repo +
# routes) and the portfolio PDF renderer. No TestClient (build-Mac httpx
# pin) — routes are called directly and once through a hand-built ASGI scope.
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_saved_pdf.py

import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import zlib
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pf_saved_pdf_"))
os.environ.setdefault("SCALP_APP_HOME", str(TMP / "home"))

import app.backtest.repo.portfolio_repo as PR               # noqa: E402
from app.backtest.report.portfolio_pdf import (             # noqa: E402
    inr, money, compact_inr, render_portfolio_pdf, text_w)

FAILS, OK = [], [0]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        OK[0] += 1
    else:
        FAILS.append(name)


DB = TMP / "backtest.db"
PR._db_path = lambda: DB                                      # every repo call → temp DB


def seed_runs():
    c = sqlite3.connect(str(DB))
    c.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (run_id TEXT PRIMARY KEY, strategy_id TEXT,
                 underlying TEXT, date_from TEXT, date_to TEXT, status TEXT)""")
    rows = [("r_vet", "VET_V1", "2020-01-01", "2026-08-07", "done"),
            ("r_tsg", "TSG_V1", "2020-01-01", "2026-08-07", "done"),
            ("r_ic2", "IC_V2", "2020-01-01", "2026-08-07", "done"),
            ("r_tma", "TMA_V2", "2020-01-01", "2026-08-07", "done"),
            ("r_orb", "ORB_V1", "2020-01-01", "2026-08-07", "done"),
            ("r_v1", "SCALP_V1", "2020-01-01", "2026-08-07", "done"),
            ("r_vet2", "VET_V1", "2020-01-01", "2026-08-07", "done"),
            ("r_short", "BRK_V1", "2021-01-01", "2026-08-07", "done"),
            ("r_run", "HA_V1", "2020-01-01", "2026-08-07", "running")]
    c.executemany("INSERT OR REPLACE INTO backtest_runs VALUES (?, ?, 'NIFTY', ?, ?, ?)", rows)
    c.commit()
    c.close()


def raises(fn, status=None, text=None):
    try:
        fn()
    except PR.PortfolioError as e:
        return (status is None or e.status == status) and (text is None or text in str(e))
    return False


def repo_suite():
    print("\n── saved portfolios: repo ──")
    seed_runs()
    pf, rep = PR.save_portfolio("  Core   four ", ["r_vet", "r_tsg", "r_ic2", "r_tma"])
    check("save: name whitespace normalised, legs in given order",
          pf["name"] == "Core four" and pf["run_ids"] == ["r_vet", "r_tsg", "r_ic2", "r_tma"] and not rep)
    check("save: legs carry strategy + period and are present",
          [l["strategy_id"] for l in pf["legs"]] == ["VET_V1", "TSG_V1", "IC_V2", "TMA_V2"]
          and all(l["present"] for l in pf["legs"]) and pf["date_from"] == "2020-01-01" and pf["missing"] == 0)
    check("clash: same name (case-insensitive) without replace → 409",
          raises(lambda: PR.save_portfolio("CORE FOUR", ["r_vet", "r_tsg"]), 409, "already exists"))
    pf2, rep2 = PR.save_portfolio("core four", ["r_vet", "r_orb"], replace=True)
    check("replace: same pf_id, runs swapped, flagged replaced",
          pf2["pf_id"] == pf["pf_id"] and pf2["run_ids"] == ["r_vet", "r_orb"] and rep2)
    check("replace keeps created_at, bumps nothing else odd",
          pf2["created_at"] == pf["created_at"] and pf2["updated_at"] >= pf["updated_at"])
    check("1 run rejected", raises(lambda: PR.save_portfolio("x", ["r_vet"]), 400, "at least 2"))
    check("duplicate ids collapse → still needs 2", raises(lambda: PR.save_portfolio("x", ["r_vet", "r_vet"]), 400))
    check("two runs of one strategy rejected",
          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_vet2"]), 400, "One run per strategy"))
    check("mismatched date range rejected",
          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_short"]), 400, "same date range"))
    check("unfinished run rejected", raises(lambda: PR.save_portfolio("x", ["r_vet", "r_run"]), 400, "not done"))
    check("unknown run → 404", raises(lambda: PR.save_portfolio("x", ["r_vet", "nope"]), 404, "not found"))
    check("more than 5 rejected",
          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_tsg", "r_ic2", "r_tma", "r_orb", "r_v1"]), 400, "at most"))
    check("blank name rejected", raises(lambda: PR.save_portfolio("   ", ["r_vet", "r_tsg"]), 400, "name"))
    check("over-long name rejected", raises(lambda: PR.save_portfolio("n" * 81, ["r_vet", "r_tsg"]), 400, "too long"))
    check("bad run_ids type rejected", raises(lambda: PR.save_portfolio("x", "r_vet,r_tsg"), 400))
    PR.save_portfolio("alpha", ["r_tsg", "r_ic2"], note="  try  ")
    lst = PR.list_portfolios()
    check("list: alphabetical (case-insensitive)", [p["name"] for p in lst] == ["alpha", "core four"])
    check("note trimmed", lst[0]["note"] == "try")
    c = sqlite3.connect(str(DB))
    c.execute("DELETE FROM backtest_runs WHERE run_id = 'r_orb'")
    c.commit()
    c.close()
    core = [p for p in PR.list_portfolios() if p["name"] == "core four"][0]
    check("a run deleted later → leg present=False, missing=1 (portfolio kept)",
          core["missing"] == 1 and [l["present"] for l in core["legs"]] == [True, False])
    check("delete: removes the portfolio only", PR.delete_portfolio(core["pf_id"]) == 1
          and [p["name"] for p in PR.list_portfolios()] == ["alpha"])
    c = sqlite3.connect(str(DB))
    n_runs = c.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
    c.close()
    check("...and never touches backtest_runs", n_runs == 8)
    check("delete unknown id → 0, no error", PR.delete_portfolio("zzz") == 0)


# ───────────────────────── PDF ─────────────────────────
ACC = ["#f97316", "#d946ef", "#818cf8", "#c084fc", "#f59e0b"]


def payload(n=4, months=80, pts=1500, taxed=True, with_eq=True, name="Core four"):
    t0 = 1577836800 + 4 * 3600
    labels = ["VET", "TSG", "IC2", "TMA2", "ORB"][:n]
    strats = []
    eq = []
    for i in range(n):
        strats.append({"sid": labels[i], "label": labels[i], "color": ACC[i], "run_id": f"abcdef{i}9999",
                       "params": "Signal EMA10/20 · SMA36 ± ATR36×0.618 · TF 5m · Leg option SELLING · Hedge window "
                                 "09:20–15:15 · Lots 10 · " * 3,
                       "note": "5L - Non-Expiry only" if i == 0 else None,
                       "net": 4556945 - i * 1100000, "pre_tax_net": 4556945, "tax": 120000 if taxed else 0,
                       "taxed": taxed and i < 2, "max_dd": 302620 + i * 1000, "rdd": 15.06 if i else None,
                       "rdd_inf": i == 0, "trades": 2462 + i, "share": 33.0 - i,
                       "dd_contrib": -234220 + i * 90000,
                       "worst_day": {"label": "11 Apr 24", "v": -98000 - i, "portfolio_total": -40000 + i * 30000}})
        if with_eq:
            ts = [t0 + k * 86400 for k in range(pts)]
            v = [k * (900 + i * 200) - (k % 37) * 3000 for k in range(pts)]
            eq.append({"label": labels[i], "color": ACC[i], "thick": False, "ts": ts, "v": v})
    if with_eq:
        eq.append({"label": "Portfolio", "color": "#6d28d9", "thick": True, "ts": eq[0]["ts"],
                   "v": [sum(e["v"][k] for e in eq) for k in range(pts)]})
    ms = []
    for k in range(months):
        y, m = 2020 + k // 12, k % 12 + 1
        per = [((-1) ** (k + j)) * (40000 + 1000 * j) for j in range(n)]
        ms.append({"label": f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m-1]} {y}",
                   "per": per, "n": [0 if (k == 3 and j == 1) else 5 for j in range(n)], "total": sum(per)})
    stat = {"green": 50, "red": 28, "idle": 2, "pct_green": 64.1, "max_red_run": 3,
            "red_run_label": "Mar 2022 to May 2022", "worst": {"v": -210000, "label": "May 2022"},
            "best": {"v": 640000, "label": "Jun 2024"}}
    return {
        "v": 1, "name": name, "period": {"from": "2020-01-01", "to": "2026-08-07"},
        "generated_label": "08 Oct 2026, 07:45 IST", "basis": "after", "any_taxed": taxed,
        "any_tax_available": taxed, "combined_tax": 240000,
        "strategies": strats,
        "kpis": {"combined_net": 13729110, "combined_max_dd": 371107, "dd_reduction": 58.9, "sum_ind_dd": 903256,
                 "return_to_dd": 37.0, "return_to_dd_inf": False, "best_single": 19.27, "best_single_inf": False},
        "dd_window": {"from_label": "11/04, 09:16", "to_label": "08/05, 09:24"},
        "corr": [[1 if a == b else (0.12 if (a + b) % 2 else 0.61) for b in range(n)] for a in range(n)],
        "days": {"rescued": 412, "cluster": 230, "red": 560, "green": 980, "total": 1600,
                 "worst_combined": {"label": "04 Jun 24", "total": -310000}},
        "exposure": {"max_concurrent": 9, "max_strats": n, "n_strats": n, "peak_notional": 1840000,
                     "overlap_pct": 41.3, "t_multi_label": "812h 5m", "t_any_label": "1960h 40m"},
        "monthly": {"months": ms, "stats": {"per": [stat] * n, "combined": stat}},
        "equity": eq,
    }


def pdf_pages(b: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", b))


def pdf_text_blob(b: bytes) -> bytes:
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", b, re.S):
        try:
            out += zlib.decompress(m.group(1))
        except Exception:
            pass
    return out


def pdf_suite():
    print("\n── portfolio PDF renderer ──")
    check("inr: Indian grouping like fmtInr", inr(13729110) == "₹1,37,29,110" and inr(-302620) == "₹3,02,620"
          and inr(999) == "₹999" and inr(1000) == "₹1,000" and inr(None) == "—")
    check("money: sign + grouping", money(4556945) == "+₹45,56,945" and money(-24994) == "−₹24,994")
    check("compact axis labels", compact_inr(13729110) == "₹1.37 Cr" and compact_inr(4556945) == "₹45.6 L"
          and compact_inr(-12000) == "−₹12k")
    check("₹ glyph has width in DejaVu (not a missing-glyph box)", text_w("₹", 10) > 0.5)

    t = time.time()
    b = render_portfolio_pdf(payload())
    dt = time.time() - t
    check("4-strategy, 80-month, 1500-pt payload renders a PDF", b[:5] == b"%PDF-" and len(b) > 20000)
    np_ = pdf_pages(b)
    check("multi-page layout (≥ 4 pages: overview, equity, exposure, months)", np_ >= 4, f"pages={np_}")
    check("fonts embedded as TrueType (pdf.fonttype 42)", b"/FontFile2" in b)
    check("DejaVu embedded", b"DejaVu" in b)
    check(f"render time sane ({dt:.1f}s < 20s)", dt < 20)
    (TMP / "sample_report.pdf").write_bytes(b)

    b5 = render_portfolio_pdf(payload(n=5, months=12, pts=50, taxed=False, name=""))
    check("5 strategies, untaxed, unnamed → renders", b5[:5] == b"%PDF-" and pdf_pages(b5) >= 3)
    b2 = render_portfolio_pdf(payload(n=2, months=1, pts=2, with_eq=False))
    check("2 strategies, no equity points → renders (no-chart message)", b2[:5] == b"%PDF-")
    p = payload(n=3, months=200, pts=10)
    b3 = render_portfolio_pdf(p)
    check("200 months paginate onto extra pages", pdf_pages(b3) > pdf_pages(render_portfolio_pdf(payload(n=3, months=10, pts=10))))
    ugly = payload(n=2, months=3, pts=3)
    ugly["kpis"] = {"combined_net": None, "combined_max_dd": "x", "dd_reduction": None, "return_to_dd": None,
                    "return_to_dd_inf": True}
    ugly["strategies"][0].update({"net": None, "max_dd": None, "worst_day": None, "params": None, "color": None})
    ugly["corr"] = [[1]]                       # short matrix
    ugly["monthly"] = {}
    ugly["exposure"] = None
    ugly["days"] = None
    check("missing / malformed fields never crash the renderer", render_portfolio_pdf(ugly)[:5] == b"%PDF-")
    bad = 0
    for junk in (None, [], {}, {"strategies": []}):
        try:
            render_portfolio_pdf(junk)
        except ValueError:
            bad += 1
    check("payload without strategies → ValueError (route maps to 400)", bad == 4)


# ───────────────────────── routes ─────────────────────────
def asgi_call(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
                         (b"host", b"test")],
             "client": ("127.0.0.1", 5), "server": ("test", 80), "root_path": ""}
    out = {"status": None, "headers": {}, "body": b""}
    sent = [False]

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return out


def route_suite():
    print("\n── routes (direct + hand-built ASGI scope) ──")
    from fastapi import FastAPI, HTTPException
    import app.api.backtest_portfolio_routes as R
    import app.utils.app_paths as AP
    AP.APP_HOME = TMP / "home"

    r = R.save(R.SavePortfolioRequest(name="Route pf", run_ids=["r_vet", "r_tsg", "r_ic2"]))
    check("POST save → ok + portfolio", r["ok"] and r["portfolio"]["name"] == "Route pf" and not r["replaced"])
    try:
        R.save(R.SavePortfolioRequest(name="route PF", run_ids=["r_vet", "r_tsg"]))
        code = None
    except HTTPException as e:
        code = e.status_code
    check("name clash → HTTP 409", code == 409)
    r2 = R.save(R.SavePortfolioRequest(name="route PF", run_ids=["r_vet", "r_tsg"], replace=True))
    check("replace=true → replaced", r2["replaced"] and r2["portfolio"]["run_ids"] == ["r_vet", "r_tsg"])
    try:
        R.save(R.SavePortfolioRequest(name="bad", run_ids=["r_vet", "r_vet2"]))
        code = None
    except HTTPException as e:
        code = e.status_code
    check("invalid composition → HTTP 400", code == 400)

    app = FastAPI()
    app.include_router(R.router)
    res = asgi_call(app, "GET", "/api/backtest/portfolios")
    names = [p["name"] for p in json.loads(res["body"])["portfolios"]] if res["status"] == 200 else []
    check("ASGI GET /api/backtest/portfolios → 200 list", res["status"] == 200 and "route PF" in names)
    res = asgi_call(app, "POST", "/api/backtest/portfolios/report.pdf", payload(n=3, months=24, pts=200, name="My/Mix 3"))
    check("ASGI POST report.pdf → 200 application/pdf bytes",
          res["status"] == 200 and res["headers"].get("content-type", "").startswith("application/pdf")
          and res["body"][:5] == b"%PDF-")
    fn = res["headers"].get("x-report-file", "")
    check("filename is sanitised and dated", fn.startswith("portfolio_My_Mix_3_2020-01-01_to_2026-08-07_") and fn.endswith(".pdf"),
          fn)
    saved = res["headers"].get("x-report-path", "")
    check("a copy is kept under backtest/reports/portfolio", saved and Path(saved).exists()
          and Path(saved).parent == TMP / "home" / "backtest" / "reports" / "portfolio")
    check("Content-Disposition is attachment", "attachment" in res["headers"].get("content-disposition", ""))
    res = asgi_call(app, "POST", "/api/backtest/portfolios/report.pdf", {"strategies": []})
    check("empty payload → 400 (not a blank PDF)", res["status"] == 400)
    pid = r2["portfolio"]["pf_id"]
    res = asgi_call(app, "DELETE", f"/api/backtest/portfolios/{pid}")
    check("ASGI DELETE → deleted 1", res["status"] == 200 and json.loads(res["body"])["deleted"] == 1)
    res = asgi_call(app, "DELETE", f"/api/backtest/portfolios/{pid}")
    check("repeat DELETE is idempotent (deleted 0)", res["status"] == 200 and json.loads(res["body"])["deleted"] == 0)


def wiring_suite():
    print("\n── wiring ──")
    here = Path(__file__).resolve()
    backend = here.parents[3]
    src = (backend / "app" / "api_server.py").read_text(encoding="utf-8")
    i_bt = src.find("app.include_router(backtest_router")
    i_pf = src.find("app.include_router(backtest_portfolio_router, dependencies=[Depends(_require_admin_ui)])")
    i_mount = src.find("SCALP_UI_SERVE")
    check("api_server mounts the portfolio router with the admin gate, after the Backtest router",
          0 < i_bt < i_pf and (i_mount == -1 or i_pf < i_mount))


if __name__ == "__main__":
    repo_suite()
    pdf_suite()
    route_suite()
    wiring_suite()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} PORTFOLIO SAVED/PDF CHECKS PASSED  (sample PDF: {TMP / 'sample_report.pdf'})")
