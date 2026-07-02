"""Pluggable VLM-response parser for tailgating verification.

Wired via ``vlm.response_parser: "parsers.tailgating_enrichment.TailgatingVerifier"``
in ``vlm-as-verifier/configs/config.yml`` and the ``${VLM_AS_VERIFIER_PARSERS_DIR}
-> /app/parsers`` bind-mount in ``services/alert/compose.yml``. This mirrors the
VSS smartcities ``vlm-as-verifier`` profile's
``parsers.collision_enrichment.SceneEnhancer`` (see ``a_enrich``).

Alert Bridge loads this class ONCE at startup
(``models.base_response_parser.load_response_parser``) and shares the single
instance across every worker thread, so ``parse()`` MUST be stateless /
thread-safe: it reads only from compiled-once module constants and the local
stack and never mutates ``self``.

Contract (MOEAGENT-623 pluggable path): when ``vlm.response_parser`` is set it
FULLY replaces the built-in CR1/CR2 verdict parsing. The dict returned here is
JSON-serialized by Alert Bridge into ``info["vlm_response"]`` and
``info["verdict"]`` is forced to ``""``. Downstream consumers read the
structured verdict back out of ``info.vlm_response``.

Expected VLM output (per the tailgating prompts in ``alert_type_config.json``)::

    {
      "verdict": true,
      "description": "Two people entered; the second did not badge in.",
      "severity": "high",
      "confidence": 0.82,
      "reasoning": "A second individual follows closely without scanning."
    }

The cosmos-reason family often wraps JSON in ``<think>...</think>`` /
``<answer>...</answer>`` tags and/or markdown fences, so those are stripped
before decoding. ``parse()`` never raises: a reply that is not the expected
JSON degrades to ``{"vlm_response": <raw text>}`` so a malformed VLM response
keeps the event shippable instead of producing a verification-failed event.
"""

from __future__ import annotations

import json
import os
import re

_DEBUG = os.getenv("AMBIENT_PARSER_DEBUG", "").lower() in {"1", "true", "yes", "on"}

# Output contract, documented for prompt authors; not consulted at runtime.
PROMPT_OUTPUT_CONTRACT = {
    "verdict": "boolean",
    "description": "string describing the person who tailgated",
    "severity": ["low", "medium", "high"],
    "confidence": "float 0.0-1.0",
    "reasoning": "short explanation",
}

_VALID_SEVERITIES = ("low", "medium", "high")

# Compiled once at import; read-only afterwards (thread-safe).
_RE_MD_FENCE = re.compile(r"^```(?:\w+)?\s*\n(.*?)```\s*$", re.DOTALL)
_RE_ANSWER_TAG = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL | re.IGNORECASE)
_RE_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_RE_FIRST_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)

_TRUE_TOKENS = {"true", "yes", "y", "1", "confirmed", "tailgating", "positive"}


def _strip_wrappers(text: str) -> str:
    """Peel cosmos-reason think/answer tags and markdown fences off raw text."""
    s = (text or "").strip()
    # Prefer the <answer> payload when present; otherwise drop <think> blocks.
    m = _RE_ANSWER_TAG.search(s)
    if m:
        s = m.group(1).strip()
    else:
        s = _RE_THINK_TAG.sub("", s).strip()
    fence = _RE_MD_FENCE.match(s)
    if fence:
        s = fence.group(1).strip()
    return s


def _coerce_verdict(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUE_TOKENS


def _coerce_confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    # Clamp to the documented 0.0-1.0 range.
    return max(0.0, min(1.0, c))


def _coerce_severity(value) -> str:
    s = str(value if value is not None else "").strip().lower()
    return s if s in _VALID_SEVERITIES else "unknown"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


class TailgatingVerifier:
    """Map a tailgating VLM JSON response into structured verdict fields."""

    def parse(self, raw_response: str) -> dict:
        clean = _strip_wrappers(raw_response)

        data = None
        try:
            data = json.loads(clean)
        except (json.JSONDecodeError, TypeError):
            # Last resort: grab the first {...} block embedded in prose.
            m = _RE_FIRST_JSON_OBJ.search(clean)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = None

        if not isinstance(data, dict):
            fallback = {"vlm_response": _text(raw_response)}
            if _DEBUG:
                print(f"tailgating_verifier_fallback={json.dumps(fallback)}", flush=True)
            return fallback

        parsed = {
            "verdict": _coerce_verdict(data.get("verdict")),
            "description": _text(data.get("description")),
            "severity": _coerce_severity(data.get("severity")),
            "confidence": _coerce_confidence(data.get("confidence")),
            "reasoning": _text(data.get("reasoning")),
        }
        if _DEBUG:
            print(f"tailgating_verifier={json.dumps(parsed, sort_keys=True)}", flush=True)
        return parsed
