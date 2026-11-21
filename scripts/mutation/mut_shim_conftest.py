# conftest.py for a mutation workspace — ── MUTMUT_20261117 ── (TD-043)
# Copied into <work>/<module>/tests/ by scripts/mutation/mutmut_run.py.
# Mutation runs are offline: any socket connect that is not loopback fails
# at once, so no mutant can ever reach Kite, Telegram or a licence server.
import socket

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _allowed(addr):
    if not isinstance(addr, tuple):          # AF_UNIX path
        return True
    return str(addr[0]) in _LOOPBACK


def _connect(self, addr):
    if not _allowed(addr):
        raise OSError(f"MUTMUT_20261117: network blocked in mutation runs ({addr[0]})")
    return _real_connect(self, addr)


def _connect_ex(self, addr):
    if not _allowed(addr):
        return 111                            # ECONNREFUSED
    return _real_connect_ex(self, addr)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
