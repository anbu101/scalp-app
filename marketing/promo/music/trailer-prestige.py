# Trailer A soundtrack — 24 s, 120 BPM grid (beat 0.5 s), A minor. Drone, clock ticks, braams on the
# cuts, string ostinato, taiko build, a trailer silence at 19.72–20.0, then the title hit.
import sys, numpy as np
from synthlib import *
D = 24.0; m = Mix(D, seed=11); r = m.rng; B = 0.5
# drone A1+E2, slow swell, whole piece (ducked by the silence)
n = int(D * SR); tt = np.arange(n) / SR
drone = lp(saw(hz(33), n, 0.004, 5) + 0.7 * saw(hz(40), n, 0.005, 5), 260 + 160 * np.sin(tt * 0.6) ** 2)
dg = np.minimum(1, tt / 1.6) * np.where((tt >= 19.72) & (tt < 20.0), 0, 1) * np.where(tt >= 20, np.exp(-(tt - 20) / 2.5), 1)
m.add(drone * dg, 0, 0.14)
# clock ticks on every beat 0 → 8, then 12 → 19.5 (tick/tock)
def tick(f): k = int(0.05 * SR); t = np.arange(k) / SR; return (np.sin(2 * np.pi * f * t) + 0.5 * r.standard_normal(k)) * np.exp(-t / 0.008)
for i in range(int(19.5 / B)):
    t = i * B
    if 8.0 <= t < 12.0: continue
    m.add(tick(2600 if i % 2 == 0 else 2100), t, 0.10 if t < 8 else 0.07, pan=0.3 if i % 2 else -0.3)
# braams + booms on the cuts, swells into them
for c, notes in [(2.0, [45, 52, 57]), (4.0, [41, 48, 53]), (8.0, [45, 52, 57, 60]), (12.0, [43, 50, 55]), (16.0, [41, 48, 53, 57])]:
    m.add(riser(r, 0.55, 3000), c - 0.55, 0.22)
    m.add(braam(notes), c, 0.32); m.add(boom(r), c, 0.55)
# string ostinato 8ths from 8.0 (A minor), 16ths from 16.0
CH = [[57, 60, 64], [53, 57, 60], [55, 59, 62], [52, 56, 59]]
def spic(f):
    k = int(0.16 * SR); t = np.arange(k) / SR
    return lp(saw(f, k, 0.004, 3), 2600) * np.minimum(1, t / 0.006) * np.exp(-t / 0.07)
pat = [0, 2, 1, 2, 0, 2, 1, 2]
t = 8.0; i = 0
while t < 19.7:
    step = B / 2 if t < 16.0 else B / 4
    ch = CH[int((t - 8.0) // 2.0) % 4]
    m.add(spic(hz(ch[pat[i % 8]] + 12)), t, 0.10 + (0.04 if t >= 16 else 0), pan=-0.35 if i % 2 else 0.35)
    m.add(spic(hz(ch[0])), t, 0.06)
    t += step; i += 1
# taiko: beats 1 & 3 from 12, every beat from 16, eighths in the last bar
def taiko():
    a = kick(r, int(0.7 * SR), 120, 55, 0.26); a[: int(0.3 * SR)] += 0.3 * noise_hit(r, int(0.3 * SR), 0.05, None, 1200); return a
t = 12.0
while t < 19.7:
    m.add(taiko(), t, 0.55 if t >= 16 else 0.42)
    t += (B if t >= 16 else 2 * B) if t < 18.0 else B / 2
m.add(riser(r, 1.9, 8000), 17.82, 0.35)
# title hit 20.0: braam stack + boom + shimmering high note
m.add(braam([33, 45, 52, 57, 64], d=4.0, bright=2200), 20.0, 0.45); m.add(boom(r, 3.5, 36), 20.0, 0.8)
for k2, (tn, mm) in enumerate([(20.7, 81), (21.2, 88), (21.7, 84)]):
    kk = int(1.6 * SR); t2 = np.arange(kk) / SR
    m.add((np.sin(2 * np.pi * hz(mm) * t2) + 0.3 * np.sin(4 * np.pi * hz(mm) * t2)) * np.exp(-t2 / 0.6), tn, 0.06)
m.write(sys.argv[1], fade_out=1.0, reverb=0.28, rev_d=2.4)
print("ok A")
