# backend/app/license/test_machine_id_stable.py
# ── MACHINE_ID_STABLE_20261119 ── a licence stays valid when the OS lists the
# network interfaces in a new order (Dhivakar's Mac after a battery-empty
# standby, 19 Nov 2026); a copied licence still fails on another machine.
#   cd backend && PYTHONPATH=$PWD python3 app/license/test_machine_id_stable.py
import hashlib, os, subprocess, sys, tempfile, time
from types import SimpleNamespace
os.environ["HOME"] = tempfile.mkdtemp(prefix="mid_stable_")
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

MID.platform.system = lambda: "Darwin"
MID.platform.machine = lambda: "x86_64"


def fp(node: int) -> str:
    """The licence fingerprint formula, written out independently of machine_id.py."""
    return hashlib.sha256(f"Darwin|x86_64|{hex(node)}".encode()).hexdigest()

# Dhivakar's Mac at 16:14 on 19 Nov: `ifconfig -a`, in the order it printed.
HIS_IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
ap1: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 2e:8e:ae:36:8e:17
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 1e:bf:74:07:53:49
awdl0: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST> mtu 1500
\tether 62:d4:f2:e4:5c:0f
en1: flags=8963<UP,BROADCAST,SMART,RUNNING,PROMISC,SIMPLEX,MULTICAST> mtu 1500
\tether 82:c1:07:c1:40:01
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 16000
\tether ac:de:48:00:11:22
"""
AP1, EN5 = 0x2e8eae368e17, 0xacde48001122
ID_AP1, ID_EN5 = fp(AP1), fp(EN5)
OTHER_MAC_IFCONFIG = "en0: flags=8863<UP> mtu 1500\n\tether 3c:22:fb:11:22:33\n"


class Run:
    def __init__(self, stdout):
        self.stdout = stdout


def machine(ifconfig, node, cached=None):
    """Point machine_id at a fake machine: its ifconfig text, its getnode()."""
    subprocess.run = lambda *a, **k: Run(ifconfig)
    MID.uuid.getnode = lambda: node
    MID.sys = SimpleNamespace(platform="darwin")
    if MID.MACHINE_ID_FILE.exists():
        MID.MACHINE_ID_FILE.unlink()
    if cached:
        MID.LICENSE_DIR.mkdir(parents=True, exist_ok=True)
        MID.MACHINE_ID_FILE.write_text(cached)


def cache():
    return MID.MACHINE_ID_FILE.read_text().strip() if MID.MACHINE_ID_FILE.exists() else None


print("── TC-LICMID-0001 parsing")
machine(HIS_IFCONFIG, AP1)
macs = MID._local_macs() if hasattr(MID, "_local_macs") else None
check("all five MACs read from ifconfig, ap1 first, en5 last",
      macs == ["2e:8e:ae:36:8e:17", "1e:bf:74:07:53:49", "62:d4:f2:e4:5c:0f", "82:c1:07:c1:40:01",
               "ac:de:48:00:11:22"], macs)
check("each one maps to the same fingerprint formula as getnode()",
      hasattr(MID, "_present_ids") and ID_EN5 in MID._present_ids())

print("── TC-LICMID-0002 his case: token bound to en5, getnode() now returns ap1")
machine(HIS_IFCONFIG, AP1, cached=ID_AP1)
check("old behaviour reproduced: getnode() alone gives the ap1 fingerprint", MID._compute() == ID_AP1)
check("with the token's bound id (en5 present) → the licensed en5 id", MID.get_machine_id(prefer=ID_EN5) == ID_EN5)
check("… and the cache file now holds the en5 id", cache() == ID_EN5)
check("later calls without a token (heartbeat, activate) also send en5", MID.get_machine_id() == ID_EN5)

print("── TC-LICMID-0003 interface order flips, no token at hand")
machine(HIS_IFCONFIG, AP1, cached=ID_EN5)
check("cached en5 id kept while en5 is on the machine", MID.get_machine_id() == ID_EN5 and cache() == ID_EN5)

print("── TC-LICMID-0004 licence copied to another machine")
machine(OTHER_MAC_IFCONFIG, 0x3c22fb112233, cached=ID_EN5)
own = fp(0x3c22fb112233)
check("token's en5 id is NOT taken (no en5 here) → own fingerprint", MID.get_machine_id(prefer=ID_EN5) == own)
check("copied cache file is replaced by this machine's own id", cache() == own)

print("── TC-LICMID-0005 real hardware change / first launch / no readable MAC")
machine(OTHER_MAC_IFCONFIG, 0x3c22fb112233, cached="f" * 64)
check("cached id whose MAC is gone → recomputed, as before", MID.get_machine_id() == own and cache() == own)
machine(HIS_IFCONFIG, AP1)
check("first launch, no cache, no token → getnode() fingerprint, as before",
      MID.get_machine_id() == ID_AP1 and cache() == ID_AP1)
machine("", 0x010000000001, cached="e" * 64)
subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("no ifconfig"))
check("random getnode() and ifconfig failing → cached id, as before", MID.get_machine_id() == "e" * 64)

print("── TC-LICMID-0006 end to end: _evaluate_local with a signed token")
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


LC.LICENSE_DIR.mkdir(parents=True, exist_ok=True)
LC.KEY_FILE.write_text("TEST-KEY")
LC.TOKEN_FILE.write_text(token_for(ID_EN5))
machine(HIS_IFCONFIG, AP1, cached=ID_AP1)
LC._evaluate_local()
check("his Mac after the standby: licence VALID (was INVALID 'bound to a different machine')",
      LS.LICENSE_STATUS == LS.LicenseStatus.VALID, (LS.LICENSE_STATUS, LS.LICENSE_MESSAGE))
check("… and the heartbeat would send the bound en5 id", MID.get_machine_id() == ID_EN5)
machine(OTHER_MAC_IFCONFIG, 0x3c22fb112233)
LC._evaluate_local()
check("same key + token copied to another machine: still INVALID",
      LS.LICENSE_STATUS == LS.LicenseStatus.INVALID and "different machine" in LS.LICENSE_MESSAGE,
      (LS.LICENSE_STATUS, LS.LICENSE_MESSAGE))

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL MACHINE ID STABLE CHECKS PASSED")
