from fastapi import APIRouter, Query
from typing import Optional
from app.db.sqlite import get_conn
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/debug/ui", tags=["debug-ui"])


def render_table(title, columns, rows, refresh):
    refresh_meta = (
        f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    )

    header = "".join(f"<th>{c}</th>" for c in columns)

    body = ""
    for r in rows:
        body += (
            "<tr data-row='1'>"
            + "".join(f"<td>{v}</td>" for v in r)
            + "</tr>"
        )

    return f"""
    <html>
    <head>
        <title>{title}</title>
        {refresh_meta}
        <style>
            body {{
                font-family: Arial;
                background:#0b1220;
                color:#e6e6e6;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
            }}
            th, td {{
                border: 1px solid #333;
                padding: 6px;
                font-size: 12px;
                white-space: nowrap;
            }}
            th {{
                background: #1f2937;
                position: sticky;
                top: 0;
            }}
            tr:nth-child(even) {{ background: #111827; }}

            tr.buy {{ background: #3b2f00 !important; }}
            tr.exit {{ background: #003b1f !important; }}

            tr.blink {{
                animation: blink 0.6s linear 4;
            }}

            @keyframes blink {{
                50% {{ opacity: 0.3; }}
            }}
        </style>
    </head>

    <body>
        <h3>{title}</h3>
        <table id="tbl">
            <thead><tr>{header}</tr></thead>
            <tbody>{body}</tbody>
        </table>

        <script>
            const cols = {columns};
            const rows = document.querySelectorAll("tr[data-row]");
            const stateIdx = cols.indexOf("state");
            const idIdx = cols.indexOf("trade_id");

            function beep(freq) {{
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                osc.frequency.value = freq;
                osc.connect(ctx.destination);
                osc.start();
                setTimeout(() => osc.stop(), 150);
            }}

            rows.forEach(row => {{
                const cells = row.children;
                if (stateIdx === -1 || idIdx === -1) return;

                const tradeId = cells[idIdx].innerText;
                const state = cells[stateIdx].innerText;
                const key = "trade_state_" + tradeId;
                const prev = localStorage.getItem(key);

                if (state === "BUY_FILLED") {{
                    row.classList.add("buy");
                }}
                if (state === "CLOSED") {{
                    row.classList.add("exit");
                }}

                if (prev && prev !== state) {{
                    row.classList.add("blink");

                    if (state === "BUY_FILLED") beep(700);
                    if (state === "CLOSED") beep(400);
                }}

                localStorage.setItem(key, state);
            }});
        </script>
    </body>
    </html>
    """


# =========================
# TRADES
# =========================

@router.get("/trades", response_class=HTMLResponse)
def ui_trades(
    limit: int = Query(20, ge=1, le=200),
    symbol: Optional[str] = None,
    state: Optional[str] = None,
    refresh: Optional[int] = Query(None, ge=1, le=60),
):
    conn = get_conn()

    q = "SELECT * FROM trades WHERE 1=1"
    params = []

    if symbol:
        q += " AND symbol = ?"
        params.append(symbol)

    if state:
        q += " AND state = ?"
        params.append(state)

    q += " ORDER BY entry_time DESC LIMIT ?"
    params.append(limit)

    cur = conn.execute(q, tuple(params))
    rows = cur.fetchall()

    if not rows:
        return render_table("trades (empty)", [], [], refresh)

    return render_table(
        "trades",
        list(rows[0].keys()),
        [list(r) for r in rows],
        refresh,
    )


# =========================
# MARKET TIMELINE
# =========================

@router.get("/market_timeline", response_class=HTMLResponse)
def ui_market_timeline(
    limit: int = Query(50, ge=1, le=500),
    symbol: Optional[str] = None,
    refresh: Optional[int] = Query(None, ge=1, le=60),
):
    conn = get_conn()

    BASE_QUERY = """
    SELECT
        id,
        symbol,
        timeframe,
        ts,

        open, high, low, close,

        ema8,
        ema20_low,
        ema20_high,
        rsi_raw,

        cond_close_gt_open,
        cond_close_gt_ema8,
        cond_close_ge_ema20,
        cond_close_not_above_ema20,
        cond_not_touching_high,

        cond_rsi_ge_40,
        cond_rsi_le_65,
        cond_rsi_range,
        cond_rsi_rising,

        cond_is_trading_time,
        cond_no_open_trade,
        cond_all,

        signal,
        strategy_version,
        mode,
        created_at
    FROM market_timeline
    """

    if symbol:
        cur = conn.execute(
            BASE_QUERY + """
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
    else:
        cur = conn.execute(
            BASE_QUERY + """
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )


    rows = cur.fetchall()
    if not rows:
        return render_table("market_timeline (empty)", [], [], refresh)

    return render_table(
        "market_timeline (1m candles + conditions)",
        list(rows[0].keys()),
        [list(r) for r in rows],
        refresh,
    )
