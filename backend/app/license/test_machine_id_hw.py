# backend/app/license/test_machine_id_hw.py
# ── MACHINE_ID_HW_20261119 ── the licence id comes from the hardware UUID, so two
# Intel T2 Macs (same en5 MAC ac:de:48:00:11:22) get different ids; the MAC id
# rides along as legacy_machine_id until the server has moved every licence.
#   cd backend && PYTHONPATH=$PWD python3 app/license/test_machine_id_hw.py
import hashlib, os, subprocess, sys, tempfile, time
from types import SimpleNamespace
os.environ["HOME"] = tempfile.mkdtemp(prefix="mid_hw_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


from app.license import machine_id as MID          # noqa: E402
from app.license import license_client as LC      # noqa: E402
from app.license import license_state as LS       # noqa: E402
import app.jobs.liveness_ping as LP                # noqa: E402

MID.platform.system = lambda: "Darwin"
MID.platform.machine = lambda: "x86_64"

EN5 = 0xacde48001122                                # every Intel T2 Mac has this en5 MAC
LEGACY_T2 = hashlib.sha256(f"Darwin|x86_64|{hex(EN5)}".encode()).hexdigest()
UUID_A = "5A1B2C3D-0000-4E5F-8A9B-111111111111"     # Dhivakar's Mac (made up)
UUID_B = "9F8E7D6C-0000-4B3A-9C8D-222222222222"     # another T2 Mac (made up)


def hw(u, system="Darwin"):
    """The new id, written out independently of machine_id.py."""
    return hashlib.sha256(f"scalp-mid-v2|{system}|{u.lower()}".encode()).hexdigest()


T2_IFCONFIG = ("ap1: flags=8843<UP> mtu 1500\n\tether 2e:8e:ae:36:8e:17\n"
               "en5: flags=8863<UP> mtu 16000\n\tether ac:de:48:00:11:22\n")


def ioreg(u):
    return ('+-o J152fAP  <class IOPlatformExpertDevice>\n    {\n'
            '      "IOPlatformSerialNumber" = "C02XXXXXXXXX"\n'
            f'      "IOPlatformUUID" = "{u}"\n    }}\n')


class Run:
    def __init__(self, stdout):
        self.stdout = stdout


def mac(uuid_str, ifconfig=T2_IFCONFIG, node=EN5, fresh=True):
    """Point machine_id at a fake Mac: its ioreg UUID (None = ioreg fails), ifconfig, getnode()."""
    def run(cmd, *a, **k):
        if "ioreg" in cmd[0]:
            if uuid_str is None:
                raise subprocess.TimeoutExpired(cmd, 5)
            return Run(ioreg(uuid_str))
        return Run(ifconfig)
    subprocess.run = run
    MID.uuid.getnode = lambda: node
    MID.sys = SimpleNamespace(platform="darwin")
    if fresh:
        for f in (MID.MACHINE_ID_FILE, MID.HARDWARE_ID_FILE):
            if f.exists():
                f.unlink()


print("── TC-LICHW-0001 the hardware id on macOS")
mac(UUID_A)
check("hardware id = sha256(scalp-mid-v2|Darwin|IOPlatformUUID, lower-case)", MID.hardware_id() == hw(UUID_A))
MID.platform.machine = lambda: "arm64"
check("same id whatever platform.machine() says (Rosetta reports x86_64)", MID.hardware_id() == hw(UUID_A))
MID.platform.machine = lambda: "x86_64"
check("get_machine_id() now gives the hardware id", MID.get_machine_id() == hw(UUID_A))
check("the raw UUID is not in the id", UUID_A.lower() not in MID.get_machine_id())

print("── TC-LICHW-0002 two Intel T2 Macs share the en5 MAC")
mac(UUID_A)
a_legacy, a_hw = MID.legacy_machine_id(), MID.hardware_id()
mac(UUID_B)
b_legacy, b_hw = MID.legacy_machine_id(), MID.hardware_id()
check("old id: identical on both Macs (the hole)", a_legacy == b_legacy == LEGACY_T2, (a_legacy, b_legacy))
check("new id: different on each Mac", a_hw != b_hw and a_hw == hw(UUID_A) and b_hw == hw(UUID_B))

print("── TC-LICHW-0003 Windows reads MachineGuid from the registry")
_winreg_calls = []


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake_winreg = SimpleNamespace(
    HKEY_LOCAL_MACHINE="HKLM", KEY_READ=1, KEY_WOW64_64KEY=256,
    OpenKey=lambda root, path, res, access: (_winreg_calls.append((root, path, access)), _Key())[1],
    QueryValueEx=lambda k, name: ("D3ADB33F-1234-4321-ABCD-0123456789AB", 1) if name == "MachineGuid" else None)
sys.modules["winreg"] = fake_winreg
MID.sys = SimpleNamespace(platform="win32")
MID.platform.system = lambda: "Windows"
for f in (MID.HARDWARE_ID_FILE,):
    if f.exists():
        f.unlink()
check("Windows hardware id = sha256(scalp-mid-v2|Windows|MachineGuid)",
      MID.hardware_id() == hw("D3ADB33F-1234-4321-ABCD-0123456789AB", "Windows"))
check("… read from HKLM\\SOFTWARE\\Microsoft\\Cryptography, 64-bit view",
      _winreg_calls and _winreg_calls[-1][1] == r"SOFTWARE\Microsoft\Cryptography" and _winreg_calls[-1][2] & 256,
      _winreg_calls)
del sys.modules["winreg"]
MID.platform.system = lambda: "Darwin"

print("── TC-LICHW-0004 ioreg fails: cached hardware id, else the legacy id")
mac(UUID_A)
MID.hardware_id()                                  # a good read leaves the cache
mac(None, fresh=False)
check("live read fails, cache present → cached hardware id", MID.hardware_id() == hw(UUID_A))
check("… and get_machine_id() still sends it", MID.get_machine_id() == hw(UUID_A))
mac(None)
check("live read fails, no cache → no hardware id", MID.hardware_id() is None)
check("… get_machine_id() falls back to the legacy MAC id", MID.get_machine_id() == LEGACY_T2)
mac("not-a-uuid")
check("an unparsable ioreg answer counts as unreadable", MID.hardware_id() is None)

print("── TC-LICHW-0005 the ids sent to the server")
mac(UUID_A)
MID.MACHINE_ID_FILE.write_text(LEGACY_T2)          # cache from MACHINE_ID_STABLE: en5 kept though ap1 is first
check("machine_id = hardware id, legacy_machine_id = the bound en5 id",
      MID.device_ids() == {"machine_id": hw(UUID_A), "legacy_machine_id": LEGACY_T2}, MID.device_ids())
mac(None)
check("no hardware id → only machine_id = legacy, no legacy field",
      MID.device_ids() == {"machine_id": LEGACY_T2}, MID.device_ids())

print("── TC-LICHW-0006 local token check through the move")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey   # noqa: E402
from cryptography.hazmat.primitives import serialization                          # noqa: E402
import jwt                                                                         # noqa: E402

priv = Ed25519PrivateKey.generate()
LC.PUBLIC_KEY_PEM = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)


