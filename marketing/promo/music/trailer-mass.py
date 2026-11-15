# Trailer B soundtrack — 20 s, 120 BPM, A minor, punchy "mass" groove. Slams on every on-screen hit,
# pre-drop gap 12.3–12.5, then the app's own target-hit whistle (NotificationProvider._playWhistle).
import sys, numpy as np
from synthlib import *
D = 20.0; m = Mix(D, seed=5); r = m.rng; B = 0.5
HITS = [(0.05, 1), (1.0, 1), (2.0, 1.2), (2.5, .5), (3.0, .5), (6.0, 1.3), (6.5, .8), (7.0, .6), (9.0, 1), (9.5, 1.1),
        (10.0, .35), (10.25, .35), (10.5, .35), (10.75, .35), (11.0, .35), (11.25, .35), (12.5, 1.2), (14.0, 1.2), (14.5, .8), (17.0, 1.5)]
ROOT = [45, 41, 43, 40]
def gap(t): return 12.3 <= t < 12.5 or t >= 17.0
# groove 2.0 → 17.0: syncopated kick, toms, claps on 2 & 4, 16th shaker
kpat = [1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0]       # 16ths over a bar (2 s)
t = 2.0; i = 0
while t < 17.0:
    s16 = i % 16
    if not gap(t):
        if kpat[s16]: m.add(kick(r, f0=170, f1=48, d=0.16), t, 0.85)
        if s16 in (4, 12): m.add(noise_hit(r, int(0.25 * SR), 0.07, 1000), t, 0.30)
        if s16 in (14, 15): m.add(kick(r, int(0.3 * SR), 260, 140, 0.09), t, 0.32, pan=0.3)   # tom fill
        m.add(noise_hit(r, int(0.05 * SR), 0.012, 7000), t, 0.07 if i % 2 else 0.11, pan=0.3)
    t += B / 4; i += 1
# hook bars 0–2: half-time toms + claps
for k in range(4):
    m.add(kick(r, int(0.4 * SR), 220, 90, 0.14), k * B + 0.05, 0.4)
# 808 bass, 8ths, with a slide into each bar
t = 2.0; i = 0
while t < 17.0:
    if not gap(t):
        root = ROOT[int((t - 2.0) // 2.0) % 4]
        k = int(0.24 * SR); tt = np.arange(k) / SR
        f = hz(root - 12) * (1 + 0.5 * np.exp(-tt / 0.02))
        m.add(np.tanh(2.5 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * env(k, 0.003, 0.14), t, 0.22)
    t += B / 2; i += 1
# riff: bright saw, A minor pentatonic, 6.0 → 17.0
RIFF = [69, 72, 74, 76, 74, 72, 69, 67]
t = 6.0; i = 0
while t < 17.0:
    if not gap(t) and i % 8 not in (3, 7):
        k = int(0.2 * SR); tt = np.arange(k) / SR
        note = RIFF[i % 8] + (0 if int((t - 6) // 2) % 2 == 0 else -2)
        m.add(lp(saw(hz(note), k, 0.01, 5), 3800) * env(k, 0.004, 0.09), t, 0.08, pan=-0.25 if i % 2 else 0.25)
    t += B / 2; i += 1
# slams: impact + brass stab chord
def stab(notes, k=int(0.35 * SR)):
    tt = np.arange(k) / SR; s = sum(saw(hz(n), k, 0.008, 5) for n in notes)
    return lp(s, 3000) * np.minimum(1, tt / 0.005) * np.exp(-tt / 0.11)
for h, kk in HITS:
    m.add(boom(r, 1.0 + kk, 42), h, 0.2 * kk)
    m.add(noise_hit(r, int(0.5 * SR), 0.09, 300, 5000), h, 0.25 * kk)
    if kk >= 1: m.add(stab([57, 60, 64, 69]), h, 0.16)
    else: m.add(stab([69, 72]), h, 0.07)
for c in (2.0, 6.0, 9.0, 14.0, 17.0):
    m.add(riser(r, 0.6, 7000), c - 0.6, 0.20)
# the app's whistle: two rising sine sweeps with 28 Hz vibrato (±40 Hz), gain 0.28 envelope
def whistle():
    out = np.zeros(int(0.7 * SR))
    for at, f0, f1, dur in [(0.00, 1750, 2650, 0.24), (0.32, 1750, 2750, 0.30)]:
        k = int(dur * SR); tt = np.arange(k) / SR
        rise = np.where(tt < dur * 0.7, f0 * (f1 / f0) ** (tt / (dur * 0.7)), f1 * (0.92) ** ((tt - dur * 0.7) / (dur * 0.3)))
        f = rise + 40 * np.sin(2 * np.pi * 28 * tt)
        g = np.where(tt < 0.02, 0.0001 * (0.28 / 0.0001) ** (tt / 0.02), np.where(tt < dur * 0.6, 0.28, 0.28 * (0.0001 / 0.28) ** ((tt - dur * 0.6) / (dur * 0.4))))
        i0 = int(at * SR); out[i0:i0 + k] += np.sin(2 * np.pi * np.cumsum(f) / SR) * g
    return out
m.add(whistle(), 12.5, 1.6)
m.add(whistle(), 13.25, 1.0, pan=0.2)
# title: rumble + held chord
k = int(3.0 * SR); tt = np.arange(k) / SR
m.add(lp(sum(saw(hz(n), k, 0.006, 5) for n in (45, 52, 57, 60, 64)), 1800) * np.minimum(1, tt / 0.02) * np.exp(-tt / 1.2), 17.0, 0.16)
m.write(sys.argv[1], fade_out=0.8, reverb=0.16, rev_d=1.6, drive=1.0)
print("ok B")
