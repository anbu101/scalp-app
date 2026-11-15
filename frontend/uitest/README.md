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
