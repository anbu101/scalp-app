# backend/app/utils/mpl_fonts.py — ── TEST_FIXES_20261126 ── (DEF-088 / DEF-089, TD-099)
#
# The EOD card (api/telegram_summary_card.py) and the portfolio PDF
# (backtest/report/portfolio_pdf.py) measure text in matplotlib's bundled
# DejaVu fonts. When those files are missing, matplotlib silently falls back
# to another font (here: DejaVu Sans Bold, the only one left), every string
# comes out wider, and layout checks fail as if the layout code were wrong.
# That is what DEF-088 / DEF-089 were: since 30 Oct 2026 backend/venv's
# matplotlib had lost 15 of its 40 font files (DejaVuSans.ttf, all of DejaVu
# Sans Mono, …) while the installed app and the build venv were complete.
#
# A repaired install can still fail the same way: matplotlib caches its font
# list (fontlist-v<N>.json in its cache dir) and does not rebuild it when the
# files come back, so DejaVu Sans Mono stayed "not found" after the 26 Nov
# reinstall until that file was deleted. stale_lookups() catches that.
# (The installed app is not affected: PyInstaller gives it its own temporary
# MPLCONFIGDIR, so it never reads ~/.matplotlib.)
#
# missing_fonts() names what is missing; require_fonts() is the one line the
# card / PDF suites call first, so a damaged install fails with the repair
# command instead of a misleading layout failure. scalp-backend.spec checks
# the same files before PyInstaller bundles them.
import os
import sys

REQUIRED = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf")


def font_dir(mpl_data_path=None) -> str:
    if mpl_data_path is None:
        import matplotlib
        mpl_data_path = matplotlib.get_data_path()
    return os.path.join(mpl_data_path, "fonts", "ttf")


def missing_fonts(directory=None) -> list:
    d = directory or font_dir()
    return [f for f in REQUIRED if not os.path.isfile(os.path.join(d, f))]


FAMILIES = (("DejaVu Sans", "DejaVuSans.ttf"), ("DejaVu Sans Mono", "DejaVuSansMono.ttf"))


def stale_lookups() -> list:
    """Families whose lookup does not land on matplotlib's own file although the
    file exists — the font cache predates the fonts (delete it to rebuild)."""
    import matplotlib.font_manager as fm
    out = []
    for fam, fname in FAMILIES:
        try:
            got = fm.findfont(fm.FontProperties(family=fam), fallback_to_default=False)
        except Exception:
            got = ""
        if os.path.basename(got or "") != fname:
            out.append(fam)
    return out


def cache_files() -> list:
    import glob
    import matplotlib
    return sorted(glob.glob(os.path.join(matplotlib.get_cachedir(), "fontlist-v*.json")))


def repair_command() -> str:
    try:
        import matplotlib
        ver = matplotlib.__version__
    except Exception:
        ver = "<version>"
    return f"{sys.executable} -m pip install --force-reinstall --no-deps matplotlib=={ver}"


def require_fonts(suite: str = "") -> None:
    """Exit 1 with one clear message when matplotlib's DejaVu fonts are missing or its font cache is stale."""
    miss = missing_fonts()
    if miss:
        print(f"FAIL  matplotlib fonts missing{(' — ' + suite) if suite else ''}: {', '.join(miss)} not in "
              f"{font_dir()}. Text would render in a fallback font and every layout check below would fail "
              f"for the wrong reason (DEF-088/089). Repair this Python's matplotlib:\n  {repair_command()}")
        sys.exit(1)
    stale = stale_lookups()
    if stale:
        print(f"FAIL  matplotlib font cache is stale{(' — ' + suite) if suite else ''}: {', '.join(stale)} not found "
              f"although the files are in {font_dir()} (the cache was built while they were missing; DEF-088/089). "
              f"Delete it, matplotlib rebuilds it on the next run:\n  rm {' '.join(cache_files()) or '<cachedir>/fontlist-v*.json'}")
        sys.exit(1)
