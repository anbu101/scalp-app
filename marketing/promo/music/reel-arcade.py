# Soundtrack for reel-arcade (vertical feature reel): the Mass trailer's sound, built by arcade_mass.py,
# with this cut's headline slams (times from reel-arcade.jsx, all on the beat grid).
# fence: PROMO_ARCADE_20261120
import sys
from arcade_mass import build
# on-screen hits (seconds), (time, weight): weight >= 1 gets the full chord stab
HITS = [(0.5, 1), (0.75, .7),                                   # ON / AUTOPILOT.
        (2.0, 1.3), (2.25, .7),                                 # TRADES / ITSELF.
        (6.0, 1.2), (6.25, .7), (7.0, .6), (7.5, .6), (8.0, .6),  # SLICK / EXECUTION. + three tiles
        (10.0, 1.4), (10.25, .8), (10.5, .7),                   # 6+ / YEARS / BACKTESTED
        (14.0, 1.2), (14.5, .8),                                # COMPOUNDING / switch on
        (18.0, 1.3), (18.25, .7),                               # EVERY TRADE, / IN TELEGRAM.
        (22.0, 1.5), (22.25, .8), (22.5, .8)]                   # ⚡ / SCALP / TERMINAL

build(sys.argv[1], HITS)
print("ok reel-arcade")
