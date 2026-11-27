"""
monitor_server/pages.py — ── FLEET_MONITOR_20261127 ── (TD-110)

Server-rendered HTML for the portal. Every value from a device goes through e().

── FLEET_PORTAL_REDESIGN_20261127 ── (TD-127) the "Midnight" design Anbu chose (direction C + "Needs attention"):
a dark command view — fleet boxes, a Needs-attention strip, a live machines × strategies grid and a live feed —
the same look on every page, and a phone layout (machine rows with strategy chips, tabs at the bottom).

LIVE. A page made with live=True carries data-live on <main>; /static/app.js (same origin, no inline script:
CSP script-src 'self') re-fetches the page every 3 s with "X-Partial: 1" and swaps <main> when it changed —
not while you are typing in it — and keeps "Live · updated N s ago" current. Without JavaScript every page still
works (forms post, links navigate). No inline style attributes: the CSP (style-src 'self') would drop them, so the
heat colours are fixed classes (h-p1…h-p4 / h-n1…h-n4).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from store import IST

ONLINE_S = 150          # market open: the app checks in every 5 s (v12.0.30+) / 60 s (older); 150 s of slack
ONLINE_CLOSED_S = 420   # ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-116) market closed: every 300 s (+ slack)
LATE_S = 900
LIVE_POLL_MS = 3000


def e(v: Any) -> str:
    return escape("" if v is None else str(v), quote=True)


def inr(v: Any, sign: bool = True) -> str:
    """Indian grouping, as the app prints it: +₹1,28,667."""
    try:
        x = round(float(v))
    except Exception:
        return "—"
    s = str(abs(x))
    if len(s) > 3:
        h, t = s[:-3], s[-3:]
        g: List[str] = []
        while len(h) > 2:
            g.insert(0, h[-2:])
            h = h[:-2]
        if h:
            g.insert(0, h)
        s = ",".join(g) + "," + t
    pre = ("+" if x > 0 else "−" if x < 0 else "") if sign else ("−" if x < 0 else "")
    return f"{pre}₹{s}"


def money(v: Any, sign: bool = True) -> str:
    try:
        x = float(v)
    except Exception:
        return '<span class="muted">—</span>'
    cls = "pos" if round(x) > 0 else "neg" if round(x) < 0 else ""
    return f'<span class="{cls}">{inr(x, sign)}</span>'


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


def hm(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), IST).strftime("%H:%M")
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


# ── look ─────────────────────────────────────────────────────────────
CSS = """
/* ── FLEET_PORTAL_ARCADE_20261127 ── (TD-128) the Scalp app's Arcade theme (frontend/src/index.css, THEME_ARCADE_20260906):
   deep violet, magenta primary, cyan labels, neon-green profit, hot-pink loss; magenta grid horizon + faint scanlines in
   the page background only (cards are opaque); Orbitron headings, Space Grotesk UI (served from /static/fonts). */
