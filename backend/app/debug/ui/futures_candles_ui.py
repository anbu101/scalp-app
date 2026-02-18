from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from app.db.futures_candles_repo import fetch_candles

router = APIRouter()


@router.get("/debug/ui/futures_candles", response_class=HTMLResponse)
def futures_candles_ui(
    request: Request,
    symbol: str = Query(...),
    tf: str = Query("3m"),
    limit: int = Query(200),
):
    rows = fetch_candles(
        symbol=symbol,
        timeframe=tf,
        limit=limit,
    )

    if not rows:
        return "<h3>No data found</h3>"

    columns = rows[0].keys()

    html = """
    <html>
    <head>
        <title>Futures Candles Debug</title>
        <style>
            body { font-family: monospace; background: #0b1320; color: #fff; }
            table { border-collapse: collapse; width: 100%; }
            th, td {
                border: 1px solid #444;
                padding: 6px;
                font-size: 12px;
            }
            th { background: #1f2937; }
            tr:nth-child(even) { background: #111827; }
            tr:hover { background: #1f2937; }
        </style>
    </head>
    <body>
        <h2>futures_candles ({symbol} | {tf})</h2>
        <table>
            <tr>
    """

    for col in columns:
        html += f"<th>{col}</th>"

    html += "</tr>"

    for row in rows:
        html += "<tr>"
        for col in columns:
            val = row.get(col)
            html += f"<td>{val if val is not None else ''}</td>"
        html += "</tr>"

    html += """
        </table>
    </body>
    </html>
    """

    return html.format(symbol=symbol, tf=tf)
