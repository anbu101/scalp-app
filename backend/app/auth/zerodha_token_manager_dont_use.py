import json
import datetime
from pathlib import Path
from kiteconnect import KiteConnect


BASE_DIR = Path(__file__).resolve().parents[1]
AUTH_FILE = BASE_DIR / "config" / "zerodha_auth.json"



class ZerodhaTokenManager:

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    # -------------------------
    # Load token from file
    # -------------------------

    def load_token(self) -> str | None:
        if not AUTH_FILE.exists():
            return None

        data = json.loads(AUTH_FILE.read_text())
        token = data.get("access_token")
        date = data.get("date")

        today = datetime.date.today().isoformat()
        if token and date == today:
            return token

        return None

    # -------------------------
    # Save token
    # -------------------------

    def save_token(self, access_token: str):
        data = {
            "access_token": access_token,
            "date": datetime.date.today().isoformat(),
        }
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text(json.dumps(data, indent=2))


    # -------------------------
    # Validate token
    # -------------------------

    def is_token_valid(self, access_token: str) -> bool:
        try:
            kite = KiteConnect(api_key=self.api_key)
            kite.set_access_token(access_token)
            kite.profile()
            return True
        except Exception:
            return False

    # -------------------------
    # Manual login flow
    # -------------------------

    def manual_login(self) -> str:
        kite = KiteConnect(api_key=self.api_key)
        print("Login URL:")
        print(kite.login_url())

        request_token = input("\nPaste request_token here: ").strip()

        data = kite.generate_session(
            request_token,
            api_secret=self.api_secret
        )

        access_token = data["access_token"]
        self.save_token(access_token)
        return access_token

    # -------------------------
    # API-safe login URL (no input)
    # -------------------------

    def get_login_url(self) -> str:
        kite = KiteConnect(api_key=self.api_key)
        return kite.login_url()
