# Shared soundtrack of the Arcade promos (promo-arcade landscape, reel-arcade vertical): the Mass trailer's
# sound (music/trailer-mass.py: syncopated kick, toms, claps, 16th shaker, 808 with slides, saw riff, brass
# stabs + booms, the app's own target-hit whistle), 26 s, 120 BPM, A minor, on the videos' section bars
# 2 / 6 / 10 / 14 / 18 / 22 s with pre-drop gaps before 2 / 10 / 18 s. Each cut passes its own HITS (its
# headline slams); the UI ticks and whistles follow lib/arcade.jsx, which both cuts share.
# fence: PROMO_ARCADE_20261120
import wave, numpy as np
from synthlib import *

def build(path, HITS, trim_db=-2.0, D=26.0):
    m = Mix(D, seed=5); r = m.rng; B = 0.5
    GROOVE = (2.0, 22.0)
    GAPS = [(1.75, 2.0), (9.75, 10.0), (17.75, 18.0)]
    def gap(t): return any(a <= t < b for a, b in GAPS)
    ROOT = [45, 41, 43, 40]

    # ── groove 2 → 22: the Mass trailer's syncopated kick, toms, claps on 2 & 4, 16th shaker
    kpat = [1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0]       # 16ths over a bar (2 s)
    t = GROOVE[0]; i = 0
    while t < GROOVE[1] - 1e-9:
        s16 = i % 16
        if not gap(t):
            if kpat[s16]: m.add(kick(r, f0=170, f1=48, d=0.16), t, 0.85)
            if s16 in (4, 12): m.add(noise_hit(r, int(0.25 * SR), 0.07, 1000), t, 0.30)
            if s16 in (14, 15) and int((t - 2) // 2) % 2 == 1: m.add(kick(r, int(0.3 * SR), 260, 140, 0.09), t, 0.32, pan=0.3)   # tom fill every other bar
            m.add(noise_hit(r, int(0.05 * SR), 0.012, 7000), t, 0.07 if i % 2 else 0.11, pan=0.3)
        t += B / 4; i += 1
    # hook 0 → 1.75: half-time toms
    for k in range(4):
        m.add(kick(r, int(0.4 * SR), 220, 90, 0.14), k * B, 0.4)
    # ── 808 bass, 8ths, with a slide into each note
    t = GROOVE[0]
    while t < GROOVE[1] - 1e-9:
        if not gap(t):
            root = ROOT[int((t - 2.0) // 2.0) % 4]
            k = int(0.24 * SR); tt = np.arange(k) / SR
            f = hz(root - 12) * (1 + 0.5 * np.exp(-tt / 0.02))
            m.add(np.tanh(2.5 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * env(k, 0.003, 0.14), t, 0.22)
        t += B / 2
    # ── riff: bright saw, A minor pentatonic, 6 → 22
    RIFF = [69, 72, 74, 76, 74, 72, 69, 67]
    t = 6.0; i = 0
    while t < GROOVE[1] - 1e-9:
        if not gap(t) and i % 8 not in (3, 7):
            k = int(0.2 * SR)
            note = RIFF[i % 8] + (0 if int((t - 6) // 2) % 2 == 0 else -2)
            m.add(lp(saw(hz(note), k, 0.01, 5), 3800) * env(k, 0.004, 0.09), t, 0.08, pan=-0.25 if i % 2 else 0.25)
        t += B / 2; i += 1

    # ── slams: boom + noise crack + brass stab, exactly on the video's hits
    def stab(notes, k=int(0.35 * SR)):
        tt = np.arange(k) / SR; s = sum(saw(hz(n), k, 0.008, 5) for n in notes)
        return lp(s, 3000) * np.minimum(1, tt / 0.005) * np.exp(-tt / 0.11)
    for h, kk in HITS:
        m.add(boom(r, 1.0 + kk, 42), h, 0.2 * kk)
        m.add(noise_hit(r, int(0.5 * SR), 0.09, 300, 5000), h, 0.25 * kk)
        if kk >= 1: m.add(stab([57, 60, 64, 69]), h, 0.16)
        else: m.add(stab([69, 72]), h, 0.07)
    for c in (2.0, 6.0, 10.0, 14.0, 18.0, 22.0):
        d = 1.0 if c in (2.0, 10.0, 18.0) else 0.6
        m.add(riser(r, d, 7000), c - d, 0.20)

    # ── small ticks on the UI beats: alerts, year counter, fee chips, lot bars, chat bubbles
    def tick(f, d=0.06):
        n = int(d * SR); tt = np.arange(n) / SR
        return np.sin(2 * np.pi * f * tt) * np.exp(-tt / 0.015)
    for at in (2.5, 3.0, 3.5, 4.0, 5.0): m.add(tick(hz(81)), at, 0.10, pan=0.2)          # alerts (4.5 is the whistle)
    for k in range(7): m.add(tick(hz(76 + 2 * k)), 10.75 + k * 0.125, 0.08)              # 2020 → 2026
    for k in range(6): m.add(tick(hz(88)), 12.0 + k * 0.25, 0.08, pan=-0.2)              # fee chips
    for k in range(8): m.add(tick(hz(69 + [0, 3, 5, 0, 7, 10, 12, 12][k]), 0.1), 15.0 + k * 0.125, 0.09)   # lot bars
    for at in (18.5, 19.5, 20.0): m.add(tick(hz(84), 0.1), at, 0.10)                       # chat bubbles, day card

    # ── the app's whistle (NotificationProvider._playWhistle): on each TARGET HIT the video shows
    def whistle():
        out = np.zeros(int(0.7 * SR))
        for at, f0, f1, dur in [(0.00, 1750, 2650, 0.24), (0.32, 1750, 2750, 0.30)]:
            k = int(dur * SR); tt = np.arange(k) / SR
            rise = np.where(tt < dur * 0.7, f0 * (f1 / f0) ** (tt / (dur * 0.7)), f1 * (0.92) ** ((tt - dur * 0.7) / (dur * 0.3)))
            f = rise + 40 * np.sin(2 * np.pi * 28 * tt)
            g = np.where(tt < 0.02, 0.0001 * (0.28 / 0.0001) ** (tt / 0.02), np.where(tt < dur * 0.6, 0.28, 0.28 * (0.0001 / 0.28) ** ((tt - dur * 0.6) / (dur * 0.4))))
            i0 = int(at * SR); out[i0:i0 + k] += np.sin(2 * np.pi * np.cumsum(f) / SR) * g
        return out
    m.add(whistle(), 4.5, 1.3)     # Outrider · TARGET HIT alert
    m.add(whistle(), 19.0, 1.3)    # Telegram: TARGET HIT bubble

    # ── title: rumble + held chord under the end card
    k = int(4.0 * SR); tt = np.arange(k) / SR
    m.add(lp(sum(saw(hz(n), k, 0.006, 5) for n in (45, 52, 57, 60, 64)), 1800) * np.minimum(1, tt / 0.02) * np.exp(-tt / 1.4), 22.0, 0.16)

    m.write(path, fade_out=1.0, reverb=0.16, rev_d=1.6, drive=1.0)
    # loudness trim to the -11…-14 LUFS window (peak-normalised mixes of this groove land louder)
    TRIM_DB = trim_db
    with wave.open(path, "rb") as w: pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((pcm * 10 ** (TRIM_DB / 20)).astype(np.int16).tobytes())
