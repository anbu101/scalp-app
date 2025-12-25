# Runbook

## Daily Startup
1. Start backend
   uvicorn api_server:app --reload --port 8000
2. Start frontend
   npm start
3. Login via UI (Zerodha)
4. Verify /zerodha/status == true

## During Market
- Observe live positions in UI
- Bot runs in LOG or LIVE as configured
- Do NOT intervene manually unless required

## End of Day
1. Generate summary
   python3 -m tools.generate_daily_summary
2. Review logs/YYYY-MM-DD.log

## Safety Rules
- Never enable LIVE without explicit intent
- Never trust UI-calculated PnL
- If in doubt, trust Zerodha over bot state

## Recovery
- Restart-safe by design
- Tokens are re-used per day
- State files restore open trades
