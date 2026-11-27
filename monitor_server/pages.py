"""
monitor_server/pages.py — ── FLEET_MONITOR_20261127 ── (TD-110)

Server-rendered HTML for the portal. No JavaScript, no external assets
(the CSP forbids both). Every value from a device goes through e().
"""

from __future__ import annotations

import time
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from store import IST

ONLINE_S = 150          # market open: the app checks in every 60 s
ONLINE_CLOSED_S = 420   # ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-116) market closed: every 300 s (+ slack)
LATE_S = 900


def e(v: Any) -> str:
    return escape("" if v is None else str(v), quote=True)


def money(v: Any, sign: bool = True) -> str:
    try:
        x = float(v)
    except Exception:
        return '<span class="muted">—</span>'
    cls = "pos" if x > 0 else "neg" if x < 0 else ""
    r = round(x)
    pre = ("+" if r > 0 else "−" if r < 0 else "") if sign else ("−" if r < 0 else "")
    return f'<span class="{cls}">{pre}₹{abs(r):,.0f}</span>'


def num(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except Exception:
        return "—"


def hms(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), IST).strftime("%H:%M:%S")
    except Exception:
        return "—"


def dt(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), IST).strftime("%d %b %H:%M:%S")
    except Exception:
        return "—"


def entry_when(ts: Any, day: Any) -> str:
    """── FLEET_OPEN_CADENCE_20261127 ── a leg carried from an earlier day shows that day ("26 Nov 14:49:00"),
    so a row dated by its exit does not look like it was entered the same day."""
    try:
        d = datetime.fromtimestamp(float(ts), IST)
    except Exception:
        return "—"
    return d.strftime("%H:%M:%S") if d.strftime("%Y-%m-%d") == str(day) else d.strftime("%d %b %H:%M:%S")


def ago(ts: Any, now: float) -> str:
    try:
        s = int(now - float(ts))
    except Exception:
        return "never"
    if s < 90:
        return f"{max(s, 0)} s ago"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def status(dev: Dict[str, Any], snap: Optional[Dict[str, Any]], now: float) -> tuple:
    if not dev.get("enabled", 1):
        return "paused", "Paused"
    age = now - float(dev.get("last_seen") or 0)
    market = bool(snap and snap.get("market_open"))
    # ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-116) the window follows the machine's own cadence: its last
    # snapshot said the market was closed → it checks in every 5 min, not every minute
    if age <= (ONLINE_S if market else ONLINE_CLOSED_S):
        return "ok", "Online"
    if age <= LATE_S:
        return "warn", "Late"
    return ("bad", "Offline") if market or _market_hours(now) else ("idle", "Offline")


