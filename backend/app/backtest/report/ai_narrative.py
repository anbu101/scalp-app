# backend/app/backtest/report/ai_narrative.py
#
# ── AI_NARRATIVE ── Phase 3: the narrative layer. A local Ollama model reads
# the report engine's computed summary and writes PROSE for section 7 of the
# report. Hard boundary, enforced in the prompt and by construction:
#
#     THE MODEL NEVER COMPUTES. Every number was calculated by
#     report_engine.py; the model only points at rows that already exist.
#
# Implementation notes (deliberate):
#   * stdlib urllib only — zero new dependencies, zero new PyInstaller hidden
#     imports (the known matplotlib/pkg_resources class of bundling pain is
#     exactly what we're avoiding).
#   * Fail-open: any Ollama failure raises OllamaError; callers surface a
#     friendly message and the deterministic report remains untouched.
#   * qwen3-family models emit <think>…</think> reasoning blocks; those are
#     stripped before the narrative is inserted.
#   * Pure helpers (compact_digest / build_prompt / insert_narrative /
#     strip_think) have no I/O — unit-testable without a live Ollama.

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# Curated, non-techy-friendly model menu. Disk sizes are approximate Q4
# defaults from the Ollama library (shown in the UI so the user can judge
# against free space BEFORE downloading).
CURATED_MODELS = [
    {"name": "llama3.2:3b", "disk_gb": 2.0,
     "note": "Smallest sensible — quickest download, fine narratives"},
    {"name": "qwen3:4b", "disk_gb": 2.5, "recommended": True,
     "note": "Recommended default — best quality per GB for structured analysis"},
    {"name": "phi4-mini", "disk_gb": 2.6,
     "note": "Strong small reasoner (Microsoft)"},
    {"name": "qwen3:8b", "disk_gb": 5.2,
     "note": "Upgrade when disk allows — noticeably better prose"},
]


class OllamaError(Exception):
    """Ollama unreachable / returned an error. Callers fail OPEN (report
    survives; only the narrative is skipped)."""


class OllamaCancelled(Exception):
    pass


def _req(base_url: str, path: str, payload: Optional[dict] = None,
         method: Optional[str] = None, timeout: float = 10.0) -> dict:
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise OllamaError(f"Ollama HTTP {e.code}: {detail or e.reason}")
    except Exception as e:
        raise OllamaError(f"Ollama not reachable at {base_url} ({e})")


# ── thin API surface ─────────────────────────────────────────────────
def get_version(base_url: str = DEFAULT_BASE_URL) -> str:
    return str(_req(base_url, "/api/version").get("version", "?"))


def list_models(base_url: str = DEFAULT_BASE_URL) -> list:
    out = []
    for m in _req(base_url, "/api/tags").get("models", []):
        out.append({"name": m.get("name"),
                    "size_bytes": int(m.get("size") or 0)})
    return out


def delete_model(name: str, base_url: str = DEFAULT_BASE_URL) -> None:
    _req(base_url, "/api/delete", {"name": name}, method="DELETE", timeout=30)


