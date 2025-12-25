from engine.strike_selector import pick_strikes
instruments = [
    {"symbol":"NIFTY-21500CE","strike":21500,"option_type":"CE","last_price":180,"volume":100,"security_id":"s1"},
    {"symbol":"NIFTY-21600CE","strike":21600,"option_type":"CE","last_price":160,"volume":50,"security_id":"s2"},
    {"symbol":"NIFTY-21500PE","strike":21500,"option_type":"PE","last_price":170,"volume":200,"security_id":"p1"},
    {"symbol":"NIFTY-21600PE","strike":21600,"option_type":"PE","last_price":90,"volume":10,"security_id":"p2"}
]
print(pick_strikes(instruments, 150, 200))
