# Soundtrack for the Scalp Terminal promo: 120 BPM, A minor, 20.0 s, one beat grid from t=0.
import numpy as np, wave, sys
SR = 48000; DUR = 20.0; N = int(SR * DUR)
BPM = 120; B = 60 / BPM            # 0.5 s beat
L = np.zeros(N); R = np.zeros(N)
rng = np.random.default_rng(7)
def hz(n): return 440 * 2 ** ((n - 69) / 12)
def idx(t): return int(round(t * SR))
def add(sig, t, gain=1.0, pan=0.0):
    i = idx(t); j = min(N, i + len(sig))
    if j <= i: return
    s = sig[: j - i] * gain
    L[i:j] += s * np.sqrt(0.5 * (1 - pan)) * 1.414 / 2 * 2 ** 0.5
    R[i:j] += s * np.sqrt(0.5 * (1 + pan)) * 1.414 / 2 * 2 ** 0.5
def env(n, a=0.005, d=0.3, curve=1.0):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d) ** curve
    return e
def lp(x, cut):
    # one-pole low-pass (cut in Hz, can be array)
    a = np.exp(-2 * np.pi * np.asarray(cut) / SR)
    y = np.zeros_like(x); z = 0.0
    if np.ndim(a) == 0:
        for k in range(len(x)): z = (1 - a) * x[k] + a * z; y[k] = z
    else:
        for k in range(len(x)): z = (1 - a[k]) * x[k] + a[k] * z; y[k] = z
    return y
def saw(f, n, det=0.0):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for d in (-det, 0, det):
        ph = (t * f * (1 + d)) % 1.0
        out += 2 * ph - 1
    return out / 3

# chords (bar = 2 s): Am F C G, repeating
CH = [[57, 60, 64], [53, 57, 60], [48, 55, 60], [55, 59, 62]]
ROOT = [45, 41, 48, 43]
def bar_of(t): return int(t // 2.0) % 4

# ── kick (from 2.0 to 17.0, four on the floor) + impacts
def kick(n=int(0.45 * SR), f0=150, f1=44):
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / 0.045)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t / 0.16) + 0.15 * rng.standard_normal(n) * np.exp(-t / 0.004)
kicks = [2.0 + i * B for i in range(int((17.0 - 2.0) / B))]
for kt in kicks:
    g = 0.55 if 14.0 <= kt < 17.0 and ((kt - 14.0) / B) % 2 else 0.9
    add(kick(), kt, g)
# sidechain-style duck curve
duck = np.ones(N)
for kt in kicks:
    i = idx(kt); n = idx(0.32); j = min(N, i + n)
    duck[i:j] = np.minimum(duck[i:j], 0.35 + 0.65 * (np.arange(j - i) / n) ** 0.7)

# ── clap on 2 & 4, hats on off-beats (2.0 → 17.0)
def noise_hit(n, d, hp_cut=None):
    x = rng.standard_normal(n)
    if hp_cut: x = x - lp(x, hp_cut)
    return x * env(n, 0.001, d)
for i in range(int((17.0 - 2.0) / B)):
    t = 2.0 + i * B
    if i % 2 == 1:
        add(noise_hit(int(0.25 * SR), 0.06, 1200), t, 0.16)
    add(noise_hit(int(0.08 * SR), 0.018, 7000), t + B / 2, 0.10, pan=0.25)

# ── bass: 8ths on root, from 2.0
for i in range(int((17.0 - 2.0) / (B / 2))):
    t = 2.0 + i * B / 2
    n = int(0.24 * SR); f = hz(ROOT[bar_of(t)] - 12 if i % 2 == 0 else ROOT[bar_of(t)])
    tt = np.arange(n) / SR
    s = 0.7 * np.sin(2 * np.pi * f * tt) + 0.25 * lp(saw(f, n), 600)
    add(s * env(n, 0.004, 0.12), t, 0.42)

# ── pad: whole piece, chord per bar, ducked; filtered open after 2.0
pad = np.zeros(N)
for bi in range(10):
    t0 = bi * 2.0; n = idx(2.0) + idx(0.6)
    ch = CH[bi % 4]
    s = np.zeros(n)
    for m in ch: s += saw(hz(m), n, 0.004) + 0.5 * saw(hz(m + 12), n, 0.006)
    tt = np.arange(n) / SR
    e = np.minimum(1, tt / 0.25) * np.minimum(1, np.maximum(0, (n / SR - tt) / 0.6))
    i = idx(t0); j = min(N, i + n)
    pad[i:j] += (s * e)[: j - i]
