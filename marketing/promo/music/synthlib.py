import numpy as np, wave
SR = 48000
class Mix:
    def __init__(s, dur, seed=7):
        s.N = int(SR * dur); s.L = np.zeros(s.N); s.R = np.zeros(s.N); s.rng = np.random.default_rng(seed)
    def add(s, sig, t, gain=1.0, pan=0.0):
        i = int(round(t * SR)); j = min(s.N, i + len(sig))
        if j <= i or i < 0: return
        x = sig[: j - i] * gain
        s.L[i:j] += x * np.sqrt(1 - pan) ; s.R[i:j] += x * np.sqrt(1 + pan)
    def write(s, path, fade_out=0.6, reverb=0.18, rev_d=1.8, drive=1.2):
        L, R = s.L, s.R
        if reverb:
            n = int(rev_d * SR); tt = np.arange(n) / SR
            for ch in (0, 1):
                ir = lp(s.rng.standard_normal(n), 3500) * np.exp(-tt / (rev_d / 5)); ir /= np.sqrt((ir ** 2).sum())
                x = L if ch == 0 else R
                y = np.fft.irfft(np.fft.rfft(x, len(x) + n) * np.fft.rfft(ir, len(x) + n))[: len(x)]
                if ch == 0: L = x + reverb * y
                else: R = x + reverb * y
        fade = np.ones(s.N); fn = int(fade_out * SR); fade[-fn:] = np.linspace(1, 0, fn) ** 1.5
        fi = int(0.008 * SR); fade[:fi] = np.linspace(0, 1, fi)
        st = np.stack([L * fade, R * fade], 1)
        st = np.tanh(st * drive) / np.tanh(drive); st *= 10 ** (-1 / 20) / np.max(np.abs(st))
        with wave.open(path, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((st * 32767).astype(np.int16).tobytes())
def hz(n): return 440 * 2 ** ((n - 69) / 12)
def env(n, a=0.005, d=0.3):
    t = np.arange(n) / SR; return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)
def lp(x, cut):
    from scipy.signal import lfilter
    if np.ndim(cut) == 0:
        a = np.exp(-2 * np.pi * cut / SR); return lfilter([1 - a], [1, -a], x)
    a = np.exp(-2 * np.pi * np.asarray(cut) / SR); y = np.zeros_like(x); z = 0.0
    for k in range(len(x)): z = (1 - a[k]) * x[k] + a[k] * z; y[k] = z
    return y
def saw(f, n, det=0.0, voices=3):
    t = np.arange(n) / SR; out = np.zeros(n)
    ds = np.linspace(-det, det, voices) if voices > 1 else [0]
    for d in ds: out += 2 * ((t * f * (1 + d)) % 1.0) - 1
    return out / len(ds)
def kick(rng, n=int(0.5 * SR), f0=150, f1=42, d=0.18):
    t = np.arange(n) / SR; f = f1 + (f0 - f1) * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / d) + 0.15 * rng.standard_normal(n) * np.exp(-t / 0.004)
def noise_hit(rng, n, d, hp=None, lpc=None):
    x = rng.standard_normal(n)
    if hp: x = x - lp(x, hp)
    if lpc: x = lp(x, lpc)
    return x * env(n, 0.001, d)
def riser(rng, d, top=6000):
    n = int(d * SR); tt = np.arange(n) / SR
    x = rng.standard_normal(n); cut = 200 + top * (tt / d) ** 2
    return lp(x, cut) * (tt / d) ** 2
def boom(rng, d=2.0, f=40):
    n = int(d * SR); tt = np.arange(n) / SR
    fr = f + 30 * np.exp(-tt / 0.08)
    return np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.exp(-tt / (d / 3)) + 0.4 * lp(rng.standard_normal(n), 900) * np.exp(-tt / 0.3)
def braam(notes, d=2.6, bright=1400):
    n = int(d * SR); tt = np.arange(n) / SR; s = np.zeros(n)
    for m in notes: s += saw(hz(m), n, 0.006, 5)
    cut = 120 + bright * np.exp(-tt / 0.5) + 200
    s = lp(lp(s, cut), cut)
    return s * np.minimum(1, tt / 0.03) * np.exp(-tt / (d / 2.5))
