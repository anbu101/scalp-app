#!/usr/bin/env python3
"""Render a built video to MP4 (or a few stills).

  python3 scripts/render.py trailer-mass                 # frames → out/trailer-mass/trailer-mass.mp4 + .jpg
  python3 scripts/render.py trailer-mass --stills 1 6.5  # out/trailer-mass/stills/*.png + contact.png
  python3 scripts/render.py trailer-mass --check          # redaction check: no strategy ids, demo tag on app UI

Size, duration, poster time and warm-up times come from the video's META (window.VIDEO).
Audio: out/<name>/music.wav if present (promo.sh makes it from music/<name>.py).
Chrome: Playwright's bundled Chromium, or set PROMO_CHROME=/path/to/chrome.
"""
import argparse, asyncio, os, re, shutil, subprocess, sys
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent.parent

async def open_page(p, name):
    exe = os.environ.get("PROMO_CHROME")
    b = await p.chromium.launch(executable_path=exe or None, args=["--force-color-profile=srgb", "--font-render-hinting=none"])
    url = (HERE / "dist" / f"{name}.html").as_uri()
    pg = await b.new_page(viewport={"width": 1080, "height": 1080}, timezone_id="Asia/Kolkata")   # app clocks read IST (card "09:20 · …" lines)
    await pg.goto(url); await pg.wait_for_function("window.stageReady === true")
    meta = await pg.evaluate("window.VIDEO")
    await pg.set_viewport_size({"width": meta["width"], "height": meta["height"]})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    # warm-up: load fonts and let regions/chart widths get measured, then reload for a clean mount order
    for t in meta.get("warmup", []):
        await pg.evaluate(f"window.renderAt({t})")
    await pg.evaluate("document.fonts.ready")
    await pg.goto(url); await pg.wait_for_function("window.stageReady === true")
    await pg.evaluate("document.fonts.ready")
    return b, pg, meta, errs

# Real strategy ids AND their admin display names must never reach the screen (codenames only,
# isAdminUi = false). Names are read from the app's own displayNames.js so new strategies are covered.
def _real_names():
    src = (HERE.parent.parent / "frontend/src/strategies/displayNames.js").read_text(encoding="utf-8")
    pairs = re.findall(r"(\b[A-Z]+_V\d+)\s*:\s*\{\s*real:\s*\"([^\"]+)\"", src)
    words = {sid for sid, _ in pairs} | {real for _, real in pairs}
    if len(pairs) < 5: raise SystemExit("could not read strategy names from displayNames.js — update _real_names()")
    return re.compile(r"(?<![\w])(" + "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True)) + r")(?![\w])")
ID_RE = None
APP_UI = "Traded today|MTM today|Fleet MTM"

async def b_check(pg, meta, errs):
    global ID_RE
    ID_RE = _real_names()
    bad, ui_no_tag, n = [], [], 0
    t = 0.0
    while t < meta["duration"]:
        await pg.evaluate(f"window.renderAt({t})")
        txt = await pg.evaluate("document.body.innerText")
        if ID_RE.search(txt): bad.append((t, ID_RE.search(txt).group(0)))
        if re.search(APP_UI, txt) and "demo data" not in txt: ui_no_tag.append(t)
        t = round(t + 0.5, 2); n += 1
    print(f"check: {n} samples | strategy ids on screen: {bad or 'none'} | app UI without demo tag: {ui_no_tag or 'none'} | page errors: {errs or 'none'}")
    if bad or ui_no_tag or errs: sys.exit(1)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name"); ap.add_argument("--stills", nargs="*", type=float); ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    out = HERE / "out" / a.name; out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b, pg, meta, errs = await open_page(p, a.name)
        if a.check:
            await b_check(pg, meta, errs); await b.close(); return
        if a.stills is not None:
            sd = out / "stills"; shutil.rmtree(sd, ignore_errors=True); sd.mkdir()
            for t in sorted(a.stills or [meta["poster"]]):
                await pg.evaluate(f"window.renderAt({t})")
                await pg.screenshot(path=str(sd / f"{t:06.2f}.png"))
            await b.close()
            cols = min(6, len(list(sd.glob("*.png"))))
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-pattern_type", "glob", "-i", str(sd / "*.png"),
                            "-vf", f"scale={'360' if meta['height'] > meta['width'] else '640'}:-1,tile={cols}x{-(-len(list(sd.glob('*.png'))) // cols)}",
                            "-frames:v", "1", str(sd / "contact.png")], check=True)
            print("stills:", sd, "| page errors:", errs[:3]); return
        fd = out / "frames"; shutil.rmtree(fd, ignore_errors=True); fd.mkdir()
        fps = meta.get("fps", 30); n = int(round(meta["duration"] * fps))
        for i in range(n):
            await pg.evaluate(f"window.renderAt({i / fps})")
            await pg.screenshot(path=str(fd / f"f{i:04d}.png"))
            if i % 150 == 0: print(f"  frame {i}/{n}", flush=True)
        await b.close()
    if errs: print("page errors:", errs[:5]); sys.exit(1)
    # poster: the META.poster frame → .jpg, and baked in as frame 0 so every platform's thumbnail shows it
    pf = fd / f"f{int(round(meta['poster'] * fps)):04d}.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(pf), "-q:v", "2", str(out / f"{a.name}.jpg")], check=True)
    shutil.copy(pf, fd / "f0000.png")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-start_number", "0", "-i", str(fd / "f%04d.png")]
    wav = out / "music.wav"
    if wav.exists(): cmd += ["-i", str(wav), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / f"{a.name}.mp4")]
    subprocess.run(cmd, check=True)
    shutil.rmtree(fd)
    print("done:", out / f"{a.name}.mp4")

asyncio.run(main())
