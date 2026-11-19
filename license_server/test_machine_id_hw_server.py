# license_server/test_machine_id_hw_server.py
# ── MACHINE_ID_HW_20261119 ── the server moves a licence from its legacy (MAC)
# id to the hardware id when a 12.0.8+ client presents both; clients that send
# only machine_id behave exactly as before; after the move a copied T2 licence
# (same MAC id) is refused.
#   cd license_server && python3 test_machine_id_hw_server.py
import os, sqlite3, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="licsrv_hw_"))
os.environ.update(LICSRV_SECRETS_DIR=str(TMP / "secrets"), LICSRV_DB=str(TMP / "licenses.db"),
                  LICSRV_DATA_DIR=str(TMP / "data"))
(TMP / "secrets").mkdir()
(TMP / "secrets" / "admin_secret.txt").write_text("test-admin-secret")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey   # noqa: E402
from cryptography.hazmat.primitives import serialization                          # noqa: E402
_priv = Ed25519PrivateKey.generate()
(TMP / "secrets" / "private_key.pem").write_bytes(_priv.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
sys.path.insert(0, str(HERE))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


# A licence DB from before this change (no legacy_machine_id / machine_migrated_at columns).
with sqlite3.connect(TMP / "licenses.db") as c:
    c.execute("""CREATE TABLE licenses (key TEXT PRIMARY KEY, label TEXT NOT NULL, tier TEXT NOT NULL,
                 entitlements_json TEXT NOT NULL, expires_at TEXT NOT NULL, machine_id TEXT, activated_at TEXT,
                 last_heartbeat_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, notes TEXT, phone TEXT, email TEXT,
                 created_at TEXT NOT NULL)""")

import notify                    # noqa: E402
notify.start_expiry_watcher = lambda fn: None
import liveness_watch           # noqa: E402
liveness_watch.start_watcher = lambda: None
PINGS = []
notify.send = lambda text: PINGS.append(text)
import jwt                       # noqa: E402
import db                        # noqa: E402
import server                    # noqa: E402
from pydantic import ValidationError         # noqa: E402

# The route functions are called with their request models: what FastAPI does
# for a JSON body, without needing a matching httpx/starlette test client.
ROUTES = {"/heartbeat": (server.heartbeat, server.DeviceRequest),
          "/activate": (server.activate, server.DeviceRequest),
          "/liveness": (server.liveness, server.LivenessRequest)}
pub = _priv.public_key()
LEGACY = "a" * 64                # the shared T2 en5 MAC id
HW_A = "1" * 64                  # Dhivakar's Mac hardware id
HW_B = "2" * 64                  # another T2 Mac


def post(path, **body):
    fn, model = ROUTES[path]
    return fn(model(**body))


def rejected(path, body):
    """True when FastAPI would answer 422 (the body fails the request model)."""
    try:
        ROUTES[path][1](**body)
        return False
    except ValidationError:
        return True


def bound(key):
    return db.get_license(key)["machine_id"]


def tok_mid(r):
    return jwt.decode(r["token"], pub, algorithms=["EdDSA"])["machine_id"]


def licence(machine_id=None, label="test"):
    k = db.create_license(label, "STANDARD", days=30)["key"]
    if machine_id:
        with sqlite3.connect(TMP / "licenses.db") as c:
            c.execute("UPDATE licenses SET machine_id = ? WHERE key = ?", (machine_id, k))
    return k


print("── TC-LICHW-0101 DB from before the change gets the two columns, idempotently")
cols = {r[1] for r in sqlite3.connect(TMP / "licenses.db").execute("PRAGMA table_info(licenses)")}
check("legacy_machine_id + machine_migrated_at added at startup",
      {"legacy_machine_id", "machine_migrated_at"} <= cols, cols)
db.init_db()
check("a second init_db is a no-op", True)

print("── TC-LICHW-0102 clients before 12.0.8 (machine_id only) are unchanged")
k = licence(LEGACY)
r = post("/heartbeat", key=k, machine_id=LEGACY)
check("bound legacy id → ok, token for the legacy id", r["status"] == "ok" and tok_mid(r) == LEGACY, r)
check("binding untouched, no ping", bound(k) == LEGACY and not PINGS)
check("another id → machine_mismatch", post("/heartbeat", key=k, machine_id=HW_B)["status"] == "machine_mismatch")
check("activate with the bound legacy id → ok", post("/activate", key=k, machine_id=LEGACY)["status"] == "ok")
check("liveness with the bound legacy id → ok", post("/liveness", key=k, machine_id=LEGACY).get("ok") is True)

print("── TC-LICHW-0103 the move on /heartbeat")
PINGS.clear()
r = post("/heartbeat", key=k, machine_id=HW_A, legacy_machine_id=LEGACY)
lic = db.get_license(k)
check("ok, and the token is for the hardware id", r["status"] == "ok" and tok_mid(r) == HW_A, r)
check("binding moved to the hardware id; legacy id + time recorded",
      lic["machine_id"] == HW_A and lic["legacy_machine_id"] == LEGACY and lic["machine_migrated_at"], lic)
check("one Telegram ping naming the move", len(PINGS) == 1 and "Machine id upgraded" in PINGS[0], PINGS)
r = post("/heartbeat", key=k, machine_id=HW_A, legacy_machine_id=LEGACY)
check("next heartbeat: ok, no second ping", r["status"] == "ok" and len(PINGS) == 1, (r, PINGS))
check("admin list shows the move", any(x["key"] == k and x["machine_migrated_at"] for x in db.list_licenses()))

print("── TC-LICHW-0104 after the move a copied T2 licence is refused")
check("old client on another T2 Mac (legacy id only) → machine_mismatch",
      post("/heartbeat", key=k, machine_id=LEGACY)["status"] == "machine_mismatch")
check("new client on another T2 Mac (own hw id + same legacy id) → machine_mismatch",
      post("/heartbeat", key=k, machine_id=HW_B, legacy_machine_id=LEGACY)["status"] == "machine_mismatch")
check("… on /activate too",
      post("/activate", key=k, machine_id=HW_B, legacy_machine_id=LEGACY)["status"] == "machine_mismatch")
check("… and liveness", post("/liveness", key=k, machine_id=HW_B, legacy_machine_id=LEGACY).get("status")
      == "machine_mismatch")
check("binding still on the owner's hardware id, no new ping", bound(k) == HW_A and len(PINGS) == 1)

print("── TC-LICHW-0105 the move on /activate, and first activations")
PINGS.clear()
k2 = licence(LEGACY)
r = post("/activate", key=k2, machine_id=HW_A, legacy_machine_id=LEGACY)
check("activate with both ids on a legacy-bound licence → moved, ok",
      r["status"] == "ok" and tok_mid(r) == HW_A and bound(k2) == HW_A and len(PINGS) == 1, (r, PINGS))
k3 = licence()
r = post("/activate", key=k3, machine_id=HW_A, legacy_machine_id=LEGACY)
lic = db.get_license(k3)
check("first activation by a 12.0.8 client binds the hardware id (marked HW)",
      r["status"] == "ok" and lic["machine_id"] == HW_A and lic["machine_migrated_at"], lic)
db.rebind(k3)
post("/activate", key=k3, machine_id=LEGACY)
lic = db.get_license(k3)
check("after a rebind an old client binds its legacy id (HW mark cleared)",
      lic["machine_id"] == LEGACY and not lic["machine_migrated_at"], lic)

print("── TC-LICHW-0106 liveness accepts either id before the move, and does not move")
k4 = licence(LEGACY)
check("liveness with (hw, legacy) on a legacy-bound licence → ok",
      post("/liveness", key=k4, machine_id=HW_A, legacy_machine_id=LEGACY).get("ok") is True)
check("… binding not moved by liveness", bound(k4) == LEGACY)
check("liveness from a stranger → machine_mismatch",
      post("/liveness", key=k4, machine_id=HW_B, legacy_machine_id="b" * 64).get("status") == "machine_mismatch")

print("── TC-LICHW-0107 compare-and-set: two Macs with the same legacy id cannot both move it")
k5 = licence(LEGACY)
check("first move succeeds", db.migrate_machine(k5, LEGACY, HW_A) is True)
check("second move with the same legacy id fails, binding kept",
      db.migrate_machine(k5, LEGACY, HW_B) is False and bound(k5) == HW_A)

print("── TC-LICHW-0108 request shape")
check("legacy_machine_id is optional and accepted by both request models",
      not rejected("/heartbeat", {"key": k, "machine_id": HW_A})
      and not rejected("/liveness", {"key": k, "machine_id": HW_A, "legacy_machine_id": LEGACY}))
check("a too-short legacy id is rejected (422)",
      rejected("/heartbeat", {"key": k, "machine_id": HW_A, "legacy_machine_id": "x"}))
r = post("/heartbeat", key=k, machine_id=HW_A, legacy_machine_id=HW_A)
check("legacy == machine_id is harmless", r["status"] == "ok" and bound(k) == HW_A, r)

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL MACHINE ID HW SERVER CHECKS PASSED")