def token_for(machine_id):
    now = int(time.time())
    return jwt.encode({"sub": "TEST-KEY", "machine_id": machine_id, "tier": "STANDARD", "entitlements": {},
                       "iat": now, "exp": now + 4 * 86400, "server_time": now}, priv, algorithm="EdDSA")


def evaluate(token_mid):
    LC.TOKEN_FILE.write_text(token_for(token_mid))
    LC._evaluate_local()
    return LS.LICENSE_STATUS, LS.LICENSE_MESSAGE


LC.LICENSE_DIR.mkdir(parents=True, exist_ok=True)
LC.KEY_FILE.write_text("TEST-KEY")
mac(UUID_A)
check("token from before the move (bound to en5, en5 present) → VALID",
      evaluate(LEGACY_T2)[0] == LS.LicenseStatus.VALID, LS.LICENSE_MESSAGE)
check("… and the next heartbeat offers both ids for the move",
      MID.device_ids() == {"machine_id": hw(UUID_A), "legacy_machine_id": LEGACY_T2})
check("token after the move (bound to this Mac's hardware id) → VALID",
      evaluate(hw(UUID_A))[0] == LS.LicenseStatus.VALID, LS.LICENSE_MESSAGE)
mac(UUID_A, ifconfig="en0: flags=8863<UP> mtu 1500\n\tether 3c:22:fb:11:22:33\n", node=0x3c22fb112233)
check("hardware-id token stays VALID when the network interfaces change completely",
      evaluate(hw(UUID_A))[0] == LS.LicenseStatus.VALID, LS.LICENSE_MESSAGE)