def pull_model(name: str, progress_cb: Callable[[dict], None],
               cancel_cb: Callable[[], bool],
               base_url: str = DEFAULT_BASE_URL) -> None:
    """Streaming pull with progress. Ollama emits line-delimited JSON:
    {status, digest?, total?, completed?}. A healthy pull emits lines
    continuously, so a 120s read-inactivity timeout is a real stall."""
    url = base_url.rstrip("/") + "/api/pull"
    req = urllib.request.Request(
        url, data=json.dumps({"name": name, "stream": True}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                if cancel_cb():
                    raise OllamaCancelled("pull cancelled by user")
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if p.get("error"):
                    raise OllamaError(str(p["error"]))
                progress_cb({
                    "status": p.get("status") or "",
                    "total": p.get("total"),
                    "completed": p.get("completed"),
                })
    except OllamaCancelled:
        raise
    except OllamaError:
        raise
    except Exception as e:
        raise OllamaError(f"pull failed: {e}")


def _generate_once(model: str, system: str, prompt: str, base_url: str,
                   timeout: float, num_predict: int,
                   think_flag: Optional[bool]) -> str:
    payload = {
        "model": model, "system": system, "prompt": prompt, "stream": False,
        # num_ctx pinned: Ollama's default context can silently TRUNCATE the
        # prompt from the FRONT (i.e. drop the DATA) when the digest is large.
        "options": {"temperature": 0.2, "num_predict": num_predict,
                    "num_ctx": 8192},
    }
    if think_flag is not None:
        payload["think"] = think_flag
    d = _req(base_url, "/api/generate", payload, timeout=timeout)
    return str(d.get("response") or "")


def _gen_safe(model: str, system: str, prompt: str, base_url: str,
              timeout: float, num_predict: int,
              think_flag: Optional[bool]) -> str:
    """One generation, retried without the `think` field if this Ollama build
    rejects it (older builds 400 on unknown fields)."""
    try:
        return _generate_once(model, system, prompt, base_url, timeout,
                              num_predict=num_predict, think_flag=think_flag)
    except OllamaError as e:
        if think_flag is not None and "think" in str(e).lower():
            return _generate_once(model, system, prompt, base_url, timeout,
                                  num_predict=num_predict, think_flag=None)
        raise


def generate(model: str, system: str, prompt: str,
             base_url: str = DEFAULT_BASE_URL, timeout: float = 420.0) -> str:
    """Single-shot generation with think-stripping. The narrative path uses
    generate_narrative (extraction-gated ladder); this stays as the plain
    building block."""
    return clean_response(_gen_safe(model, system, prompt, base_url, timeout,
                                    num_predict=3072, think_flag=None))


# ── pure helpers (unit-tested, no I/O) ───────────────────────────────
_THINK_RX = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """qwen3-family models can emit <think>…</think> reasoning; drop it."""
    return _THINK_RX.sub("", text or "").strip()


def clean_response(raw: str) -> str:
    """── THINK_LADDER ── an OPENED-but-never-CLOSED <think> means generation
    was truncated mid-reasoning: everything present is reasoning, no answer
    exists. Return empty so the retry ladder fires, instead of pasting the
    model's half-finished chain of thought into the report."""
    if not raw:
        return ""
    low = raw.lower()
    if "<think>" in low and "</think>" not in low:
        return ""
    return strip_think(raw)


# ── NARRATIVE_EXTRACT BEGIN ── model output is UNTRUSTED input, same as any
# external API: validate the shape, salvage the good lines, reject the rest.
# Small models leak working notes ("We are given a JSON…", restated rules,
# "Steps:") into the answer; this keeps only genuine bullet lines and the
# suggestion line, and returns "" (= retry/fail upstream) when fewer than 3
# real bullets survive — a rejected narrative is better than a polluted report.
_BULLET_RX = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_SUGGEST_RX = re.compile(r"^\s*(?:[-*•]\s*)?(suggested next sweep\s*:.*\S)\s*$",
                         re.IGNORECASE)
_META_RX = re.compile(
    # phrase tells of leaked working notes (word-boundary matching)
    r"\b(the task|hard rules?|we are given|the json|json with|verbatim|"
    r"preamble|re-?read|observations? section|output format|steps?\s*:|"
    r"let'?s|shape only|varied axis is|the problem says)\b"
    # digest STRUCTURE keys — genuine prose never contains these; substring
    # match on purpose (\b fails on snake_case: 'runs_shown' in
    # 'runs_shown_note' has no trailing word boundary)
    r"|(runs_total|runs_shown|sensitivity_by_axis|shared_baseline|"
    r"varied_axes|robust_ranking_top|naive_ranking_top|sign_flips|"
    r"net_by_year|top1_divergence)",
    re.IGNORECASE)


def extract_narrative(text: str) -> str:
    bullets = []
    suggest = None
    for line in (text or "").splitlines():
        ms = _SUGGEST_RX.match(line)
        if ms:
            s = ms.group(1).strip()
            suggest = "S" + s[1:] if s else s   # normalize capitalisation
            continue
        mb = _BULLET_RX.match(line)
        if not mb:
            continue                            # prose/meta between bullets: drop
        b = mb.group(1).strip()
        if _META_RX.search(b) or len(b) < 15:
            continue                            # working notes / fragments: drop
        bullets.append("- " + b)
    if len(bullets) < 3:
        return ""
    out = "\n".join(bullets[:8])
    if suggest:
        out += "\n\n" + suggest
    return out
# ── NARRATIVE_EXTRACT END ──


def _san(v):
    """JSON-safe: inf/nan → strings, floats rounded (token diet + a 4B model
    reads '1.34' better than '1.3399999999')."""
    if isinstance(v, dict):
        return {k: _san(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_san(x) for x in v]
    if isinstance(v, float):
        if v != v:
            return None
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return round(v, 2)
    return v


def compact_digest(summary: dict, max_runs: int = 12) -> dict:
    """Trim the engine summary to what a small local model can hold: full
    sensitivity tables (small by construction), top-8 + bottom-4 runs by net
    when the set is large, robust/naive top-5 orders, flips, portfolio."""
    runs = summary.get("runs") or []

    def row(p):
        k = p.get("kpis") or {}
        rs = p.get("robust") or {}
        return {
            "run": p.get("label"),
            "net": k.get("net"), "max_dd": k.get("max_dd"),
            "return_to_dd": k.get("return_to_dd"),
            "profit_factor": k.get("profit_factor"),
            "win_rate": k.get("win_rate"), "trades": k.get("trades"),
            "net_by_year": {y: (v.get("net") if isinstance(v, dict) else v)
                            for y, v in (p.get("years") or {}).items()},
            "worst_year_net": rs.get("worst_year_net"),
            "profitable_years": f"{rs.get('profitable_years', 0)}/{rs.get('total_years', 0)}",
            "flip": rs.get("flip_note") or None,
        }

    by_net = sorted(runs, key=lambda p: (p.get("kpis") or {}).get("net") or 0,
                    reverse=True)
    picked = by_net if len(by_net) <= max_runs else by_net[:8] + by_net[-4:]
    label_of = {p.get("run_id"): p.get("label") for p in runs}
    ranking = summary.get("ranking") or {}
    robust5 = [label_of.get(r, r) for r in (ranking.get("robust_order") or [])[:5]]
    naive5 = [label_of.get(r, r) for r in (ranking.get("naive_order") or [])[:5]]

    return _san({
        "title": summary.get("title"),
        "strategies": summary.get("strategies"),
        "periods": summary.get("periods"),
        "varied_axes": summary.get("varied_axes"),
        "shared_baseline": summary.get("shared_baseline"),
        "runs_total": len(runs),
        "runs_shown": [row(p) for p in picked],
        "runs_shown_note": ("all runs" if len(by_net) <= max_runs
                            else "top 8 and bottom 4 by net; middle omitted"),
        "sensitivity_by_axis": summary.get("sensitivity"),
        "grid": summary.get("grid"),
        "robust_ranking_top5": robust5,
        "naive_ranking_top5": naive5,
        "top1_divergence": bool(robust5 and naive5 and robust5[0] != naive5[0]),
        "sign_flips": [p.get("label") for p in runs
                       if (p.get("robust") or {}).get("flip")],
        "portfolio": summary.get("portfolio"),
    })


SYSTEM_PROMPT = (
    "You write the Observations section of an options-backtest report from "
    "computed JSON statistics.\n"
    "Rules: use only numbers that appear in the JSON — never compute, average "
    "or invent any; if the data is thin (one year, few runs, few trades) say "
    "so and temper every conclusion; plain trading language; no preamble, no "
    "meta-commentary, no headings, no restating these rules.\n\n"
    "Output EXACTLY this shape and nothing else:\n"
    "- <observation about plateaus vs cliffs in the sensitivity tables>\n"
    "- <observation comparing the robust ranking to the naive one>\n"
    "- <observation about sign-flips, portfolio findings, or data thinness>\n"
    "- <up to 4 more observations if the data warrants them>\n"
    "Suggested next sweep: <concrete axis values to test next>\n\n"
    "Example of the SHAPE ONLY — its numbers are from unrelated data and must "
    "never appear in your answer:\n"
    "- TP 30 is the only profitable column (mean net +14,057) while TP 50 and "
    "TP 100 are deeply negative — the edge dies past TP 30.\n"
    "- Robust and naive rankings agree on the leader, but with a single year "
    "of data the robust view adds nothing yet.\n"
    "- Sample is thin: one month, 16 trades per run — treat every ranking as "
    "provisional.\n"
    "Suggested next sweep: hold TP at 30 and test 20/25/35 over the full "
    "corpus."
)


def build_prompt(digest: dict) -> str:
    return ("DATA (all values computed by the report engine):\n"
            + json.dumps(digest, indent=1)
            + "\n\nWrite the Observations now. Begin your reply directly with "
              "the first '- ' bullet.")


def insert_narrative(markdown: str, narrative: str, model_name: str,
                     when_str: str) -> str:
    """Replace the body of '## 7. Observations' (up to the next '## '
    heading) with the narrative + attribution. Idempotent: re-narrating
    replaces the previous narrative."""
    header = "## 7. Observations"
    attrib = (f"\n\n_Narrative by local model `{model_name}` · {when_str}. "
              f"Prose only — every figure in this report is computed by the "
              f"report engine; this section references those tables, it is "
              f"not a source of numbers._")
    block = f"{header}\n\n{narrative.strip()}{attrib}\n"
    idx = markdown.find(header)
    if idx == -1:
        return markdown.rstrip() + "\n\n" + block
    nxt = markdown.find("\n## ", idx + len(header))
    if nxt == -1:
        return markdown[:idx] + block
    return markdown[:idx] + block + markdown[nxt:]


def generate_narrative(summary: dict, model: str,
                       base_url: str = DEFAULT_BASE_URL) -> str:
    """Digest → prompt → local model → EXTRACTED, validated narrative.

    ── THINK_LADDER ── two extraction-gated attempts:
      1. Thinking allowed with a generous budget (3072 tokens) — thinking
         models write their best answers WITH the reasoning; we strip the
         <think> block afterwards. (The original failure was an 800-token
         budget, not the thinking itself.)
      2. If the extractor rejects the result (leaked working notes, <3 real
         bullets, truncation): /no_think + explicit format anchor.
    Only extractor-approved bullets ever reach the report; a double rejection
    raises with the concrete remedies. Raises OllamaError on any failure —
    the caller fails open and the report stays untouched."""
    prompt = build_prompt(compact_digest(summary))
    raw = _gen_safe(model, SYSTEM_PROMPT, prompt, base_url, 420.0,
                    num_predict=3072, think_flag=None)
    text = extract_narrative(clean_response(raw))
    if text:
        return text
    raw = _gen_safe(model, SYSTEM_PROMPT,
                    "/no_think\n" + prompt +
                    "\nOutput ONLY '- ' bullet lines and the final "
                    "'Suggested next sweep:' line.",
                    base_url, 420.0, num_predict=1400, think_flag=False)
    text = extract_narrative(clean_response(raw))
    if text:
        return text
    raise OllamaError(
        "the model couldn't produce a clean Observations section (working "
        "notes instead of bullets, twice). Re-try once; if it persists, "
        "switch the active model — qwen3:8b or llama3.2:3b handle this "
        "format reliably."
    )