# Soundtrack for reel-arcade (vertical feature reel), 26 s, 120 BPM, A minor — punchier than the landscape
# cut: distorted four-on-the-floor kick, clap + snare, 16th hats, pumping off-beat bass, sidechained
# supersaw chords, a square-wave chiptune lead (doubled from 14 s), half-beat silences before the drops
# at 2 / 10 / 18 s, and an impact + stab on every headline slam of the video.
# fence: PROMO_ARCADE_20261120
import sys, numpy as np
from synthlib import *
D = 26.0; m = Mix(D, seed=11); r = m.rng; B = 0.5
GROOVE = (2.0, 22.0)
GAPS = [(1.75, 2.0), (9.75, 10.0), (17.75, 18.0)]          # pre-drop silences
def gap(t): return any(a <= t < b for a, b in GAPS)
CH = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]   # Am F C G, a bar (2 s) each
ROOT = [45, 41, 48, 43]
def bar(t): return int(t // 2.0) % 4
def sq(f, n, duty=0.5):
    tt = np.arange(n) / SR; ph = (tt * f) % 1.0
    return np.where(ph < duty, 1.0, -1.0)

# ── kick: hard, a touch of drive; quarter notes through the groove
def hard_kick():
    k = kick(r, int(0.42 * SR), 190, 46, 0.17)
    return np.tanh(2.2 * k)
kicks = [GROOVE[0] + i * B for i in range(int((GROOVE[1] - GROOVE[0]) / B))]
for kt in kicks:
    if not gap(kt): m.add(hard_kick(), kt, 0.95)
# sidechain curve from the kicks
N = m.N; duck = np.ones(N)
for kt in kicks:
    if gap(kt): continue
    i = int(kt * SR); n = int(0.3 * SR); j = min(N, i + n)
    duck[i:j] = np.minimum(duck[i:j], 0.2 + 0.8 * (np.arange(j - i) / n) ** 0.6)

# ── clap + snare on 2 & 4, 16th hats with accents, open hat on the off-beats
t = GROOVE[0]; i = 0
while t < GROOVE[1]:
    if not gap(t):
        s16 = i % 8
        if s16 == 4:
            m.add(noise_hit(r, int(0.3 * SR), 0.09, 900), t, 0.34)
            m.add(kick(r, int(0.2 * SR), 240, 180, 0.05), t, 0.25)
        if s16 == 2 or s16 == 6: m.add(noise_hit(r, int(0.18 * SR), 0.05, 6000), t, 0.09, pan=-0.25)
        m.add(noise_hit(r, int(0.04 * SR), 0.01, 8000), t, 0.10 if i % 2 else 0.05, pan=0.3)
    t += B / 4; i += 1
# hook: tight hat ticks building, then a 4-hit tom roll into 2.0
for k in range(12):
    m.add(noise_hit(r, int(0.04 * SR), 0.01, 8000), 0.25 + k * B / 4, 0.03 + 0.006 * k, pan=0.2)
for k, tt0 in enumerate([1.5, 1.625, 1.75 - 0.0001]):
    m.add(kick(r, int(0.25 * SR), 280 - 30 * k, 130, 0.08), tt0, 0.3 + 0.1 * k)

# ── bass: pumping off-beat saw + sub on the beat
t = GROOVE[0]
while t < GROOVE[1]:
    if not gap(t):
        f = hz(ROOT[bar(t)] - 12)
        n = int(0.22 * SR); tt = np.arange(n) / SR
        m.add(np.tanh(1.8 * lp(saw(f * 2, n, 0.01, 3), 900)) * env(n, 0.004, 0.12), t + B / 2, 0.30)
        nn = int(0.3 * SR); ts = np.arange(nn) / SR
        m.add(np.sin(2 * np.pi * f * ts) * env(nn, 0.004, 0.18), t, 0.32)
    t += B

# ── chords: supersaw, sidechained, filter opening across the piece
pad = np.zeros(N)
for bi in range(13):
    t0 = bi * 2.0; n = int(2.2 * SR); tt = np.arange(n) / SR
    s = sum(saw(hz(x), n, 0.012, 5) + 0.5 * saw(hz(x + 12), n, 0.015, 5) for x in CH[bi % 4])
    e = np.minimum(1, tt / 0.03) * np.minimum(1, np.maximum(0, (2.2 - tt) / 0.2))
    i0 = int(t0 * SR); j = min(N, i0 + n); pad[i0:j] += (s * e)[: j - i0]
tsec = np.arange(N) / SR
cut = np.clip(600 + 2600 * (tsec - 2) / 20, 500, 3200)
cut = np.where(tsec < 2.0, 300 + 900 * (tsec / 2) ** 2, cut)
pad = lp(pad, cut) * duck
for a, b in GAPS: pad[int(a * SR):int(b * SR)] *= 0.08
m.L += pad * 0.075; m.R += pad * 0.075

# ── chiptune lead: square arp on the chord tones, 16ths; second voice an octave up from 14 s
ARP = [0, 1, 2, 3, 2, 1, 2, 3]
t = 6.0; i = 0
while t < GROOVE[1]:
    if not gap(t):
        ch = CH[bar(t)]; tones = ch + [ch[0] + 12]
        note = tones[ARP[i % 8] % 4] + 12
        n = int(0.12 * SR)
        v = lp(sq(hz(note), n, 0.25), 5000) * env(n, 0.002, 0.06)
        m.add(v, t, 0.07, pan=-0.3 if i % 2 else 0.3)
        if t >= 14.0: m.add(lp(sq(hz(note + 12), n, 0.5), 6000) * env(n, 0.002, 0.05), t + B / 8, 0.04, pan=0.3 if i % 2 else -0.3)
    t += B / 4; i += 1

# ── hits on every headline slam (times from reel-arcade.jsx) + risers into each section
def stab(notes, k=int(0.4 * SR)):
    tt = np.arange(k) / SR; s = sum(saw(hz(x), k, 0.01, 5) for x in notes)
    return lp(s, 3400) * np.minimum(1, tt / 0.004) * np.exp(-tt / 0.12)
BIG = [0.5, 2.1, 6.1, 10.0, 14.0, 18.0, 22.05]
SMALL = [0.75, 2.35, 6.35, 7.0, 7.5, 8.0, 10.25, 10.5, 14.5, 18.25, 22.2, 22.45]
for h in BIG:
    m.add(boom(r, 2.0, 40), h, 0.32)
    m.add(noise_hit(r, int(0.6 * SR), 0.1, 300, 6000), h, 0.3)
    m.add(stab([57, 60, 64, 69]), h, 0.16)
for h in SMALL:
    m.add(noise_hit(r, int(0.3 * SR), 0.05, 500, 7000), h, 0.16)
    m.add(stab([69, 76]), h, 0.07)
for c in (2.0, 6.0, 10.0, 14.0, 18.0, 22.0):
    m.add(riser(r, 1.0 if c in (2.0, 10.0, 18.0) else 0.6, 8000), c - (1.0 if c in (2.0, 10.0, 18.0) else 0.6), 0.22)

# ── UI blips: alerts, year ticks, fee chips, ladder, chat messages
def blip(f, d=0.07):
    n = int(d * SR); tt = np.arange(n) / SR
    return lp(sq(f, n, 0.25), 7000) * np.exp(-tt / 0.02)
for k in range(6): m.add(blip(hz(81 + [0, 3, 5, 7, 12, 0][k])), 2.5 + k * B, 0.09, pan=0.2)
for k in range(7): m.add(blip(hz(76 + 2 * k), 0.05), 10.6 + k * 0.16, 0.07)
for k in range(6): m.add(blip(hz(88), 0.05), 12.0 + k * 0.18, 0.06, pan=-0.2)
for k in range(8): m.add(blip(hz(69 + [0, 3, 5, 0, 7, 10, 12, 12][k]), 0.1), 14.9 + k * 0.12, 0.08)
for tt0, f in [(18.5, 81), (19.0, 84), (19.5, 76), (20.2, 81), (20.325, 88)]:
    m.add(blip(hz(f), 0.12), tt0, 0.09)

# ── outro: big chord bloom + chip arpeggio up
n = int(4.0 * SR); tt = np.arange(n) / SR
fin = lp(sum(saw(hz(x), n, 0.008, 5) for x in (45, 52, 57, 60, 64, 69)), 2200) * np.minimum(1, tt / 0.02) * np.exp(-tt / 1.6)
m.add(fin, 22.0, 0.14)
for i, x in enumerate([69, 72, 76, 81, 84]):
    m.add(blip(hz(x), 0.14), 22.5 + i * B / 2, 0.08, pan=-0.3 + 0.15 * i)

m.write(sys.argv[1], fade_out=1.0, reverb=0.14, rev_d=1.4, drive=1.15)
# loudness: the dense groove lands near -8.6 LUFS once peak-normalised; trim to about -12 LUFS
import wave
with wave.open(sys.argv[1], "rb") as w: pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((pcm * 10 ** (-3.5 / 20)).astype(np.int16).tobytes())
print("ok reel-arcade")
