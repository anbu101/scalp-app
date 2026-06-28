#!/usr/bin/env python3
"""
Probe whether Dhan serves DEEP (last-year) BANKNIFTY history at MINUTE level.
Fill in TOKEN below, then:  python3 probe_bnf_history.py
"""


BNF = 26009          # BANKNIFTY underlying index security id
SEG = "NSE_FNO"

from dhanhq import DhanContext, dhanhq

CLIENT_ID = "1107330409"
TOKEN     = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzgyNjIxNzM2LCJpYXQiOjE3ODI1MzUzMzYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA3MzMwNDA5In0.uTzE70j320leAljnVi8-wJDeobnng6dIJmTw24By2N9dii_52hRl9CRtZ4ijsSzLIot6JVjvbA4dbBYaRvoYeQ"   # <-- regenerate from Dhan, paste here

ctx  = DhanContext(CLIENT_ID, TOKEN)
dhan = dhanhq(ctx)

def show(title, r):
    print("\n" + "="*60); print(title); print("="*60)
    print(str(r)[:600])

# 1) Which expiries does Dhan list for BANKNIFTY? (do past dates appear?)
try:
    show("1) expiry_list(BANKNIFTY)",
         dhan.expiry_list(under_security_id=BNF, under_exchange_segment=SEG))
except Exception as e:
    show("1) expiry_list ERROR", e)

# 2) Expired OPTIONS, PAST month (Sep 2025), MINUTE interval
try:
    show("2) expired_options_data Sep-2025 ATM CALL minute",
         dhan.expired_options_data(
             security_id=BNF, exchange_segment=SEG, instrument_type="OPTIDX",
             expiry_flag="MONTH", expiry_code=1, strike="ATM",
             drv_option_type="CALL",
             required_data=["open","high","low","close","volume"],
             from_date="2025-09-01", to_date="2025-09-30", interval=1))
except Exception as e:
    show("2) expired_options_data ERROR", e)

# 3) Sanity: DAILY past FUT via expiry_code (we KNOW this works) — proves creds OK
try:
    show("3) historical_daily_data FUT Sep-2025 (sanity, should return candles)",
         dhan.historical_daily_data(
             security_id="62326", exchange_segment=SEG, instrument_type="FUTIDX",
             from_date="2025-09-01", to_date="2025-09-30", expiry_code=1, oi=True))
except Exception as e:
    show("3) historical_daily_data ERROR", e)

print("\n\nREAD ME:")
print("- If (1) lists PAST dates AND (2) returns minute arrays with data → DEEP")
print("  history is SOLVABLE via the SDK. Tell Claude.")
print("- If (2) is empty/None but (3) has candles → creds fine, but Dhan only")
print("  serves DAILY for expired BANKNIFTY (no minute) → recent months only.")