def _market_hours(now: float) -> bool:
    d = datetime.fromtimestamp(now, IST)
    m = d.hour * 60 + d.minute
    return d.weekday() < 5 and 9 * 60 + 15 <= m <= 15 * 60 + 30


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#14181f;--muted:#69727f;--line:#e3e6ea;--pos:#0a7a3d;--neg:#c0262d;
--ok:#0a7a3d;--warn:#a86200;--bad:#c0262d;--idle:#69727f;--accent:#2457c5;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e8ebef;--muted:#8d96a3;
--line:#2a3039;--pos:#3ccf7d;--neg:#ff6b6b;--ok:#3ccf7d;--warn:#f0a43a;--bad:#ff6b6b;--idle:#8d96a3;
--accent:#7aa2ff;--chip:#222831}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;background:var(--card);
border-bottom:1px solid var(--line);flex-wrap:wrap}
header b{font-size:15px}nav{display:flex;gap:14px;flex-wrap:wrap}nav a.on{font-weight:600;color:var(--fg)}
header form{margin-left:auto}main{padding:16px;max-width:1400px;margin:0 auto}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:150px}
.kpi small{display:block;color:var(--muted)}.kpi span.v{font-size:20px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr));gap:14px}
.tw{overflow-x:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;min-width:0}
.card h3{margin:0;font-size:15px;display:flex;gap:8px;align-items:center}
.meta{color:var(--muted);font-size:12px;margin:2px 0 10px}
.nums{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 10px;margin-bottom:10px}
.nums small{display:block;color:var(--muted);font-size:11px}
.badge{font-size:11px;font-weight:600;padding:1px 7px;border-radius:10px;color:#fff}
.b-ok{background:var(--ok)}.b-warn{background:var(--warn)}.b-bad{background:var(--bad)}
.b-idle,.b-paused{background:var(--idle)}
.chip{font-size:11px;padding:1px 6px;border-radius:4px;background:var(--chip);color:var(--fg)}
.k-CRITICAL,.k-ORDER_REJECTED{background:var(--bad);color:#fff}.k-SYSTEM_ALERT{background:var(--warn);color:#fff}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--muted);font-weight:500;font-size:12px;white-space:nowrap}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
.wrap{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow-x:auto}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--muted)}
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:12px}
.filters label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:2px}
input,select,button{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg)}button{cursor:pointer}button.p{background:var(--accent);color:#fff;border:0}
.warnbox{border-left:3px solid var(--warn);padding:6px 10px;background:var(--chip);margin:8px 0;font-size:12px}
.login{max-width:340px;margin:12vh auto;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:22px}.login label{display:block;margin:10px 0 4px;color:var(--muted)}
.login input{width:100%}.login button{width:100%;margin-top:16px}.err{color:var(--neg)}
form.inline{display:flex;gap:6px}form.inline input{width:140px}
.qr{background:#fff;padding:10px;border-radius:8px;width:210px;margin:8px 0}.qr svg{width:100%;height:auto;display:block}
.setupkey{font-family:ui-monospace,Menlo,monospace;font-size:13px;word-break:break-all;background:var(--chip);
padding:6px 8px;border-radius:6px}.login ol{padding-left:18px;color:var(--muted);font-size:13px;margin:6px 0}
@media (max-width:600px){.grid{grid-template-columns:minmax(0,1fr)}.nums{grid-template-columns:repeat(2,1fr)}
main{padding:12px}.kpi{min-width:0;flex:1 1 140px}th,td{padding:5px 6px}}
"""

NAV = [("/", "Fleet"), ("/alerts", "Alerts"), ("/trades", "Trades"), ("/timeline", "Timeline"), ("/day", "Day summary")]


def layout(title: str, body: str, user: Optional[Dict[str, Any]], active: str = "", refresh: int = 0) -> str:
    nav = "".join(f'<a href="{h}"{" class=on" if h == active else ""}>{t}</a>' for h, t in NAV)
    head_user = ""
    if user:
        head_user = (f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                     f'<span class="muted">{e(user["username"])}</span> <button>Log out</button></form>')
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">{meta}'
            f'<title>{e(title)} · Fleet Monitor</title><link rel="stylesheet" href="/static/app.css"></head><body>'
            f'<header><b>Fleet Monitor</b><nav>{nav if user else ""}</nav>{head_user}</header>'
            f'<main>{body}</main></body></html>')


def login_page(error: str = "") -> str:
    err = f'<p class="err">{e(error)}</p>' if error else ""
    body = ('<form class="login" method="post" action="/login"><h2 style="margin:0">Sign in</h2>'
            f'{err}<label>Username</label><input name="username" autocomplete="username" required autofocus>'
            '<label>Password</label><input name="password" type="password" autocomplete="current-password" required>'
            '<label>Authenticator code</label><input name="code" inputmode="numeric" autocomplete="one-time-code"'
            ' pattern="[0-9 ]{6,7}" required><button class="p">Sign in</button></form>')
    return layout("Sign in", body, None)


def _kpi(label: str, value: str) -> str:
    return f'<div class="kpi"><small>{e(label)}</small><span class="v">{value}</span></div>'


def kite_chip(b: Optional[Dict[str, Any]], now: float) -> str:
    """── KITE_STATUS_PORTAL_20261127 ── (TD-112) the machine's Kite session from its last snapshot.
    None = no fresh answer (outside 08:30–15:30, or an app older than v12.0.26)."""
    if not isinstance(b, dict):
        return ""
    when = f' title="checked {hms(b.get("checked_at"))} IST"'
    if b.get("trade_ready"):
        return f'<span class="badge b-ok"{when}>Kite ✓</span>'
    if b.get("logged_in"):
        return f'<span class="badge b-warn"{when}>Kite: trading off</span>'
    return f'<span class="badge b-bad"{when}>Kite: NOT logged in</span>'


def fleet_page(user, devs, snaps, unacked, now, today, channel: str = "Telegram") -> str:   # ── FLEET_SWITCH_LABEL_20261127 ── channel
    total_net = total_open = online = 0
    cards = []
    for d in devs:
        s = snaps.get(d["key"]) or {}
        data = s.get("data") or {}
        st_cls, st_txt = status(d, s, now)
        online += st_cls == "ok"
        is_today = s.get("day") == today
        tot = (data.get("totals") or {}) if is_today else {}
        total_net += float(tot.get("net") or 0)
        total_open += int(tot.get("open_legs") or 0)
        n_un = unacked.get(d["key"], 0)
        rows = "".join(
            f'<tr><td>{e(r.get("codename") or r.get("id"))}</td><td>{e(r.get("state"))}</td>'
            f'<td class="r">{e(r.get("open_legs"))}</td><td class="r">{e(r.get("closed_trades"))}</td>'
            f'<td class="r">{money(r.get("net"))}</td></tr>'
            for r in (data.get("rows") or []) if is_today and r.get("state") != "IDLE")
        table = (f'<div class="tw"><table><tr><th>Strategy</th><th>State</th><th class="r">Open</th><th class="r">Closed</th>'
                 f'<th class="r">Net</th></tr>{rows}</table></div>' if rows else '<p class="muted">No live trades today.</p>')
        notes = []
        if data.get("error"):
            notes.append(f"Snapshot failed on the machine: {e(data['error'])}")
        if tot.get("approx") or tot.get("marks_stale"):
            notes.append("Some marks are approximate or stale.")
        if d.get("dropped"):
            notes.append(f"{e(d['dropped'])} events were dropped on the machine (outbox full).")
        note_html = "".join(f'<div class="warnbox">{n}</div>' for n in notes)
        kite = kite_chip(data.get("broker") if is_today else None, now)   # ── KITE_STATUS_PORTAL_20261127 ──
        alert_link = (f'<a href="/alerts?{urlencode({"device": d["key"], "unacked": "1"})}">'
                      f'<span class="badge b-bad">{n_un} unacknowledged</span></a>' if n_un else "")
        admin = ""
        if user["role"] == "admin":
            admin = (f'<form method="post" action="/devices/{e(d["key"])}/enabled" style="margin-top:8px">'
                     f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                     f'<input type="hidden" name="enabled" value="{0 if d.get("enabled", 1) else 1}">'
                     f'<button>{"Pause sending" if d.get("enabled", 1) else "Resume sending"}</button></form>')
            # ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-117) per-machine Telegram switch
            admin += (f'<form method="post" action="/devices/{e(d["key"])}/relay" style="margin-top:6px">'
                      f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                      f'<input type="hidden" name="relay" value="{0 if d.get("relay", 1) else 1}">'
                      f'<button>{("Stop sending to " if d.get("relay", 1) else "Send to ") + e(channel)}</button></form>')   # ── FLEET_SWITCH_LABEL_20261127 ──
        cards.append(
            f'<div class="card"><h3>{e(d["label"])} <span class="badge b-{st_cls}">{st_txt}</span> {kite} {alert_link}</h3>'
            f'<div class="meta">seen {ago(d.get("last_seen"), now)} · v{e(d.get("app_version") or "?")}'
            f' · {e(d.get("tier") or "")} · {("sent to " if d.get("relay", 1) else "not sent to ") + e(channel)}</div>'   # ── FLEET_SWITCH_LABEL_20261127 ──
            f'<div class="nums"><div><small>Net today</small>{money(tot.get("net"))}</div>'
            f'<div><small>Realised</small>{money(tot.get("realised"))}</div>'
            f'<div><small>Unrealised</small>{money(tot.get("unrealised"))}</div>'
            f'<div><small>Charges</small>{money(-float(tot["charges"])) if tot.get("charges") else "—"}</div>'   # ── FLEET_OPEN_CADENCE_20261127 ── (TD-119) Net = Realised + Unrealised − Charges
            f'<div><small>Open legs</small>{e(tot.get("open_legs", "—"))}</div>'
            f'<div><small>Closed trades</small>{e(tot.get("closed_trades", "—"))}</div>'
            f'<div><small>Risk to stops</small>{money(tot.get("risk_to_stops")) if tot.get("stops_known") else "—"}</div></div>'
            f'{note_html}{table}{admin}</div>')
    kpis = (_kpi("Fleet net today (LIVE)", money(total_net)) + _kpi("Open legs", str(total_open))
            + _kpi("Machines online", f"{online} / {len(devs)}")
            + _kpi("Unacknowledged alerts", str(sum(unacked.values()))))
    body = (f'<div class="kpis">{kpis}</div><div class="grid">{"".join(cards) or "<p>No machine has reported yet.</p>"}</div>'
            f'<p class="muted" style="margin-top:14px">Refreshes every 30 s · {dt(now)} IST</p>')
    return layout("Fleet", body, user, "/", refresh=30)


def _device_select(devs, current: str) -> str:
    opts = '<option value="">All machines</option>' + "".join(
        f'<option value="{e(d["key"])}"{" selected" if d["key"] == current else ""}>{e(d["label"])}</option>' for d in devs)
    return f'<label>Machine<select name="device">{opts}</select></label>'


def alerts_page(user, devs, rows, device, unacked_only, days, strategy: str = "", names=()) -> str:   # ── ALERT_STRATEGY_INFER_20261127 ──
    labels = {d["key"]: d["label"] for d in devs}
    day_opts = "".join(f'<option value="{n}"{" selected" if n == days else ""}>{n} day{"s" if n > 1 else ""}</option>'
                       for n in (1, 7, 30, 90))
    filt = (f'<form class="filters" method="get">{_device_select(devs, device)}'
            f'<label>Period<select name="days">{day_opts}</select></label>'
            + '<label>Strategy<select name="strategy"><option value="">All strategies</option>'
            + "".join(f'<option value="{e(n)}"{" selected" if n == strategy else ""}>{e(n)}</option>' for n in names)
            + '</select></label>'
            f'<label><span>&nbsp;</span><span><input type="checkbox" name="unacked" value="1"'
            f'{" checked" if unacked_only else ""}> unacknowledged only</span></label><button>Show</button></form>')
    trs = []
    for r in rows:
        if r.get("ack_user"):
            ack = (f'<span class="muted">✓ {e(r["ack_user"])} {dt(r["ack_ts"])}</span>'
                   + (f'<br><span class="muted">{e(r["ack_note"])}</span>' if r.get("ack_note") else ""))
        elif r["kind"] == "SYSTEM_ALERT":
            ack = '<span class="muted">—</span>'
        else:
            ack = (f'<form class="inline" method="post" action="/alerts/{int(r["id"])}/ack">'
                   f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                   f'<input name="note" placeholder="note (optional)" maxlength="300"><button>Ack</button></form>')
        trs.append(f'<tr><td>{dt(r["ts"])}</td><td>{e(labels.get(r["device_key"], r["device_key"][:8]))}</td>'
                   f'<td><span class="chip k-{e(r["kind"])}">{e(r["kind"].replace("_", " "))}</span></td>'
                   f'<td>{e(r.get("codename") or "")}</td><td>{e(r.get("summary"))}</td><td>{ack}</td></tr>')
    table = (f'<div class="wrap"><table><tr><th>Time (IST)</th><th>Machine</th><th>Kind</th><th>Strategy</th>'
             f'<th>Message</th><th>Acknowledged</th></tr>{"".join(trs)}</table></div>' if trs
             else '<p class="muted">No alerts in this period.</p>')
    return layout("Alerts", filt + table, user, "/alerts")


def trades_page(user, devs, legs, f) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get">{_device_select(devs, f["device"])}'
            f'<label>Strategy<input name="strategy" value="{e(f["strategy"])}" placeholder="e.g. IC_V2"></label>'
            f'<label>From<input type="date" name="from" value="{e(f["from"])}"></label>'
            f'<label>To<input type="date" name="to" value="{e(f["to"])}"></label><button>Show</button>'
            f'<a href="/trades.csv?{urlencode(f)}">Download CSV</a></form>')
    trs, net, n_open = [], 0.0, 0
    for r in legs:
        x = r["data"]
        n_open += bool(x.get("open"))
        net += float(x.get("net") or 0) if not x.get("open") else 0
        trs.append(
            f'<tr><td>{e(r.get("day"))}</td><td>{entry_when(x.get("entry_ts"), r.get("day"))}</td><td>{hms(x.get("exit_ts")) if x.get("exit_ts") else "open"}</td>'
            f'<td>{e(labels.get(r["device_key"], r["device_key"][:8]))}</td><td>{e(r.get("strategy_id"))}</td>'
            f'<td>{e(x.get("symbol"))}</td><td>{"SELL" if x.get("short") else "BUY"}</td><td class="r">{e(x.get("qty"))}</td>'
            f'<td class="r">{num(x.get("entry"))}</td><td class="r">{num(x.get("exit")) if x.get("exit") is not None else "—"}</td>'
            f'<td class="r">{money(x.get("net")) if not x.get("open") else "<span class=muted>open</span>"}</td>'
            f'<td>{e(x.get("reason") or "")}</td></tr>')
    head = (f'<div class="kpis">{_kpi("Legs", str(len(legs)))}{_kpi("Open", str(n_open))}'
            f'{_kpi("Net of closed legs", money(net))}</div>')
    table = (f'<div class="wrap"><table><tr><th>Day</th><th>In</th><th>Out</th><th>Machine</th><th>Strategy</th>'
             f'<th>Symbol</th><th>Side</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Exit</th>'
             f'<th class="r">Net</th><th>Reason</th></tr>{"".join(trs)}</table></div>' if trs
             else '<p class="muted">No live trades for these filters.</p>')
    note = ('<p class="muted">Read from each machine\'s trade tables (the same numbers as its Dashboard); '
            'net is after charges where the machine has them.</p>')
    return layout("Trades", filt + head + table + note, user, "/trades")


def timeline_page(user, devs, rows, day, device) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get">{_device_select(devs, device)}'
            f'<label>Day<input type="date" name="day" value="{e(day)}"></label><button>Show</button></form>')
    trs = "".join(
        f'<tr><td>{hms(r["ts"])}</td><td class="muted">{hms(r["received_at"])}</td>'
        f'<td>{e(labels.get(r["device_key"], r["device_key"][:8]))}</td>'
        f'<td><span class="chip k-{e(r["kind"])}">{e(r["kind"].replace("_", " "))}</span></td>'
        f'<td>{e(r.get("summary"))}</td></tr>' for r in rows)
    table = (f'<div class="wrap"><table><tr><th>On machine</th><th>Received</th><th>Machine</th><th>Event</th>'
             f'<th>Detail</th></tr>{trs}</table></div>' if trs else '<p class="muted">No events on this day.</p>')
    return layout("Timeline", filt + table, user, "/timeline")


def day_page(user, devs, sums, counts, day) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get"><label>Day<input type="date" name="day" value="{e(day)}"></label>'
            f'<button>Show</button></form>')
    cards, fleet = [], 0.0
    for s in sorted(sums, key=lambda x: labels.get(x["device_key"], "")):
        t = s["totals"] or {}
        fleet += float(t.get("net") or 0)
        c = counts.get(s["device_key"], {})
        rows = "".join(f'<tr><td>{e(r.get("codename") or r.get("id"))}</td><td class="r">{e(r.get("closed_trades"))}</td>'
                       f'<td class="r">{money(r.get("realised"))}</td><td class="r">{money(r.get("charges"), False)}</td>'
                       f'<td class="r">{money(r.get("net"))}</td></tr>'
                       for r in s["rows"] if r.get("state") != "IDLE")
        cards.append(
            f'<div class="card"><h3>{e(labels.get(s["device_key"], s["device_key"][:8]))}</h3>'
            f'<div class="meta">last snapshot {hms(s["ts"])} IST · events streamed: '
            f'{c.get("TRADE_ENTRY", 0) + c.get("GROUP_ENTRY", 0)} entries, {c.get("TRADE_EXIT", 0) + c.get("GROUP_EXIT", 0)} exits, '
            f'{c.get("CRITICAL", 0) + c.get("ORDER_REJECTED", 0)} critical</div>'
            f'<div class="nums"><div><small>Net</small>{money(t.get("net"))}</div>'
            f'<div><small>Realised</small>{money(t.get("realised"))}</div>'
            f'<div><small>Charges</small>{money(t.get("charges"), False)}</div>'
            f'<div><small>Closed trades</small>{e(t.get("closed_trades", "—"))}</div>'
            f'<div><small>Open at last snapshot</small>{e(t.get("open_legs", "—"))}</div></div>'
            + (f'<div class="tw"><table><tr><th>Strategy</th><th class="r">Closed</th><th class="r">Realised</th>'
               f'<th class="r">Charges</th><th class="r">Net</th></tr>{rows}</table></div>' if rows else
               '<p class="muted">No live trades.</p>') + '</div>')
    body = (filt + f'<div class="kpis">{_kpi("Fleet net (LIVE)", money(fleet))}{_kpi("Machines reporting", str(len(sums)))}</div>'
            + (f'<div class="grid">{"".join(cards)}</div>' if cards else '<p class="muted">No snapshot for this day.</p>'))
    return layout("Day summary", body, user, "/day")


def now_ist_day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y-%m-%d")


# ── PORTAL_USERS_ADMIN_20261127 ── (TD-113) one-time setup link page
def setup_page(inv: Dict[str, Any], qr: Optional[str], uri: str, error: str = "") -> str:
    err = f'<p class="err">{e(error)}</p>' if error else ""
    what = "Set up your sign-in" if inv["mode"] == "new" else "Reset your sign-in"
    qr_html = (f'<div class="qr">{qr}</div>' if qr else "")
    body = (f'<form class="login" method="post" style="max-width:420px"><h2 style="margin:0">{what}</h2>'
            f'<p class="muted" style="margin:4px 0 0">Fleet Monitor account <b>{e(inv["username"])}</b>'
            f' ({e(inv["role"])})</p>{err}'
            '<label>1. Choose a password (10+ characters)</label>'
            '<input name="password" type="password" autocomplete="new-password" minlength="10" required>'
            '<label>Same password again</label>'
            '<input name="password2" type="password" autocomplete="new-password" minlength="10" required>'
            '<label>2. Add this account to an authenticator app</label>'
            '<ol><li>Open Google Authenticator, Microsoft Authenticator or 1Password.</li>'
            '<li>Add an account and scan this code, or type the setup key.</li></ol>'
            f'<p style="margin:6px 0"><a href="{e(uri)}">On this phone? Tap to add it to your authenticator app</a></p>'
            '<p class="muted" style="font-size:12px;margin:0 0 6px">On iPhone this opens <b>Passwords</b>: tap '
            '<b>+</b> to save a new entry (website 139-59-8-202.sslip.io, your username and this password), '
            'then come back here and type the 6-digit code it shows.</p>'   # ── PORTAL_COPY_FIX_20261127 ──
            f'{qr_html}<div class="setupkey">{e(inv["totp_secret"])}</div>'
            '<label>3. Enter the 6-digit code the app shows now</label>'
            '<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" required>'
            f'<button class="p">Finish and sign in</button>'
            f'<p class="muted" style="font-size:12px">This link works once. Keep your phone: the app '
            f'asks for a code at every sign-in.</p></form>')
    return layout(what, body, None)


def setup_gone_page() -> str:
    return layout("Link expired", '<div class="login"><h2 style="margin:0">This link no longer works</h2>'
                  '<p>It has expired, was already used, or was replaced by a newer one. '
                  'Ask Anbu for a new link.</p><p><a href="/login">Go to sign-in</a></p></div>', None)
