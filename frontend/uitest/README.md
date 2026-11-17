# UI checks (tracker T5)

Headless Chrome on the BUILT frontend, against the real backend routes for the
alert bell (in-app event feed) and the Dashboard live chart (replay rule).
Nothing touches the live app: a throw-away HOME, its own port 47321 (close the
app first), no broker.

    cd frontend && CI=false npm run build          # or use the build from build-scalp.sh
    pip3 install playwright fastapi uvicorn        # once; no browser download needed
    PYTHONPATH=../backend python3 uitest/test_ui_fleet.py

Uses Google Chrome from its usual place (set CHROME=/path to override).
`UITEST_OLD_OPEN_RULE=1` runs it against the pre-11-Nov open-position rule:
check C2 must then fail (proves the check can see the 11 Nov symptom).

## The redesigned Dashboard (DASH_CALM_20261117)

    PYTHONPATH=../backend python3 uitest/test_ui_calm.py        # 23 checks, layout "modern"
    PYTHONPATH=../backend python3 uitest/shot_calm.py /tmp/calm # screenshots of 5 scenes

Scenes come from `calm_fixture.py` (Live + Paper day, Paper-only day, before the open).
The suites written for the previous page (`test_ui_dash_system.py`, `test_ui_live_chart.py`,
`test_ui_phone_meter.py`, `shot_dashboard.py`) now set the layout to "classic", which is
that page.

## NIFTY / BANKNIFTY behind the MTM chart (INDEX_PATH_20261117)

    PYTHONPATH=../backend python3 uitest/test_ui_index.py       # 11 checks, layout "modern"
