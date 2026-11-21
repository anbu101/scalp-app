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

## Feed stalls on one machine only
<!-- INCIDENT_IP_CLASH_20261120 -->
Symptom: Telegram "no NIFTY tick for Ns — forcing a WebSocket reconnect" from one machine
while another machine on the same Wi-Fi is fine. Every socket on that machine goes silent at once.
1. Compare: did the other machine's log show `WATCHDOG` lines at the same minutes? If yes, it is Zerodha or the internet link. If no, it is that machine's network path.
2. Check for an IP clash: note the stalling machine's IP (Wi-Fi → Details → TCP/IP) and its Wi-Fi address; from any other machine run `arp -an | grep "(<that IP>)"`. A different hardware address there means two devices share the IP.
3. Find the other device: `curl http://<IP>:8008/ssdp/device-desc.xml` (TVs / Chromecasts) or the router's device list.
4. Fix: give the trading machine a different address (Wi-Fi → Details → TCP/IP → "Using DHCP with manual address", high in the range, e.g. 192.168.1.250), or move the other device's static IP out of the router's DHCP pool.
Full write-up: docs/INCIDENT_2026-11-20_FEED_STALLS_IP_CLASH.md
