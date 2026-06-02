"""Shared scorecard parser. Used by streamlit_app.py renderer and the MCP
watchlist enrichment. Single source of truth for the JSON-in-markdown format
the Scorecard pre-scan section uses."""

import json
import re


def parse_scorecard_json(raw: str | None) -> dict | None:
    """Extract a JSON dict from a markdown answer.

    Accepts either a fenced ```json ... ``` block or a raw JSON object
    in the text. Returns the parsed dict, or None on any failure.
    """
    if not raw:
        return None

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    payload = m.group(1) if m else None

    if payload is None:
        start = raw.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(raw)):
                ch = raw[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        payload = raw[start:i + 1]
                        break

    if payload is None:
        return None

    try:
        return json.loads(payload)
    except Exception:
        pass

    try:
        fixed = re.sub(
            r'"((?:[^"\\]|\\.)*)"',
            lambda mm: '"' + mm.group(1).replace("\n", "\\n").replace("\r", "") + '"',
            payload,
            flags=re.DOTALL,
        )
        return json.loads(fixed)
    except Exception:
        return None


def parse_scorecard(ai_notes: dict | None) -> dict:
    """Pull verdict (str) and phase (int) out of ai_notes['Scorecard'].

    Returns {"verdict": str|None, "phase": int|None}. Never raises.
    """
    if not isinstance(ai_notes, dict):
        return {"verdict": None, "phase": None}

    raw = ai_notes.get("Scorecard")
    if not isinstance(raw, str):
        return {"verdict": None, "phase": None}

    parsed = parse_scorecard_json(raw)
    if not isinstance(parsed, dict):
        return {"verdict": None, "phase": None}

    verdict = parsed.get("verdict")
    if not isinstance(verdict, str):
        verdict = None

    phase_raw = parsed.get("phase")
    phase_num = None
    if isinstance(phase_raw, int):
        # compact form: {"phase": 3}
        phase_num = phase_raw
    elif isinstance(phase_raw, dict):
        # canonical form: {"phase": {"number": 3, ...}}
        n = phase_raw.get("number")
        if isinstance(n, int):
            phase_num = n
        elif isinstance(n, str) and n.isdigit():
            phase_num = int(n)
    elif isinstance(phase_raw, str) and phase_raw.isdigit():
        # very compact: {"phase": "3"}
        phase_num = int(phase_raw)

    return {"verdict": verdict, "phase": phase_num}


def resolve_verdict(cfg):
    """Single source of truth for a ticker's verdict + phase.

    The robustness table (cfg['robustness']['verdict_mapped']) is authoritative
    when present; otherwise fall back to the Scorecard section. Phase always
    comes from the Scorecard. Never raises.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    sc = parse_scorecard(cfg.get("ai_notes"))
    rob = cfg.get("robustness")
    verdict = sc["verdict"]
    if isinstance(rob, dict) and rob.get("verdict_mapped"):
        verdict = rob["verdict_mapped"]
    return {"verdict": verdict, "phase": sc["phase"]}