@font-face{font-family:"Orbitron";font-style:normal;font-weight:400 900;font-display:swap;src:url("/static/fonts/orbitron.woff2") format("woff2")}
@font-face{font-family:"Space Grotesk";font-style:normal;font-weight:300 700;font-display:swap;src:url("/static/fonts/space-grotesk.woff2") format("woff2")}
:root{color-scheme:dark;--bg:#0b0621;--top:#0e0828;--panel:#160c3a;--panel2:#1d1148;--panel3:#211556;--line:#3b2a6e;--line2:#5a45a0;
--fg:#f2e9ff;--mut:#cdbff0;--dim:#a493d6;--faint:#8f7fc2;--up:#39ff8c;--dn:#ff4d6d;--amb:#ffd23f;--mag:#ff3cac;--cyan:#5ce1ff;
--vio:#ff3cac;--acc:#ff3cac;--ui:"Space Grotesk","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;--disp:"Orbitron","Space Grotesk",sans-serif}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;background-color:var(--bg);
background-image:repeating-linear-gradient(0deg,rgba(0,0,0,.16) 0 1px,transparent 1px 3px),
linear-gradient(180deg,#0b0621 0,#0b0621 55%,rgba(11,6,33,.35) 100%),
linear-gradient(rgba(255,60,172,.22) 1px,transparent 1px),linear-gradient(90deg,rgba(255,60,172,.22) 1px,transparent 1px);
background-size:100% 3px,100% 100%,100% 26px,44px 100%;background-attachment:fixed;min-height:100%}
body{margin:0;background:transparent;color:var(--fg);font:14px/1.45 var(--ui)}
::selection{background:rgba(255,60,172,.35)}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#5a45a0;border-radius:2px;border:2px solid #0b0621}
a{color:var(--mag);text-decoration:none}a:hover{text-decoration:underline}
b,strong{font-weight:650}
.pos{color:var(--up)}.neg{color:var(--dn)}.muted{color:var(--dim)}.err{color:var(--dn)}
.top{display:flex;align-items:center;gap:18px;padding:10px 22px;background:var(--top);border-bottom:1px solid var(--line);box-shadow:0 1px 0 rgba(255,60,172,.25);position:sticky;top:0;z-index:5}
.logo{display:flex;gap:9px;align-items:center;font:700 14px var(--disp);letter-spacing:.08em;text-transform:uppercase;color:var(--fg);white-space:nowrap}.logo:hover{text-decoration:none}
.logo i{width:22px;height:22px;border-radius:5px;background:linear-gradient(135deg,#ff3cac,#5ce1ff);display:inline-block;box-shadow:0 0 12px rgba(255,60,172,.5)}
.nav{display:flex;gap:2px}.nav a{color:var(--mut);padding:6px 11px;border-radius:4px;font-weight:500;white-space:nowrap}
.nav a:hover{text-decoration:none;color:#fff}.nav a.on{background:rgba(255,60,172,.16);color:#fff;box-shadow:inset 0 -2px 0 var(--mag)}
.nb{background:var(--dn);color:#fff;border-radius:9px;padding:0 6px;font-size:11px;font-weight:700;margin-left:5px}
.live{margin-left:auto;display:flex;align-items:center;gap:7px;color:var(--mut);font-size:12px;white-space:nowrap}
.live b{width:8px;height:8px;border-radius:50%;background:var(--up);box-shadow:0 0 0 4px rgba(57,255,140,.18),0 0 10px rgba(57,255,140,.6)}
.live.bad b{background:var(--amb);box-shadow:0 0 0 4px rgba(255,210,63,.2)}
.who{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:13px}.top .who{margin-left:12px}
.top:not(:has(.live)) .who{margin-left:auto}
main{padding:18px 22px 30px;max-width:1460px;margin:0 auto}
h1{font:700 19px var(--disp);text-transform:uppercase;letter-spacing:.06em;margin:0 0 14px;text-shadow:0 0 14px rgba(255,60,172,.45)}h2,h3{font-family:var(--disp);text-transform:uppercase;letter-spacing:.06em}h2{font-size:16px;margin:0}
button,.btn{font:inherit;font-size:12.5px;cursor:pointer;background:var(--panel2);color:#fff;border:1px solid var(--line2);
border-radius:4px;padding:6px 12px;white-space:nowrap;display:inline-block}button:hover,.btn:hover{filter:brightness(1.12)}
.btn:hover{text-decoration:none;border-color:var(--acc)}button.p{background:var(--mag);border-color:var(--mag);box-shadow:0 0 12px rgba(255,60,172,.35)}
button.danger{background:var(--dn);border-color:var(--dn);box-shadow:0 0 12px rgba(255,77,109,.35)}
input,select{font:inherit;color:var(--fg);background:#0e0828;border:1px solid var(--line2);border-radius:4px;padding:6px 9px}
input:focus,select:focus{outline:2px solid rgba(255,60,172,.55);outline-offset:0}
.k{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 15px;min-width:0}
.k small{display:block;color:var(--cyan);font-size:11px;letter-spacing:.08em;text-transform:uppercase}.k .v{font-size:24px;font-weight:750;letter-spacing:-.5px;margin-top:2px;white-space:nowrap}
.k .s{color:var(--dim);font-size:11.5px;margin-top:1px}
.hero{display:grid;grid-template-columns:1.45fr 1fr 1fr 1fr 1fr;gap:12px}
.k.big{display:flex;justify-content:space-between;align-items:flex-end;gap:10px}.k.big .v{font-size:31px}
.k.big svg{flex:0 1 180px;max-width:46%}
.kgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.att{margin:14px 0;background:rgba(255,77,109,.08);border:1px solid rgba(255,77,109,.45);border-radius:10px;padding:9px 14px;box-shadow:0 0 18px rgba(255,77,109,.12)}
.att h4{font:600 11px var(--disp);letter-spacing:.12em;color:var(--dn);margin:2px 0 4px;display:flex;gap:8px;align-items:center}
.att h4 span{background:var(--dn);color:#fff;border-radius:9px;padding:0 7px;letter-spacing:0}
.att .row{display:flex;gap:10px;align-items:center;padding:7px 0;border-top:1px solid rgba(255,77,109,.18)}
.att .row:first-of-type{border-top:0}.att .row .t{flex:1;min-width:0}.att .row time{color:var(--dim);font-size:12px}
.att form{margin:0}.att .more{font-size:12.5px;padding:6px 0 2px}
.side{display:grid;grid-template-columns:minmax(0,2.3fr) minmax(0,1fr);gap:14px;margin-top:14px}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;min-width:0}
.box>h4{font:600 11px var(--disp);letter-spacing:.12em;color:var(--cyan);padding:12px 14px 6px;margin:0;display:flex;justify-content:space-between;gap:10px}
.box>h4 a{letter-spacing:0;font-weight:500}
.pad{padding:12px 14px}
.mx{padding:0 8px 6px;overflow-x:auto}
.mx table{border-collapse:separate;border-spacing:4px;width:100%}
.mx th{color:var(--dim);font-weight:500;font-size:12px;text-align:right;padding:4px 9px;white-space:nowrap}.mx th:first-child{text-align:left}
.mx td{padding:7px 9px;border-radius:9px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;vertical-align:middle}
.mx td small{display:block;color:rgba(242,233,255,.62);font-size:11px;font-weight:400}
.mx td.nm{text-align:left}.mx td.nm a{color:var(--fg);font-size:15px;font-weight:650}
.mx td.nm small{color:var(--dim)}.mx td.tot{font-weight:750;font-size:15px}.mx td.dash{color:#3b2a6e}
.mx tr.fleet td{color:var(--mut);border-top:1px solid var(--line)}.mx tr.fleet td b{font-weight:700}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:1px;background:var(--up);box-shadow:0 0 8px rgba(57,255,140,.6)}
.d-warn,.d-paused{background:var(--amb);box-shadow:0 0 8px rgba(255,210,63,.5)}.d-bad{background:var(--dn);box-shadow:0 0 8px rgba(255,77,109,.6)}.d-idle{background:var(--faint);box-shadow:none}
.live-dot{color:var(--up);text-shadow:0 0 6px rgba(57,255,140,.7)}
.h-p1{background:rgba(57,255,140,.10)}.h-p2{background:rgba(57,255,140,.18)}.h-p3{background:rgba(57,255,140,.28)}.h-p4{background:rgba(57,255,140,.42)}
.h-n1{background:rgba(255,77,109,.12)}.h-n2{background:rgba(255,77,109,.22)}.h-n3{background:rgba(255,77,109,.34)}.h-n4{background:rgba(255,77,109,.5)}
.h-0{background:var(--panel2)}
.tag,.badge{font-size:10.5px;font-weight:650;border-radius:20px;padding:1px 8px;margin-left:6px;vertical-align:2px;white-space:nowrap;display:inline-block}
.t-bad,.b-bad{background:rgba(255,77,109,.16);color:var(--dn)}.t-warn,.b-warn{background:rgba(255,210,63,.14);color:var(--amb)}
.b-ok{background:rgba(57,255,140,.12);color:var(--up)}.b-idle,.b-paused{background:rgba(143,127,194,.16);color:var(--mut)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--dim);font-size:11.5px;padding:2px 16px 12px}
.legend span{display:inline-flex;gap:6px;align-items:center}.sw{width:14px;height:10px;border-radius:3px;display:inline-block}
.feed{padding:0 14px 10px}.ev{display:flex;gap:9px;padding:8px 0;border-top:1px solid var(--line);font-size:13px;align-items:flex-start}
.ev:first-child{border-top:0}.ev time{color:var(--dim);font-variant-numeric:tabular-nums;min-width:38px}.ev .i{width:18px;text-align:center}
.ev .t{min-width:0;overflow-wrap:anywhere}
.mlist{display:none}
.mrow{padding:10px 12px;border-top:1px solid var(--line)}.mrow:first-child{border-top:0}
.mrow .h{display:flex;align-items:center;gap:4px}.mrow .h a{color:var(--fg);font-weight:650;font-size:15px}
.mrow .h .mt{margin-left:auto;font-weight:750;font-size:16px}
.mrow .sub{display:flex;justify-content:space-between;align-items:center;gap:8px;color:var(--dim);font-size:11.5px;margin:2px 0 7px}
.cells{display:flex;flex-wrap:wrap;gap:5px}.cells span{border-radius:8px;padding:4px 8px;font-size:12px;font-variant-numeric:tabular-nums}
.cells small{color:rgba(236,235,255,.6);margin-left:4px}.cells .none{color:var(--dim);border:1px dashed var(--line)}
.fleetrow{padding:10px 12px;border-top:1px solid var(--line);font-size:12px;color:var(--mut)}
.tbl{overflow-x:auto}.tbl table{border-collapse:collapse;width:100%}
.tbl th{color:var(--dim);font-weight:500;font-size:12px;text-align:left;padding:9px 12px;white-space:nowrap;border-bottom:1px solid var(--line)}
.tbl td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}.tbl tr:last-child td{border-bottom:0}
.tbl td.r,.tbl th.r{text-align:right;font-variant-numeric:tabular-nums}
.chip{font-size:10.5px;font-weight:700;border-radius:6px;padding:2px 7px;background:var(--panel2);color:var(--mut);letter-spacing:.3px;white-space:nowrap}
.k-CRITICAL,.k-ORDER_REJECTED{background:rgba(255,77,109,.18);color:var(--dn)}.k-SYSTEM_ALERT{background:rgba(255,210,63,.14);color:var(--amb)}
.k-TRADE_ENTRY,.k-GROUP_ENTRY{background:rgba(92,225,255,.14);color:var(--cyan)}.k-TRADE_EXIT,.k-GROUP_EXIT{background:rgba(57,255,140,.12);color:var(--up)}
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:14px}
.filters label{font-size:12px;color:var(--dim);display:flex;flex-direction:column;gap:3px}
.filters .chk{flex-direction:row;align-items:center;gap:6px;padding-bottom:7px;color:var(--mut);font-size:13px}
.filters a{padding-bottom:7px}
form.inline{display:flex;gap:6px;margin:0}form.inline input{width:150px}
.acked{color:var(--mut);font-size:12.5px}.acked b{color:var(--up);font-weight:600}
.warnbox{border-left:3px solid var(--amb);padding:7px 11px;background:rgba(255,210,63,.07);border-radius:0 8px 8px 0;margin:8px 0;font-size:12.5px;color:var(--mut)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(380px,100%),1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.card h3{margin:0;font-size:14px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.meta{color:var(--dim);font-size:12px;margin:3px 0 10px}
.nums{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 10px;margin-bottom:10px}
.nums small{display:block;color:var(--cyan);font-size:11px;letter-spacing:.04em}.nums div{font-weight:600}
.mhead{display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap;margin-bottom:14px}.mhead h1{margin:0}
.mhead .back{color:var(--dim);font-size:13px}.mhead h1{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.admin{display:flex;gap:8px;flex-wrap:wrap}.admin form{margin:0}
.grid2{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:14px;margin-top:14px}
.login{max-width:380px;margin:9vh auto;background:var(--panel);border:1px solid var(--line2);border-radius:10px;padding:26px;box-shadow:0 0 30px rgba(255,60,172,.18)}
.login h2{font-size:18px;margin:0 0 6px;text-shadow:0 0 14px rgba(255,60,172,.45)}.login label{display:block;margin:12px 0 5px;color:var(--mut);font-size:13px}
.login input{width:100%;padding:9px 11px}.login button{width:100%;margin-top:18px;padding:10px;font-size:14px}
.login .brand{display:flex;gap:9px;align-items:center;font:700 12px var(--disp);margin-bottom:16px;color:var(--cyan);letter-spacing:.12em}
.login .brand i{width:22px;height:22px;border-radius:5px;background:linear-gradient(135deg,#ff3cac,#5ce1ff);box-shadow:0 0 12px rgba(255,60,172,.5)}
.login ol{padding-left:18px;color:var(--mut);font-size:13px;margin:6px 0}
.qr{background:#fff;padding:10px;border-radius:10px;width:210px;margin:10px 0}.qr svg{width:100%;height:auto;display:block}
.setupkey{font-family:ui-monospace,Menlo,monospace;font-size:13px;word-break:break-all;background:#0e0828;padding:7px 9px;border-radius:4px;border:1px solid var(--line)}
.small{font-size:12px}.foot{color:var(--dim);font-size:12px;margin-top:14px}
.mt{margin-top:14px}.att-wait{margin-bottom:14px}.grid2>div{min-width:0}.tbl td.r{white-space:nowrap}.box>h4 a{white-space:nowrap}
.tabs{display:none}
.empty{color:var(--dim);padding:14px}
@media (max-width:1100px){.hero{grid-template-columns:1fr 1fr 1fr}.hero .k.big{grid-column:1/-1}.side,.grid2{grid-template-columns:1fr}
.kgrid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:700px){.top{padding:9px 14px;gap:10px}.top .nav,.top .who{display:none}main{padding:12px 12px 90px}
.hero{grid-template-columns:1fr 1fr;gap:8px}.k{padding:10px 12px}.k .v{font-size:20px}.k.big .v{font-size:27px}
.att .row{flex-wrap:wrap}.att .row .t{flex:1 1 70%}
.mx{display:none}.mx+.legend{display:none}.mlist{display:block}
.tabs{display:flex;position:fixed;bottom:0;left:0;right:0;background:var(--top);box-shadow:0 -1px 0 rgba(255,60,172,.25);border-top:1px solid var(--line);justify-content:space-around;
padding:7px 0 calc(10px + env(safe-area-inset-bottom));z-index:5}
.tabs a{font-size:10.5px;color:var(--dim);text-align:center;position:relative;min-width:56px}.tabs a.on{color:var(--mag)}.tabs a:hover{text-decoration:none}
.tabs a i{display:block;font-style:normal;font-size:17px;line-height:1.3}.tabs .nb{position:absolute;top:-3px;right:6px;margin:0}
.cards{grid-template-columns:minmax(0,1fr)}.nums{grid-template-columns:repeat(2,1fr)}
.tbl.stack table,.tbl.stack tbody,.tbl.stack tr,.tbl.stack td{display:block}.tbl.stack tr:first-child{display:none}
.tbl.stack tr{border-bottom:1px solid var(--line);padding:8px 0}.tbl.stack td{border:0;padding:2px 12px}
.filters label{flex:1 1 40%}.filters .chk{flex:0 1 auto}.filters select,.filters input{width:100%}form.inline input{width:100%}}
"""

APP_JS = """(function(){
var m=document.querySelector('main[data-live]');var lv=document.getElementById('live');
var last=Date.now(),fails=0,busy=false;
function tick(){if(!lv)return;var s=Math.max(0,Math.round((Date.now()-last)/1000));
lv.classList.toggle('bad',fails>1);lv.lastChild.textContent=fails>1?'Reconnecting\\u2026':'Live \\u00b7 updated '+s+' s ago';}
function typing(){var a=document.activeElement;if(a&&m.contains(a)&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))return true;
var n=m.querySelectorAll('input[name=note]');for(var i=0;i<n.length;i++){if(n[i].value)return true;}return false;}
function badge(){var b=m.querySelector('#nbv');if(!b)return;var n=b.getAttribute('data-n');
var all=document.querySelectorAll('.nb');for(var i=0;i<all.length;i++){all[i].textContent=n;all[i].hidden=(n==='0');}}
function poll(){if(!m||busy||document.hidden||typing())return;busy=true;
fetch(location.href,{headers:{'X-Partial':'1'},credentials:'same-origin',cache:'no-store'}).then(function(r){
if(r.redirected||r.url.indexOf('/login')>=0){location.href='/login';return null;}if(!r.ok)throw new Error(r.status);return r.text();
}).then(function(t){if(t===null)return;if(t!==m.getAttribute('data-h')){m.innerHTML=t;m.setAttribute('data-h',t);badge();}
last=Date.now();fails=0;}).catch(function(){fails++;}).then(function(){busy=false;tick();});}
if(m){setInterval(poll,%d);setInterval(tick,1000);document.addEventListener('visibilitychange',function(){if(!document.hidden)poll();});}
})();""" % LIVE_POLL_MS

def _ver(text: str) -> str:
    """── FLEET_ASSET_VERSION_20261127 ── (TD-129) a fingerprint of the file: the page asks for app.css?v=<it>, so a
    deploy that changes the look or the script is fetched at once (a new address), and an unchanged one can be cached."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


CSS_VER = _ver(CSS)
JS_VER = _ver(APP_JS)
CSS_URL = f"/static/app.css?v={CSS_VER}"
JS_URL = f"/static/app.js?v={JS_VER}"

NAV = [("/", "Fleet", "▦"), ("/alerts", "Alerts", "⚠"), ("/trades", "Trades", "☰"), ("/timeline", "Timeline", "◷"),
       ("/day", "Day summary", "▤")]


def layout(title: str, body: str, user: Optional[Dict[str, Any]], active: str = "", live: bool = False,
           badge: int = 0, partial: bool = False) -> str:
    """A whole page, or (partial=True, the live refresh) just what goes inside <main>."""
    marker = f'<i id="nbv" hidden data-n="{int(badge)}"></i>'
    if partial:
        return body + marker
    nb = f'<span class="nb"{"" if badge else " hidden"}>{int(badge)}</span>'
    nav = tabs = who = live_html = script = ""
    if user:
        nav = '<nav class="nav">' + "".join(
            f'<a href="{h}"{" class=on" if h == active else ""}>{t}{nb if h == "/alerts" else ""}</a>' for h, t, _ in NAV) + "</nav>"
        tabs = '<nav class="tabs">' + "".join(
            f'<a href="{h}"{" class=on" if h == active else ""}><i>{ic}</i>{t.split()[0]}{nb if h == "/alerts" else ""}</a>'
            for h, t, ic in NAV) + "</nav>"
        who = (f'<form class="who" method="post" action="/logout"><input type="hidden" name="csrf" value="{e(user["csrf"])}">'
               f'<span>{e(user["username"])}</span><button>Log out</button></form>')
        script = f'<script src="{JS_URL}" defer></script>'
    if live:
        live_html = '<div class="live" id="live"><b></b><span>Live</span></div>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="theme-color" content="#0e0828"><title>{e(title)} · Fleet Monitor</title>'
            f'<link rel="stylesheet" href="{CSS_URL}">{script}</head><body>'
            f'<header class="top"><a class="logo" href="/"><i></i>Fleet Monitor</a>{nav}{live_html}{who}</header>'
            f'<main{" data-live" if live else ""}>{body}{marker}</main>{tabs}</body></html>')


def login_page(error: str = "") -> str:
    err = f'<p class="err">{e(error)}</p>' if error else ""
    body = ('<form class="login" method="post" action="/login"><div class="brand"><i></i>SCALP FLEET MONITOR</div>'
            '<h2>Sign in</h2><p class="muted small">Username, password, and the 6-digit code from your authenticator app.</p>'
            f'{err}<label>Username</label><input name="username" autocomplete="username" required autofocus>'
            '<label>Password</label><input name="password" type="password" autocomplete="current-password" required>'
            '<label>Authenticator code</label><input name="code" inputmode="numeric" autocomplete="one-time-code"'
            ' pattern="[0-9 ]{6,7}" required><button class="p">Sign in</button></form>')
    return layout("Sign in", body, None)


def _kpi(label: str, value: str, sub: str = "", cls: str = "") -> str:
    return (f'<div class="k {cls}"><small>{e(label)}</small><div class="v">{value}</div>'
            + (f'<div class="s">{sub}</div>' if sub else "") + "</div>")


def booked_net(t: Dict[str, Any]) -> Optional[float]:
    """── FLEET_MTM_AND_SUMMARY_20261127 ── the app's "Net after charges": realised (booked) P&L minus charges."""
    try:
        return float(t.get("realised") or 0) - float(t.get("charges") or 0)
    except Exception:
        return None


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


def spark(points: List[float], w: int = 120, h: int = 32, cls: str = "pos", fill: bool = True) -> str:
    """Inline SVG line (presentation attributes only: allowed by the CSP)."""
    color = {"pos": "#39ff8c", "neg": "#ff4d6d"}.get(cls, "#a493d6")   # ── FLEET_PORTAL_ARCADE_20261127 ──
    pts = [float(p) for p in points][-400:]
    if len(pts) < 2:
        pts = (pts or [0.0]) * 2
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    rng = (hi - lo) or 1.0
    xy = [(i / (len(pts) - 1) * w, h - (p - lo) / rng * (h - 4) - 2) for i, p in enumerate(pts)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    z = h - (0 - lo) / rng * (h - 4) - 2
    area = f'<path d="{d} L{w},{h} L0,{h} Z" fill="{color}" fill-opacity="0.12"/>' if fill else ""
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">{area}'
            f'<line x1="0" x2="{w}" y1="{z:.1f}" y2="{z:.1f}" stroke="{color}" stroke-opacity="0.25" stroke-dasharray="2 3"/>'
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/></svg>')


def _heat(v: float) -> str:
    a = abs(v)
    lvl = 0 if round(v) == 0 else 1 if a < 1000 else 2 if a < 3000 else 3 if a < 7000 else 4
    return "h-0" if lvl == 0 else f'h-{"p" if v > 0 else "n"}{lvl}'


def _series(points: Iterable[tuple]) -> List[float]:
    return [float(g) for _, g in points]


def _fleet_series(series: Dict[str, List[tuple]], keys: Iterable[str]) -> List[float]:
    """Sum of the machines' per-minute MTM, each carried forward from its last point."""
    keys = [k for k in keys if series.get(k)]
    minutes = sorted({m for k in keys for m, _ in series[k]})
    last = {k: 0.0 for k in keys}
    by = {k: dict(series[k]) for k in keys}
    out = []
    for m in minutes:
        for k in keys:
            if m in by[k]:
                last[k] = by[k][m]
        out.append(sum(last.values()))
    return out


def fleet_lists(user, others, now) -> tuple:
    """── FLEET_MEMBERSHIP_20261127 ── admin-only lists: machines waiting for approval (top) and machines
    not in the fleet (bottom), each with its button. Viewers never see them."""
    if user.get("role") != "admin" or not others:
        return "", ""

    def row(d, label, value):
        return (f'<tr><td>{e(d.get("label"))}</td><td class="muted">{e(d.get("tier") or "")} · '
                f'v{e(d.get("app_version") or "?")}</td><td class="muted">seen {ago(d.get("last_seen"), now)}</td>'
                f'<td><form class="inline" method="post" action="/devices/{e(d["key"])}/membership">'
                f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                f'<input type="hidden" name="membership" value="{value}"><button>{label}</button></form></td></tr>')
    pending = [d for d in others if d.get("membership") == "pending"]
    out = [d for d in others if d.get("membership") == "out"]
    top = ""
    if pending:
        top = ('<div class="box att-wait"><h4>WAITING FOR APPROVAL <span class="badge b-warn">'
               f'{len(pending)}</span></h4><p class="muted small pad">Waiting for approval: new installs. Nothing they send is '
               'kept until you add them; after that they start within 10 min.</p><div class="tbl"><table>'
               + "".join(_pending_row(d, user, now) for d in pending) + '</table></div></div>')
    bottom = ""
    if out:
        bottom = ('<div class="box mt"><h4>NOT IN THE FLEET <span class="badge b-idle">'
                  f'{len(out)}</span></h4><p class="muted small pad">Not in the fleet: hidden from every page and the '
                  'channel; their earlier data comes back with Add back.</p><div class="tbl"><table>'
                  + "".join(row(d, "Add back", "in") for d in out) + '</table></div></div>')
    return top, bottom


def _pending_row(d, user, now) -> str:
    def btn(label, value):
        return (f'<form class="inline" method="post" action="/devices/{e(d["key"])}/membership">'
                f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                f'<input type="hidden" name="membership" value="{value}"><button>{label}</button></form>')
    return (f'<tr><td>{e(d.get("label"))}</td><td class="muted">{e(d.get("tier") or "")} · '
            f'v{e(d.get("app_version") or "?")}</td><td class="muted">seen {ago(d.get("last_seen"), now)}</td>'
            f'<td><div class="admin">{btn("Add to fleet", "in")}{btn("Don&#x27;t add", "out")}</div></td></tr>')


KIND_ICON = {"CRITICAL": "🚨", "ORDER_REJECTED": "🚫", "SYSTEM_ALERT": "⚠️", "TRADE_ENTRY": "➕", "GROUP_ENTRY": "➕",
             "TRADE_EXIT": "✅", "GROUP_EXIT": "✅"}


def _ev_icon(r: Dict[str, Any]) -> str:
    if r.get("kind") in ("TRADE_EXIT", "GROUP_EXIT"):
        try:
            pnl = json.loads(r.get("data") or "{}").get("pnl")
            if isinstance(pnl, (int, float)) and pnl < 0:
                return "❌"
        except Exception:
            pass
    return KIND_ICON.get(r.get("kind"), "•")


def _feed(rows, labels, title_link: str = '<a href="/timeline">Timeline →</a>', head: str = "LIVE FEED") -> str:
    items = "".join(f'<div class="ev"><time>{hm(r["ts"])}</time><span class="i">{_ev_icon(r)}</span>'
                    f'<span class="t"><b>{e(labels.get(r["device_key"], r["device_key"][:8]))}</b> {e(r.get("summary"))}</span></div>'
                    for r in rows)
    return (f'<div class="box"><h4>{head} {title_link}</h4><div class="feed">'
            + (items or '<p class="muted small">Nothing yet today.</p>') + '</div></div>')


def _machine_facts(d, s, today, now):
    data = (s or {}).get("data") or {}
    is_today = (s or {}).get("day") == today
    tot = (data.get("totals") or {}) if is_today else {}
    rows = [r for r in (data.get("rows") or []) if is_today and r.get("state") != "IDLE"]
    broker = data.get("broker") if is_today else None
    return data, is_today, tot, rows, broker


def fleet_page(user, devs, snaps, unacked, now, today, channel: str = "Telegram", others=(),   # ── FLEET_SWITCH_LABEL_20261127 ── channel
               attention=(), series=None, feed=(), partial: bool = False) -> str:
    """── FLEET_PORTAL_REDESIGN_20261127 ── fleet boxes, Needs attention, machines × strategies, live feed."""
    series = series or {}
    labels = {d["key"]: d["label"] for d in devs}
    total_mtm = total_booked = total_risk = 0.0
    total_open = online = 0
    risk_known = False
    offline_names, items, mach = [], [], []
    strat_machines: Dict[str, int] = {}
    strat_tot: Dict[str, float] = {}
    for d in devs:
        s = snaps.get(d["key"]) or {}
        data, is_today, tot, rows, broker = _machine_facts(d, s, today, now)
        st_cls, st_txt = status(d, s, now)
        online += st_cls == "ok"
        if st_cls in ("bad", "warn", "idle"):
            offline_names.append(d["label"])
        total_mtm += float(tot.get("gross") or 0)   # ── FLEET_MTM_AND_SUMMARY_20261127 ── MTM, as the app's headline
        total_booked += booked_net(tot) or 0.0 if tot else 0.0
        total_open += int(tot.get("open_legs") or 0)
        if tot.get("stops_known"):
            risk_known = True
            total_risk += float(tot.get("risk_to_stops") or 0)
        cells = {}
        for r in rows:
            name = str(r.get("codename") or r.get("id") or "?")
            cells[name] = r
            strat_machines[name] = strat_machines.get(name, 0) + 1
            strat_tot[name] = strat_tot.get(name, 0.0) + float(r.get("gross") or 0)
        n_un = int(unacked.get(d["key"], 0))
        kite_bad = isinstance(broker, dict) and not broker.get("trade_ready")
        problem = st_cls in ("bad", "warn", "paused") or n_un > 0 or kite_bad or bool(data.get("error"))
        mach.append(dict(d=d, s=s, tot=tot, cells=cells, st=(st_cls, st_txt), n_un=n_un, broker=broker, problem=problem,
                         data=data, is_today=is_today))
        # Needs attention: machines
        if st_cls in ("bad", "warn"):
            legs_txt = f' · {int(tot.get("open_legs") or 0)} open legs not being reported' if tot.get("open_legs") else ""
            items.append(("🔴", f'<b>{e(d["label"])}</b> — {"offline" if st_cls == "bad" else "late"}: no check-in for '
                                f'{ago(d.get("last_seen"), now).replace(" ago", "")}{legs_txt}', d.get("last_seen"), d["key"], None))
        if kite_bad:
            what = "Kite trading is off" if broker.get("logged_in") else "Kite not logged in; it cannot take trades"
            items.append(("🔑", f'<b>{e(d["label"])}</b> — {what}', broker.get("checked_at"), d["key"], None))
        if data.get("error") and is_today:
            items.append(("⚠️", f'<b>{e(d["label"])}</b> — the app could not build its numbers', s.get("ts"), d["key"], None))
    mach.sort(key=lambda m: (not m["problem"], -float(m["tot"].get("gross") or 0), str(m["d"]["label"]).lower()))
    cols = sorted(strat_machines, key=lambda k: (-strat_machines[k], k.lower()))

    # ── boxes
    fleet_line = _fleet_series(series, [d["key"] for d in devs])
    un_total = sum(unacked.values())
    oldest = min((a["ts"] for a in attention), default=None)
    hero = ('<div class="hero">'
            f'<div class="k big"><div><small>Fleet MTM today · LIVE</small><div class="v">{money(total_mtm)}</div>'
            f'<div class="s">realised + open, before charges</div></div>{spark(fleet_line, 180, 46, "pos" if total_mtm >= 0 else "neg")}</div>'
            + _kpi("Booked after charges", money(total_booked), "closed trades")
            + _kpi("Open legs", str(total_open), f"risk to stops {inr(total_risk)}" if risk_known and total_open else "across the fleet")
            + _kpi("Machines online", f'{online} <span class="muted">/ {len(devs)}</span>',
                   e(", ".join(offline_names[:3]) + (" not reporting" if offline_names else "")) or "all reporting")
            + _kpi("Alerts to acknowledge", f'<span class="{"neg" if un_total else ""}">{un_total}</span>',
                   f"oldest {hm(oldest)}" if oldest else "none")
            + '</div>')

    # ── needs attention
    rows_att = []
    for a in list(attention)[:5]:
        rows_att.append(
            f'<div class="row"><span>{KIND_ICON.get(a["kind"], "🚨")}</span><span class="t"><b>{e(labels.get(a["device_key"], "?"))}'
            f'{(" · " + e(a["codename"])) if a.get("codename") else ""}</b> — {e(a.get("summary"))}</span><time>{hm(a["ts"])}</time>'
            f'<form method="post" action="/alerts/{int(a["id"])}/ack"><input type="hidden" name="csrf" value="{e(user["csrf"])}">'
            f'<input type="hidden" name="note" value=""><button class="danger">Acknowledge</button></form></div>')
    for icon, text, ts, key, _ in items:
        rows_att.append(f'<div class="row"><span>{icon}</span><span class="t">{text}</span><time>{hm(ts) if ts else ""}</time>'
                        f'<a class="btn" href="/machine/{e(key)}">Open</a></div>')
    more = un_total - min(len(attention), 5)
    att = ""
    if rows_att:
        att = (f'<div class="att"><h4>NEEDS ATTENTION <span>{min(len(attention), 5) + len(items) + max(more, 0)}</span></h4>{"".join(rows_att)}'
               + (f'<div class="more"><a href="/alerts?unacked=1&amp;days=7">+{more} more unacknowledged → Alerts</a></div>' if more > 0 else "")
               + '</div>')

    # ── machines × strategies (desktop) and machine rows (phone)
    trs, mrows = [], []
    for m in mach:
        d, tot, (st_cls, st_txt) = m["d"], m["tot"], m["st"]
        key = d["key"]
        tags = ""
        if st_cls != "ok":
            tags += f'<span class="tag {"t-bad" if st_cls == "bad" else "t-warn"}">{st_txt}</span>'
        kite = kite_chip(m["broker"], now) if (isinstance(m["broker"], dict) and not m["broker"].get("trade_ready")) else ""
        if m["n_un"]:
            tags += (f'<a href="/alerts?{urlencode({"device": key, "unacked": "1", "days": "7"})}">'
                     f'<span class="tag t-bad">{m["n_un"]} alert{"" if m["n_un"] == 1 else "s"}</span></a>')
        k_ok = isinstance(m["broker"], dict) and m["broker"].get("trade_ready")
        sub = (f'{"updated " + ago(m["s"].get("ts"), now) if m["s"] else "no report yet"}'
               + (" · Kite ✓" if k_ok else "")
               + ("" if d.get("relay", 1) else f" · not sent to {e(channel)}"))   # ── FLEET_SWITCH_LABEL_20261127 ──
        mtm = tot.get("gross")
        line = _series(series.get(key, []))
        trend = spark(line, 96, 28, "neg" if (mtm or 0) < 0 else "pos", fill=False) if len(line) > 1 else ""
        tds = []
        for c in cols:
            r = m["cells"].get(c)
            if not r:
                tds.append('<td class="dash">·</td>')
                continue
            v = float(r.get("gross") or 0)
            ol = int(r.get("open_legs") or 0)
            live = ' <span class="live-dot">●</span>' if ol or r.get("state") == "OPEN" else ""
            tds.append(f'<td class="{_heat(v)}">{inr(v)}{live}<small>{e(r.get("closed_trades") or 0)} tr'
                       f'{f" · {ol} legs open" if ol else ""}</small></td>')
        trs.append(f'<tr><td class="nm"><span class="dot d-{st_cls}"></span><a href="/machine/{e(key)}">{e(d["label"])}</a>{tags}{kite}'
                   f'<small>{sub}</small></td>{"".join(tds)}'
                   f'<td class="tot">{money(mtm) if m["tot"] else "—"}<small>booked {inr(booked_net(tot)) if tot else "—"}</small></td>'
                   f'<td>{trend}</td></tr>')
        chips = "".join(
            f'<span class="{_heat(float(r.get("gross") or 0))}">{e(c)} <b>{inr(r.get("gross") or 0)}</b>'
            f'{" <span class=live-dot>●</span>" if int(r.get("open_legs") or 0) or r.get("state") == "OPEN" else ""}'
            f'<small>{e(r.get("closed_trades") or 0)} tr</small></span>'
            for c, r in m["cells"].items()) or '<span class="none">No live trades today</span>'
        mrows.append(f'<div class="mrow"><div class="h"><span class="dot d-{st_cls}"></span><a href="/machine/{e(key)}">{e(d["label"])}</a>'
                     f'{tags}{kite}<span class="mt">{money(mtm) if m["tot"] else "—"}</span></div>'
                     f'<div class="sub"><span>{sub} · booked {inr(booked_net(tot)) if tot else "—"}</span>'
                     f'{spark(line, 70, 18, "neg" if (mtm or 0) < 0 else "pos", fill=False) if len(line) > 1 else ""}</div>'
                     f'<div class="cells">{chips}</div></div>')
    if devs:
        fleet_tds = "".join(f'<td class="{"pos" if strat_tot[c] > 0 else "neg" if strat_tot[c] < 0 else ""}"><b>{inr(strat_tot[c])}</b>'
                            f'<small>{strat_machines[c]} mach.</small></td>' for c in cols)
        trs.append(f'<tr class="fleet"><td class="nm">Fleet</td>{fleet_tds}<td class="tot">{money(total_mtm)}</td><td></td></tr>')
        head = "".join(f"<th>{e(c)}</th>" for c in cols)
        grid = (f'<div class="box"><h4>MACHINES × STRATEGIES · MTM TODAY <a href="/day">Day summary →</a></h4>'
                f'<div class="mx"><table><tr><th>Machine</th>{head}<th>MTM today</th><th>Today</th></tr>{"".join(trs)}</table></div>'
                '<div class="legend"><span><i class="sw h-p3"></i>profit</span><span><i class="sw h-n3"></i>loss</span>'
                '<span><b class="live-dot">●</b> in a trade now</span><span>· = not traded today</span>'
                '<span>Click a machine for its positions</span></div>'
                f'<div class="mlist">{"".join(mrows)}'
                + (f'<div class="fleetrow">Fleet by strategy: ' + " · ".join(f'{e(c)} {money(strat_tot[c])}' for c in cols) + '</div>' if cols else "")
                + '</div></div>')
    else:
        grid = '<div class="box"><p class="empty">No machine is in the fleet yet.</p></div>'
    pend, out = fleet_lists(user, others, now)   # ── FLEET_MEMBERSHIP_20261127 ──
    body = (pend + hero + att + f'<div class="side">{grid}{_feed(feed, labels)}</div>' + out)
    return layout("Fleet", body, user, "/", live=True, badge=un_total, partial=partial)


def machine_page(user, d, s, now, today, channel, alerts, open_legs, events, series, badge: int = 0,
                 partial: bool = False) -> str:
    """── FLEET_PORTAL_REDESIGN_20261127 ── one machine: its numbers, strategies, open positions, today's events,
    and (admins) its switches."""
    data, is_today, tot, rows, broker = _machine_facts(d, s, today, now)
    st_cls, st_txt = status(d, s, now)
    key = d["key"]
    n_un = sum(1 for a in alerts if not a.get("ack_user") and a["kind"] in ("CRITICAL", "ORDER_REJECTED"))
    mtm = tot.get("gross")
    line = _series(series)
    head = (f'<div class="mhead"><div><a class="back" href="/">← Fleet</a><h1><span class="dot d-{st_cls}"></span>{e(d["label"])}'
            f'<span class="badge b-{st_cls}">{st_txt}</span>{kite_chip(broker, now)}'
            + (f'<a href="/alerts?{urlencode({"device": key, "unacked": "1", "days": "7"})}"><span class="tag t-bad">{n_un} unacknowledged</span></a>' if n_un else "")
            + f'</h1><div class="meta">seen {ago(d.get("last_seen"), now)} · v{e(d.get("app_version") or "?")} · {e(d.get("tier") or "")}'
            f' · {("sent to " if d.get("relay", 1) else "not sent to ") + e(channel)}'   # ── FLEET_SWITCH_LABEL_20261127 ──
            + (f' · numbers from {hms(s.get("ts"))} IST' if s else "") + '</div></div></div>')
    kp = ('<div class="hero">'
          f'<div class="k big"><div><small>MTM today</small><div class="v">{money(mtm) if tot else "—"}</div>'
          f'<div class="s">realised + open, before charges</div></div>{spark(line, 200, 50, "neg" if (mtm or 0) < 0 else "pos")}</div>'
          + _kpi("Booked after charges", money(booked_net(tot)) if tot else "—", "realised − charges")   # ── FLEET_MTM_AND_SUMMARY_20261127 ──
          + _kpi("Realised", money(tot.get("realised")) if tot else "—", "closed today")
          + _kpi("Open P&L", money(tot.get("unrealised")) if tot else "—", f'{e(tot.get("open_legs", 0))} open legs')
          + _kpi("Charges", money(-float(tot["charges"])) if tot.get("charges") else "—",
                 f'{e(tot.get("closed_trades", 0))} closed trades · risk to stops '
                 + (inr(tot.get("risk_to_stops")) if tot.get("stops_known") else "—"))
          + '</div>')
    notes = []
    if data.get("error"):
        notes.append(f"The app could not build its numbers: {e(data['error'])}")
    if tot.get("approx") or tot.get("marks_stale"):
        notes.append("Some marks are approximate or stale.")
    if d.get("dropped"):
        notes.append(f"{e(d['dropped'])} events were dropped on the machine (outbox full).")
    note_html = "".join(f'<div class="warnbox">{n}</div>' for n in notes)
    srows = "".join(
        f'<tr><td><b>{e(r.get("codename") or r.get("id"))}</b></td><td><span class="chip {"k-TRADE_ENTRY" if r.get("state") == "OPEN" else ""}">{e(r.get("state"))}</span></td>'
        f'<td class="r">{e(r.get("open_legs") or 0)}</td><td class="r">{e(r.get("closed_trades") or 0)}</td>'
        f'<td class="r">{money(r.get("realised"))}</td><td class="r">{money(r.get("unrealised")) if r.get("open_legs") else "—"}</td>'
        f'<td class="r">{money(r.get("gross"))}</td></tr>' for r in rows)
    strat = ('<div class="box"><h4>STRATEGIES TODAY</h4><div class="tbl"><table><tr><th>Strategy</th><th>State</th><th class="r">Open legs</th>'
             f'<th class="r">Closed</th><th class="r">Realised</th><th class="r">Open P&amp;L</th><th class="r">MTM</th></tr>{srows}</table></div></div>'
             if srows else '<div class="box"><h4>STRATEGIES TODAY</h4><p class="empty">No live trades today.</p></div>')
    lrows = "".join(
        f'<tr><td>{e(lg["data"].get("symbol"))}</td><td>{"SELL" if lg["data"].get("short") else "BUY"}</td>'
        f'<td class="r">{e(lg["data"].get("qty"))}</td><td class="r">{num(lg["data"].get("entry"))}</td>'
        f'<td>{e(lg.get("strategy_id"))}</td><td class="muted">{entry_when(lg["data"].get("entry_ts"), today)}</td></tr>' for lg in open_legs)
    legs_box = ('<div class="box"><h4>OPEN POSITIONS</h4><div class="tbl"><table><tr><th>Symbol</th><th>Side</th><th class="r">Qty</th>'
                f'<th class="r">Entry</th><th>Strategy</th><th>Since</th></tr>{lrows}</table></div></div>'
                if lrows else '<div class="box"><h4>OPEN POSITIONS</h4><p class="empty">No open positions.</p></div>')
    admin = ""
    if user["role"] == "admin":
        def frm(action, field, value, label):
            return (f'<form method="post" action="/devices/{e(key)}/{action}"><input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                    f'<input type="hidden" name="{field}" value="{value}"><button>{label}</button></form>')
        admin = ('<div class="box mt"><h4>ADMIN</h4><div class="pad admin">'
                 + frm("enabled", "enabled", 0 if d.get("enabled", 1) else 1, "Pause sending" if d.get("enabled", 1) else "Resume sending")
                 + frm("relay", "relay", 0 if d.get("relay", 1) else 1,                                   # ── FLEET_RELAY_PER_MACHINE_20261127 ──
                       ("Stop sending to " if d.get("relay", 1) else "Send to ") + e(channel))            # ── FLEET_SWITCH_LABEL_20261127 ──
                 + frm("membership", "membership", "out", "Remove from fleet")                            # ── FLEET_MEMBERSHIP_20261127 ──
                 + '</div></div>')
    feed = _feed(events, {key: d["label"]}, "", "TODAY ON THIS MACHINE")
    body = (head + kp + note_html + f'<div class="grid2"><div>{strat}<div class="mt"></div>{legs_box}</div>{feed}</div>' + admin)
    return layout(d["label"], body, user, "/", live=True, badge=badge, partial=partial)


def _device_select(devs, current: str) -> str:
    opts = '<option value="">All machines</option>' + "".join(
        f'<option value="{e(d["key"])}"{" selected" if d["key"] == current else ""}>{e(d["label"])}</option>' for d in devs)
    return f'<label>Machine<select name="device">{opts}</select></label>'


def alerts_page(user, devs, rows, device, unacked_only, days, strategy: str = "", names=(),   # ── ALERT_STRATEGY_INFER_20261127 ──
                badge: int = 0, partial: bool = False) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    day_opts = "".join(f'<option value="{n}"{" selected" if n == days else ""}>{n} day{"s" if n > 1 else ""}</option>'
                       for n in (1, 7, 30, 90))
    filt = (f'<form class="filters" method="get">{_device_select(devs, device)}'
            f'<label>Period<select name="days">{day_opts}</select></label>'
            + '<label>Strategy<select name="strategy"><option value="">All strategies</option>'
            + "".join(f'<option value="{e(n)}"{" selected" if n == strategy else ""}>{e(n)}</option>' for n in names)
            + '</select></label>'
            f'<label class="chk"><input type="checkbox" name="unacked" value="1"{" checked" if unacked_only else ""}> unacknowledged only</label>'
            '<button class="p">Show</button></form>')
    trs = []
    for r in rows:
        if r.get("ack_user"):
            ack = (f'<span class="acked"><b>✓ {e(r["ack_user"])}</b> {dt(r["ack_ts"])}</span>'
                   + (f'<br><span class="muted">{e(r["ack_note"])}</span>' if r.get("ack_note") else ""))
        elif r["kind"] == "SYSTEM_ALERT":
            ack = '<span class="muted">—</span>'
        else:
            ack = (f'<form class="inline" method="post" action="/alerts/{int(r["id"])}/ack">'
                   f'<input type="hidden" name="csrf" value="{e(user["csrf"])}">'
                   f'<input name="note" placeholder="note (optional)" maxlength="300"><button class="danger">Ack</button></form>')
        trs.append(f'<tr><td class="muted">{dt(r["ts"])}</td><td><b>{e(labels.get(r["device_key"], r["device_key"][:8]))}</b></td>'
                   f'<td><span class="chip k-{e(r["kind"])}">{e(r["kind"].replace("_", " "))}</span></td>'
                   f'<td>{e(r.get("codename") or "")}</td><td>{e(r.get("summary"))}</td><td>{ack}</td></tr>')
    table = (f'<div class="box"><div class="tbl stack"><table><tr><th>Time (IST)</th><th>Machine</th><th>Kind</th><th>Strategy</th>'
             f'<th>Message</th><th>Acknowledged</th></tr>{"".join(trs)}</table></div></div>' if trs
             else '<div class="box"><p class="empty">No alerts in this period.</p></div>')
    return layout("Alerts", '<h1>Alerts</h1>' + filt + table, user, "/alerts", live=True, badge=badge, partial=partial)


def trades_page(user, devs, legs, f, badge: int = 0) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get">{_device_select(devs, f["device"])}'
            f'<label>Strategy<input name="strategy" value="{e(f["strategy"])}" placeholder="e.g. IC_V2"></label>'
            f'<label>From<input type="date" name="from" value="{e(f["from"])}"></label>'
            f'<label>To<input type="date" name="to" value="{e(f["to"])}"></label><button class="p">Show</button>'
            f'<a href="/trades.csv?{urlencode(f)}">⬇ Download CSV</a></form>')
    trs, net, n_open = [], 0.0, 0
    for r in legs:
        x = r["data"]
        n_open += bool(x.get("open"))
        net += float(x.get("net") or 0) if not x.get("open") else 0
        trs.append(
            f'<tr><td class="muted">{e(r.get("day"))}</td><td>{entry_when(x.get("entry_ts"), r.get("day"))}</td><td>{hms(x.get("exit_ts")) if x.get("exit_ts") else "open"}</td>'
            f'<td><b>{e(labels.get(r["device_key"], r["device_key"][:8]))}</b></td><td>{e(r.get("strategy_id"))}</td>'
            f'<td>{e(x.get("symbol"))}</td><td>{"SELL" if x.get("short") else "BUY"}</td><td class="r">{e(x.get("qty"))}</td>'
            f'<td class="r">{num(x.get("entry"))}</td><td class="r">{num(x.get("exit")) if x.get("exit") is not None else "—"}</td>'
            f'<td class="r">{money(x.get("net")) if not x.get("open") else "<span class=muted>open</span>"}</td>'
            f'<td class="muted">{e(x.get("reason") or "")}</td></tr>')
    head = (f'<div class="kgrid">{_kpi("Legs", str(len(legs)))}{_kpi("Open", str(n_open))}'
            f'{_kpi("Net of closed legs", money(net))}</div><div class="mt"></div>')
    table = (f'<div class="box"><div class="tbl"><table><tr><th>Day</th><th>In</th><th>Out</th><th>Machine</th><th>Strategy</th>'
             f'<th>Symbol</th><th>Side</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Exit</th>'
             f'<th class="r">Net</th><th>Reason</th></tr>{"".join(trs)}</table></div></div>' if trs
             else '<div class="box"><p class="empty">No live trades for these filters.</p></div>')
    note = ('<p class="foot">Read from each machine\'s trade tables (the same numbers as its Dashboard); '
            'net is after charges where the machine has them.</p>')
    return layout("Trades", '<h1>Trades</h1>' + filt + head + table + note, user, "/trades", badge=badge)


def timeline_page(user, devs, rows, day, device, badge: int = 0, partial: bool = False, live: bool = False) -> str:
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get">{_device_select(devs, device)}'
            f'<label>Day<input type="date" name="day" value="{e(day)}"></label><button class="p">Show</button></form>')
    trs = "".join(
        f'<tr><td>{hms(r["ts"])}</td><td class="muted">{hms(r["received_at"])}</td>'
        f'<td><b>{e(labels.get(r["device_key"], r["device_key"][:8]))}</b></td>'
        f'<td><span class="chip k-{e(r["kind"])}">{e(r["kind"].replace("_", " "))}</span></td>'
        f'<td>{e(r.get("summary"))}</td></tr>' for r in rows)
    table = (f'<div class="box"><div class="tbl stack"><table><tr><th>On machine</th><th>Received</th><th>Machine</th><th>Event</th>'
             f'<th>Detail</th></tr>{trs}</table></div></div>' if trs else '<div class="box"><p class="empty">No events on this day.</p></div>')
    return layout("Timeline", '<h1>Timeline</h1>' + filt + table, user, "/timeline", live=live, badge=badge, partial=partial)


def day_page(user, devs, sums, counts, day, badge: int = 0, partial: bool = False, live: bool = False) -> str:
    """── FLEET_MTM_AND_SUMMARY_20261127 ── the app's Daily Summary: BOOKED (realised) P&L first, then charges and
    net after charges; open legs and their unrealised P&L shown apart, never as the day's result."""
    labels = {d["key"]: d["label"] for d in devs}
    filt = (f'<form class="filters" method="get"><label>Day<input type="date" name="day" value="{e(day)}"></label>'
            f'<button class="p">Show</button></form>')
    cards, fleet_r, fleet_n = [], 0.0, 0.0
    for s in sorted(sums, key=lambda x: labels.get(x["device_key"], "")):
        t = s["totals"] or {}
        fleet_r += float(t.get("realised") or 0)
        fleet_n += booked_net(t) or 0.0
        c = counts.get(s["device_key"], {})
        rows = "".join(f'<tr><td><b>{e(r.get("codename") or r.get("id"))}</b></td><td class="r">{e(r.get("closed_trades"))}</td>'
                       f'<td class="r">{money(r.get("realised"))}</td><td class="r">{e(r.get("open_legs") or 0)}</td>'
                       f'<td class="r">{money(r.get("unrealised")) if r.get("open_legs") else "—"}</td></tr>'
                       for r in s["rows"] if r.get("state") != "IDLE")
        cards.append(
            f'<div class="card"><h3>{e(labels.get(s["device_key"], s["device_key"][:8]))}</h3>'
            f'<div class="meta">last snapshot {hms(s["ts"])} IST · events streamed: '
            f'{c.get("TRADE_ENTRY", 0) + c.get("GROUP_ENTRY", 0)} entries, {c.get("TRADE_EXIT", 0) + c.get("GROUP_EXIT", 0)} exits, '
            f'{c.get("CRITICAL", 0) + c.get("ORDER_REJECTED", 0)} critical</div>'
            f'<div class="nums"><div><small>Booked (realised)</small>{money(t.get("realised"))}</div>'
            f'<div><small>Charges</small>{money(-float(t.get("charges") or 0))}</div>'
            f'<div><small>Net after charges</small>{money(booked_net(t))}</div>'
            f'<div><small>Closed trades</small>{e(t.get("closed_trades", "—"))}</div>'
            f'<div><small>Open legs</small>{e(t.get("open_legs", 0))}</div>'
            f'<div><small>Open P&amp;L (not booked)</small>{money(t.get("unrealised")) if t.get("open_legs") else "—"}</div></div>'
            + (f'<div class="tbl"><table><tr><th>Strategy</th><th class="r">Closed</th><th class="r">Booked</th>'
               f'<th class="r">Open legs</th><th class="r">Open P&amp;L</th></tr>{rows}</table></div>' if rows else
               '<p class="muted">No live trades.</p>') + '</div>')
    body = ('<h1>Day summary</h1>' + filt
            + f'<div class="kgrid">{_kpi("Fleet booked (LIVE)", money(fleet_r), "before charges")}{_kpi("After charges", money(fleet_n))}'
            f'{_kpi("Machines reporting", str(len(sums)))}</div><div class="mt"></div>'
            + (f'<div class="cards">{"".join(cards)}</div>' if cards else '<div class="box"><p class="empty">No snapshot for this day.</p></div>')
            + '<p class="foot">Booked = realised P&amp;L of trades closed that day, before '
              'charges (the app\'s Daily Summary). Open legs are not counted in it.</p>')
    return layout("Day summary", body, user, "/day", live=live, badge=badge, partial=partial)


def now_ist_day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y-%m-%d")


# ── PORTAL_USERS_ADMIN_20261127 ── (TD-113) one-time setup link page
def setup_page(inv: Dict[str, Any], qr: Optional[str], uri: str, error: str = "") -> str:
    err = f'<p class="err">{e(error)}</p>' if error else ""
    what = "Set up your sign-in" if inv["mode"] == "new" else "Reset your sign-in"
    qr_html = (f'<div class="qr">{qr}</div>' if qr else "")
    body = (f'<form class="login" method="post"><div class="brand"><i></i>SCALP FLEET MONITOR</div><h2>{what}</h2>'
            f'<p class="muted small">Fleet Monitor account <b>{e(inv["username"])}</b>'
            f' ({e(inv["role"])})</p>{err}'
            '<label>1. Choose a password (10+ characters)</label>'
            '<input name="password" type="password" autocomplete="new-password" minlength="10" required>'
            '<label>Same password again</label>'
            '<input name="password2" type="password" autocomplete="new-password" minlength="10" required>'
            '<label>2. Add this account to an authenticator app</label>'
            '<ol><li>Open Google Authenticator, Microsoft Authenticator or 1Password.</li>'
            '<li>Add an account and scan this code, or type the setup key.</li></ol>'
            f'<p class="small"><a href="{e(uri)}">On this phone? Tap to add it to your authenticator app</a></p>'
            '<p class="muted small">On iPhone this opens <b>Passwords</b>: tap '
            '<b>+</b> to save a new entry (website 139-59-8-202.sslip.io, your username and this password), '
            'then come back here and type the 6-digit code it shows.</p>'   # ── PORTAL_COPY_FIX_20261127 ──
            f'{qr_html}<div class="setupkey">{e(inv["totp_secret"])}</div>'
            '<label>3. Enter the 6-digit code the app shows now</label>'
            '<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" required>'
            f'<button class="p">Finish and sign in</button>'
            f'<p class="muted small">This link works once. Keep your phone: the app '
            f'asks for a code at every sign-in.</p></form>')
    return layout(what, body, None)


def setup_gone_page() -> str:
    return layout("Link expired", '<div class="login"><div class="brand"><i></i>SCALP FLEET MONITOR</div>'
                  '<h2>This link no longer works</h2>'
                  '<p>It has expired, was already used, or was replaced by a newer one. '
                  'Ask Anbu for a new link.</p><p><a href="/login">Go to sign-in</a></p></div>', None)