cut = np.where(np.arange(N) / SR < 2.0, 500 + 1300 * (np.arange(N) / SR / 2.0) ** 2, 2400)
cut = np.where(np.arange(N) / SR > 17.0, 2400 - 1600 * np.clip((np.arange(N) / SR - 17.0) / 3.0, 0, 1), cut)
pad = lp(pad, cut) * duck
L += pad * 0.10; R += pad * 0.10

# ── pluck arp: hook blips (counter) and the main hook 6.0 → 17.0
def pluck(f, n=int(0.32 * SR)):
    tt = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(2 * np.pi * 2 * f * tt) + 0.12 * np.sin(2 * np.pi * 3 * f * tt)
    return s * env(n, 0.002, 0.09)
arp = [0, 1, 2, 1, 2, 3, 2, 1]
for i in range(8):                    # hook: quiet ticking 8ths, rising
    t = i * B / 2
    ch = CH[0]; m = (ch + [ch[0] + 12])[i % 4] + 12
    add(pluck(hz(m)), t, 0.07 + 0.012 * i, pan=-0.2 + 0.05 * i)
for i in range(int((17.0 - 6.0) / (B / 2))):
    t = 6.0 + i * B / 2
    ch = CH[bar_of(t)]; tones = ch + [ch[0] + 12]
    m = tones[arp[i % 8] % 4] + 12
    add(pluck(hz(m)), t, 0.13, pan=0.3 if i % 2 else -0.3)

# ── riser into 2.0, whooshes into cuts, impacts
def riser(d):
    n = idx(d); tt = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = lp(x, 300 + 5000 * (tt / d) ** 2)
    return x * (tt / d) ** 2
add(riser(1.0), 1.0, 0.35)
for c in (6.0, 10.0, 14.0, 17.0):
    add(riser(0.45), c - 0.45, 0.16)
def impact():
    n = idx(1.6); tt = np.arange(n) / SR
    sub = np.sin(2 * np.pi * 45 * tt) * np.exp(-tt / 0.5)
    nz = lp(rng.standard_normal(n), 1800) * np.exp(-tt / 0.25)
    return sub + 0.5 * nz
add(impact(), 2.0, 0.55)
add(impact(), 17.0, 0.6)

# ── UI clicks on the cursor beats (scene 4), soft and in key (A)
def click(f=hz(81)):
    n = int(0.06 * SR); tt = np.arange(n) / SR
    return np.sin(2 * np.pi * f * tt) * np.exp(-tt / 0.012)
for k in range(6):
    add(click(), 10.75 + k * B + 0.11, 0.10, pan=0.15)

# ── message pop when the Telegram card lands (A5 → E6)
for k, (t, m) in enumerate([(14.5, 81), (14.625, 88)]):
    n = int(0.25 * SR); tt = np.arange(n) / SR
    add(np.sin(2 * np.pi * hz(m) * tt) * env(n, 0.003, 0.08), t, 0.16)

# ── outro: final Am(add9) chord bloom
n = idx(3.0); tt = np.arange(n) / SR
fin = sum(np.sin(2 * np.pi * hz(m) * tt) * (0.6 if m > 60 else 1) for m in (57, 64, 69, 71, 72))
fin = fin * np.minimum(1, tt / 0.05) * np.exp(-tt / 1.4)
add(fin, 17.0, 0.12)
for i, m in enumerate([69, 72, 76, 81]):
    add(pluck(hz(m)), 17.5 + i * B / 2, 0.10, pan=-0.3 + 0.2 * i)

# ── simple reverb (shared), then master
def reverb(x, d=1.6, mix=0.18):
    n = idx(d); tt = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-tt / (d / 5))
    ir = lp(ir, 3500); ir /= np.sqrt((ir ** 2).sum())
    y = np.convolve(x, ir)[: len(x)] if False else np.fft.irfft(np.fft.rfft(x, len(x) + n) * np.fft.rfft(ir, len(x) + n))[: len(x)]
    return x + mix * y
L = reverb(L); R = reverb(R)
# gentle fade at the very end + tiny fade-in
fade = np.ones(N); fn = idx(0.6); fade[-fn:] = np.linspace(1, 0, fn) ** 1.5
fi = idx(0.01); fade[:fi] = np.linspace(0, 1, fi)
L *= fade; R *= fade
# soft clip + normalise to -1 dBFS
st = np.stack([L, R], 1)
st = np.tanh(st * 1.2) / np.tanh(1.2)
st *= 10 ** (-1 / 20) / np.max(np.abs(st))
pcm = (st * 32767).astype(np.int16)
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
print("wrote", sys.argv[1], f"{DUR}s")