mac(UUID_B)
st = evaluate(hw(UUID_A))
check("moved token + key copied to another T2 Mac → INVALID (same en5 MAC does not help)",
      st[0] == LS.LicenseStatus.INVALID and "different machine" in st[1], st)

print("── TC-LICHW-0007 post_device against old and new licence servers")
calls = []


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def server(old, pydantic1=False):
    def post(url, json=None, timeout=None):
        calls.append((url, dict(json)))
        if old and "legacy_machine_id" in json:
            if pydantic1:                      # extra field dropped, hardware id unknown
                return Resp(200, {"status": "machine_mismatch"})
            return Resp(422, {"detail": [{"type": "extra_forbidden"}]})
        return Resp(200, {"status": "test-noop"})   # an unknown status leaves the state alone
    return post


calls.clear(); LC.httpx.post = server(old=True)
r = LC.post_device("/heartbeat", {"key": "K", "machine_id": "H" * 64, "legacy_machine_id": "L" * 64}, 5)
check("old server: 422, then ONE retry in the old shape with the legacy id",
      r.status_code == 200 and len(calls) == 2 and calls[1][1] == {"key": "K", "machine_id": "L" * 64}, calls)
calls.clear(); LC.httpx.post = server(old=True, pydantic1=True)
r = LC.post_device("/heartbeat", {"key": "K", "machine_id": "H" * 64, "legacy_machine_id": "L" * 64}, 5)
check("old server on pydantic 1: machine_mismatch, then ONE retry with the legacy id",
      r.json()["status"] == "test-noop" and len(calls) == 2 and calls[1][1] == {"key": "K", "machine_id": "L" * 64},
      calls)
calls.clear(); LC.httpx.post = server(old=False)
LC.post_device("/heartbeat", {"key": "K", "machine_id": "H" * 64, "legacy_machine_id": "L" * 64}, 5)
check("new server: one call with both ids", len(calls) == 1 and calls[0][1]["legacy_machine_id"] == "L" * 64, calls)
calls.clear()
LC.httpx.post = lambda url, json=None, timeout=None: (calls.append(json), Resp(200, {"status": "machine_mismatch"}))[1]
r = LC.post_device("/heartbeat", {"key": "K", "machine_id": "H" * 64, "legacy_machine_id": "L" * 64}, 5)
check("new server refusing both ids: one retry, still machine_mismatch",
      len(calls) == 2 and r.json()["status"] == "machine_mismatch", calls)
calls.clear(); LC.httpx.post = lambda url, json=None, timeout=None: (calls.append(json), Resp(422, {}))[1]
LC.post_device("/heartbeat", {"key": "K", "machine_id": "L" * 64}, 5)
check("a 422 with no legacy id to fall back on is not retried", len(calls) == 1, calls)

print("── TC-LICHW-0008 heartbeat, activate and liveness send both ids")
mac(UUID_A)
MID.MACHINE_ID_FILE.write_text(LEGACY_T2)
calls.clear(); LC.httpx.post = server(old=False)
LC._heartbeat_sync(5)
LC.activate("test-key")
LP._telegram_target = lambda: None
LP._upcoming_holidays = lambda days=90: []
p = LP._payload()
LP._post_once(p)
want = {"machine_id": hw(UUID_A), "legacy_machine_id": LEGACY_T2}
got = [(u.rsplit("/", 1)[1], {k: b.get(k) for k in want}) for u, b in calls]
check("heartbeat → /heartbeat with both ids", ("heartbeat", want) in got, got)
check("activate → /activate with both ids", ("activate", want) in got, got)
check("liveness → /liveness with both ids", ("liveness", want) in got, got)

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL MACHINE ID HW CHECKS PASSED")
