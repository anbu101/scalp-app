"""Render the REAL Telegram EOD card (backend/app/api/telegram_summary_card.py) for a FICTIONAL day.
Usage: python3 scripts/make_eod_card.py src/assets/eod_card.png   (needs the backend's matplotlib)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.api.telegram_summary_card import build_summary_card_png, CardData, StrategyRow, BookLedger, _with_gross
# FICTIONAL demo day — not real trading data. Codenames only.
live = _with_gross([
    StrategyRow("Timberwolf", 2, 2, 0, 11840, "LIVE", charges=310, best=7420, worst=4420, exit_tp=1, exit_other=1),
    StrategyRow("Icarus", 1, 1, 0, 6230, "LIVE", charges=260, best=6230, worst=6230, exit_other=1),
])
paper = _with_gross([
    StrategyRow("Velvet", 1, 0, 1, -3860, "PAPER", charges=180, best=-3860, worst=-3860, exit_sl=1),
    StrategyRow("Tigris", 2, 1, 1, 9420, "PAPER", charges=520, best=10650, worst=-1230, exit_other=2),
    StrategyRow("Outrider", 1, 1, 0, 4150, "PAPER", charges=150, best=4150, worst=4150, exit_tp=1),
    StrategyRow("Icarus", 1, 1, 0, 6230, "PAPER", charges=260, best=6230, worst=6230, exit_other=1),
])
pl = [(555,0),(570,1200),(600,2600),(630,1900),(660,4300),(690,6100),(720,5200),(750,8800),(780,10400),(810,9600),(840,13300),(870,15100),(900,16800),(930,18070),(940,18070)]
pp = [(555,0),(570,-900),(600,-2400),(630,-1100),(660,1800),(690,3400),(720,2100),(750,6900),(780,9800),(810,8200),(840,11900),(870,14400),(900,15300),(930,15940),(940,15940)]
data = CardData(date_str="09 Nov 2026", subtitle="Mon 09 Nov 2026 · 15:40 IST",
                live_rows=live, paper_rows=paper, source="demo",
                mtm_paths={"LIVE": [list(p) for p in pl], "PAPER": [list(p) for p in pp]})
open(sys.argv[1], "wb").write(build_summary_card_png(data))
print("ok")
