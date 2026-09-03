# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Multi-judge caption scoring (portable toolkit version).

Implements MULTI_JUDGE_PLAN.md §§2-7.

Inputs (CLI flags, env-var fallbacks, defaults under ./examples/):
    --gt              GT caption file (env CAPTION_EVAL_GT)
    --ref             REF caption file (env CAPTION_EVAL_REF)
    --hyp             HYP caption file (env CAPTION_EVAL_HYP)
    --server-log-dir  optional, holds ref_server.log / hyp_server.log (env CAPTION_EVAL_SERVER_LOG_DIR)
    --runner-out      optional, runner.out with 'Captions complete in' (env CAPTION_EVAL_RUNNER_OUT)
    --out-root        output root (env CAPTION_EVAL_OUT_ROOT; default ./multi_judge_runs)
    --run-id          run subdirectory name (default: timestamped)

Run:
    OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
        python3 multi_judge.py --gt path/to/gt.txt --ref path/to/ref.txt --hyp path/to/hyp.txt
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TOOLKIT_DIR = Path(__file__).resolve().parent
DEFAULT_GT = TOOLKIT_DIR / "examples" / "captions" / "gt.txt"
DEFAULT_REF = TOOLKIT_DIR / "examples" / "captions" / "ref.txt"
DEFAULT_HYP = TOOLKIT_DIR / "examples" / "captions" / "hyp.txt"
DEFAULT_SERVER_LOG_DIR = TOOLKIT_DIR / "examples" / "server_logs"
DEFAULT_RUNNER_OUT = TOOLKIT_DIR / "examples" / "runner.out"
DEFAULT_OUT_ROOT = TOOLKIT_DIR / "multi_judge_runs"

# These remain module-level so any helper that references them keeps working;
# main() reads parsed CLI args and writes back over them before use.
GT_PATH = Path(os.environ.get("CAPTION_EVAL_GT", str(DEFAULT_GT)))
REF_PATH = Path(os.environ.get("CAPTION_EVAL_REF", str(DEFAULT_REF)))
HYP_PATH = Path(os.environ.get("CAPTION_EVAL_HYP", str(DEFAULT_HYP)))
SERVER_LOG_DIR = Path(os.environ.get("CAPTION_EVAL_SERVER_LOG_DIR", str(DEFAULT_SERVER_LOG_DIR)))
RUNNER_OUT = Path(os.environ.get("CAPTION_EVAL_RUNNER_OUT", str(DEFAULT_RUNNER_OUT)))

# Per-chunk caption-generation timing lives in the VLM server logs:
#   <ts> INFO [timing] vLLM generate elapsed=X.XXs (request_id=...)
#   <ts> INFO [BigVLMCaption] chunk_id=N ...
# The two lines are emitted back-to-back; we pair them by order of appearance.
SERVER_TIMING_RE = re.compile(r"\[timing\] vLLM generate elapsed=([0-9.]+)s")
SERVER_CHUNK_RE = re.compile(r"\[BigVLMCaption\] chunk_id=(\d+)")
WALL_CLOCK_RE = re.compile(r"Captions complete in ([0-9.]+)s")

# Built-in caption-file header regexes. Each must capture the named group `chunk_id`;
# `ts`, `start`, `end` are optional (used for latency / chunk-window display when present).
CAPTION_FORMATS: dict[str, str] = {
    # Default. vLLM-style runner log: full timestamp + bracketed app tag.
    "vllm": (
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+INFO\s+\[VLMCaption\]\s+"
        r"chunk_id=(?P<chunk_id>\d+)\s+stream=\S+\s+"
        r"chunk_start_pts=(?P<start>[0-9.]+)\s+chunk_end_pts=(?P<end>[0-9.]+)\s+"
        r"frame_times=\[\S*\]\s+source=\S+\s+caption="
    ),
    # Generic. One header line per chunk, optionally with a chunk window:
    #   chunk_id=N                                  → caption follows until next 'chunk_id=' or EOF
    #   chunk_id=N start=0.0 end=30.0               → optional window
    #   chunk_id=N | start=0.0 | end=30.0           → pipe-separated also fine
    "plain": (
        r"^chunk_id=(?P<chunk_id>\d+)"
        r"(?:[\s|]+start=(?P<start>[0-9.]+))?"
        r"(?:[\s|]+end=(?P<end>[0-9.]+))?"
        r"\s*$"
    ),
}

LOG_PREFIX_RE = re.compile(CAPTION_FORMATS["vllm"], re.MULTILINE)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class ChunkRecord:
    chunk_id: int
    chunk_start_pts: float
    chunk_end_pts: float
    log_ts: dt.datetime
    caption_md: str


def parse_caption_file(path: Path, header_re: re.Pattern[str] | None = None) -> list[ChunkRecord]:
    """Parse a caption file into ChunkRecords.

    `header_re` overrides the default `LOG_PREFIX_RE` so callers can plug in
    `CAPTION_FORMATS['plain']`, a user-supplied regex, or anything else with a
    `chunk_id` named group (and optionally `ts`/`start`/`end`).
    """
    pat = header_re if header_re is not None else LOG_PREFIX_RE
    text = path.read_text()
    matches = list(pat.finditer(text))
    if not matches:
        raise ValueError(
            f"No chunk headers found in {path}. Header regex did not match. "
            f"Try `--caption-format plain` or pass a custom `--chunk-regex`."
        )
    records: list[ChunkRecord] = []
    group_names = set(pat.groupindex)
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        caption_md = text[body_start:body_end].rstrip()
        ts_raw = m.group("ts") if "ts" in group_names else None
        if ts_raw:
            try:
                log_ts = dt.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                log_ts = dt.datetime.fromtimestamp(0)
        else:
            log_ts = dt.datetime.fromtimestamp(0)
        start_raw = m.group("start") if "start" in group_names else None
        end_raw = m.group("end") if "end" in group_names else None
        records.append(
            ChunkRecord(
                chunk_id=int(m.group("chunk_id")),
                chunk_start_pts=float(start_raw) if start_raw else 0.0,
                chunk_end_pts=float(end_raw) if end_raw else 0.0,
                log_ts=log_ts,
                caption_md=caption_md,
            )
        )
    records.sort(key=lambda r: r.chunk_id)
    return records


def resolve_header_re(caption_format: str, chunk_regex: str) -> re.Pattern[str]:
    """Build the chunk-header regex from CLI flags. `chunk_regex` (escape hatch) wins
    if non-empty, else look up `caption_format` in CAPTION_FORMATS."""
    if chunk_regex:
        return re.compile(chunk_regex, re.MULTILINE)
    if caption_format not in CAPTION_FORMATS:
        raise SystemExit(
            f"Unknown --caption-format {caption_format!r}; valid: {sorted(CAPTION_FORMATS)} "
            f"(or pass --chunk-regex)."
        )
    return re.compile(CAPTION_FORMATS[caption_format], re.MULTILINE)


# ---------------------------------------------------------------------------
# Judge prompt (v0)
# ---------------------------------------------------------------------------

PROMPT_VERSION = "v2"  # v2: static-scene / non-event rule — don't reward temporal coverage padding

SYSTEM_PROMPT = """You are evaluating two machine-generated video captions (REF and HYP) against a
ground-truth caption (GT) for the same video chunk. All three captions follow the same
Markdown schema with sections ## SCENE, ## ENTITIES, ## EVENTS (or ## TIMELINE),
## INTERACTIONS, ## CRITICAL_EVENTS, ## SUMMARY (some sections may be absent or empty
depending on chunk content). The domain may be any video understanding scenario —
intersection traffic, warehouse operations, indoor surveillance, tailgating detection,
retail loss prevention, etc. Use whatever entity categories and event types the GT
caption actually declares; do not assume a specific domain.

For each of REF and HYP, score on a 0.0–1.0 scale (two decimals, e.g. 0.80) on five axes against GT:

1. scene_match - whichever structured fields GT declares under ## SCENE (setting,
   location_type, camera_view, lighting, weather, area, zone, etc.) agree with GT.
   Score by fraction of GT-declared fields that match; ignore fields GT does not declare.
2. entities_recall - every GT entity is also present in the candidate (same category
   and roughly matching attributes). Missing a GT entity lowers this score.
3. entities_precision_no_hallucination - the candidate does NOT introduce entities,
   attributes, or counts that are absent from GT. Inventing things lowers this score.
4. events_actions - SALIENT actions/events in ## EVENTS or ## TIMELINE match GT in kind and
   ordering. See the STATIC SCENES rule below: non-events do not count.
5. temporal_alignment - first_seen / last_seen ranges and chunk-window framing are
   consistent with GT (allow +/- 2s slack). See the STATIC SCENES rule below.

==== STATIC SCENES & NON-EVENTS (read carefully — this is a scoring constraint) ====

"Events" means SALIENT changes: enter, exit, appear, disappear, approach, depart, start_moving,
stop, pick_up, put_down, open, close, push, pull, sit, stand, walk, run, fall, collide_with,
interact_with, hand_to, gesture, and anything in ## CRITICAL_EVENTS or ## INTERACTIONS.
The following are NOT events: `remain_stationary`, `no_significant_action`, and any line that
merely restates an unchanged state across timestamps.

When scoring events_actions and temporal_alignment, and when building event_matches:
  - STRIP all non-event lines from BOTH GT and the candidate before comparing. Collapse a run
    of repeated identical state lines (e.g. 18× "E1 remain_stationary") to a single observation.
  - DO NOT reward temporal density or fine-grained tiling. A caption that reports a static 30s
    window with ONE observation ("stationary throughout") is FULLY CORRECT and MUST score the
    SAME as a caption that repeats that observation 20 times. Repetition is not information, and
    sparse sampling that correctly captures a static scene is not a deficiency — it is the
    intended behavior of an adaptive frame selector.
  - If, after removing non-events, GT has NO salient events in this chunk, then events_actions
    and temporal_alignment are NOT APPLICABLE: score BOTH candidates 1.00 on those two axes and
    do not let them drag the composite. Judge such a chunk on scene + entities only.
  - Penalize a candidate on events ONLY when it MISSES a salient GT event or INVENTS one that GT
    does not have — NEVER for describing a static or low-activity scene with fewer timeline rows.

ALSO emit, for each candidate, explicit entity_matches and event_matches with three lists.

==== ENTITY-MATCH LIST RULES (read carefully — these are constraints, not suggestions) ====

For entity_matches, you are aligning the GT entity set against the candidate entity set.

The three lists serve different roles:
  - tp: GT entities that ARE matched by some candidate entity (use the GT label).
  - fn: GT entities that are NOT matched by any candidate entity (use the GT label).
  - fp: candidate entities that do NOT correspond to any GT entity (use the candidate label).

HARD CONSTRAINTS:
  C1. Every GT entity goes into EXACTLY ONE of tp or fn. Never both.
      Therefore: `len(tp) + len(fn) == number_of_GT_entities`. This is a checkable invariant.
  C2. tp uses GT-side labels; fp uses candidate-side labels; fn uses GT-side labels.
      Do not duplicate the same GT entity in two lists under different wordings.
  C3. A "match" requires that the candidate entity and the GT entity refer to the SAME
      real-world object. Category must agree (use the categories declared in GT — vehicle,
      person, forklift, package, etc.). Subtype must roughly agree (sedan vs SUV, worker
      vs supervisor, sealed vs damaged box — these are SUBTYPE mismatches; both go to fn/fp
      respectively). Attributes (color, size, markings, PPE, gait, posture) need only
      roughly agree — disagreement on a primary attribute (e.g. color, PPE compliance)
      is a mismatch; minor wording variance is fine.
  C4. If GT has K entities and the candidate has M, then `len(tp) + len(fp)` can be at most M
      and `len(tp) + len(fn)` must be exactly K.

WORKED EXAMPLE (domain-neutral — study the shape, then apply to the real captions below):
  Suppose GT has 3 entities:   {GT_E1: <category A, attrs a1>,  GT_E2: <category B, attrs b1>,  GT_E3: <category C, attrs c1>}
  Candidate has 3 entities:    {C_E1:  <category A, attrs a1'>, C_E2: <category B, attrs b2>,  C_E3: <category D, attrs d1>}
  where a1' ≈ a1 (rough attribute match), b2 ≠ b1 (primary attribute differs), and D ≠ C (category differs).
  Then a correct entity_matches block is:
    "tp": ["E1:<categoryA>:<short attrs from GT>"]            # GT_E1 matched by C_E1
    "fp": ["E2:<categoryB>:<short attrs from candidate>",     # C_E2 attribute mismatch, no GT match
           "E3:<categoryD>:<short attrs from candidate>"]     # C_E3 category mismatch, no GT match
    "fn": ["E2:<categoryB>:<short attrs from GT>",            # GT_E2 unmatched
           "E3:<categoryC>:<short attrs from GT>"]            # GT_E3 unmatched
  Check: len(tp) + len(fn) = 1 + 2 = 3 = number of GT entities. ✓
  Do NOT write "tp" containing C_E2 with the GT label — that would be putting a GT entity
  in two lists and would break the partition rule.

The same partition logic applies to event_matches (events in GT split into tp/fn; events only in
the candidate go to fp). Events are messier to align across captions, but apply the same intent.

Use short canonical labels like "E1:<category>:<2-3 word attribute summary>" — the GT entity id
when available, then category, then a 2-3 word attribute summary. The labels are how we compute
precision/recall/F1 downstream, so be consistent.

Scoring guidance (0.0–1.0 scale; two decimals fine, e.g. 0.85):
- 1.00 = matches GT on this axis with only minor wording differences.
- 0.80 = mostly matches; one minor miss or extra.
- 0.60 = partial; multiple misses or extras but the core is right.
- 0.40 = significant divergence; would mislead a downstream consumer.
- 0.20 = mostly wrong; little overlap with GT.
- 0.00 = unrelated or fully hallucinated on this axis.

Composite: a single 0.0–1.0 score per candidate (one for REF, one for HYP) reflecting your overall
judgment of that candidate vs GT, weighting the five axes as you see fit. Briefly justify in one
sentence per candidate.

Then declare a winner ("ref", "hyp", or "tie") with confidence ("low", "medium", "high"). Use
"tie" only when the two composites are effectively indistinguishable (e.g. you scored them the
same down to two decimals); otherwise pick the higher composite. There is no tie band — the
downstream verdict is sign-only by default.

Return ONLY a single JSON object matching the schema. No prose outside the JSON.
"""


JSON_SCHEMA_HINT = """JSON schema (return EXACTLY this shape):
{
  "chunk_id": <int>,
  "ref_vs_gt": {
    "scene_match": <0.0-1.0>,
    "entities_recall": <0.0-1.0>,
    "entities_precision_no_hallucination": <0.0-1.0>,
    "events_actions": <0.0-1.0>,
    "temporal_alignment": <0.0-1.0>,
    "composite": <0.0-1.0>,
    "entity_matches": {"tp": [...], "fp": [...], "fn": [...]},
    "event_matches":  {"tp": [...], "fp": [...], "fn": [...]},
    "rationale": "<one sentence>"
  },
  "hyp_vs_gt": { ...same shape... },
  "winner": "ref" | "hyp" | "tie",
  "winner_confidence": "low" | "medium" | "high"
}
"""


def build_user_message(
    chunk_id: int,
    gt_md: str,
    ref_md: str,
    hyp_md: str,
    swap: bool,
    domain_context: str = "",
) -> str:
    """Position-bias randomization: with swap=True, REF is shown as CANDIDATE_B and HYP as CANDIDATE_A.
    Labels in the JSON ('ref_vs_gt', 'hyp_vs_gt') stay tied to real content; the judge sees neutral
    candidate labels in the prompt body.

    domain_context, when non-empty, is prepended to the user message under a DOMAIN CONTEXT block
    so the judge knows what kind of footage this is (e.g. 'Warehouse aisle surveillance. Critical
    events are pallet drops, PPE violations, restricted-zone entry.'). Keep it concise — the
    judge has limited attention budget for non-caption text.
    """
    if swap:
        cand_a_label, cand_a_md = "HYP", hyp_md
        cand_b_label, cand_b_md = "REF", ref_md
    else:
        cand_a_label, cand_a_md = "REF", ref_md
        cand_b_label, cand_b_md = "HYP", hyp_md
    ctx_block = (
        f"===DOMAIN CONTEXT===\n{domain_context.strip()}\n\n"
        if domain_context and domain_context.strip()
        else ""
    )
    return (
        f"chunk_id = {chunk_id}\n"
        f"NOTE: candidate A is the {cand_a_label} caption; candidate B is the {cand_b_label} caption. "
        f"Write your scores under the correct key in the JSON (ref_vs_gt for the REF caption, "
        f"hyp_vs_gt for the HYP caption) regardless of which appears as A or B below.\n\n"
        f"{ctx_block}"
        f"===GT===\n{gt_md}\n\n"
        f"===CANDIDATE_A ({cand_a_label})===\n{cand_a_md}\n\n"
        f"===CANDIDATE_B ({cand_b_label})===\n{cand_b_md}\n\n"
        f"{JSON_SCHEMA_HINT}"
    )


# ---------------------------------------------------------------------------
# Judge API callers
# ---------------------------------------------------------------------------


@dataclass
class Judge:
    id: str  # filesystem-safe
    display: str
    provider: str
    call: Callable[[str, str], tuple[str, int]]  # (system, user) -> (raw_text, latency_ms)
    # Optional calibration text appended to the global SYSTEM_PROMPT for this judge only.
    # Use sparingly — judges should mostly see the same prompt so cross-judge agreement is
    # informative. Typical use: nudge a judge that interprets a rubric rule more strictly /
    # loosely than the others (e.g. gpt-4.1 was producing sparser TP lists than gpt-5; the
    # suffix clarifies "roughly agree" without contradicting the global rules).
    system_suffix: str = ""


def _http_post_json(url: str, headers: dict, body: dict, timeout: int = 180) -> tuple[dict, int]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    t0 = time.monotonic()
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                latency_ms = int((time.monotonic() - t0) * 1000)
                return json.loads(r.read()), latency_ms
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode()[:400]
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                sleep_s = 2 ** attempt + random.random()
                print(f"  HTTP {e.code} (attempt {attempt+1}), retrying in {sleep_s:.1f}s: {body_txt[:120]}",
                      file=sys.stderr)
                time.sleep(sleep_s)
                last_err = e
                continue
            raise RuntimeError(f"HTTP {e.code}: {body_txt}") from e
        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
                last_err = e
                continue
            raise
    raise RuntimeError(f"All retries exhausted: {last_err}")


def call_openai(model: str, system: str, user: str, reasoning: bool, max_tokens: int = 4000) -> tuple[str, int]:
    """Call OpenAI chat completions.

    `reasoning=True` uses the reasoning-token budget (`max_completion_tokens=16000`)
    needed for gpt-5/o-series. Other models use `max_tokens` (default 4000; raise for
    judges that produce truncated tp/fp/fn lists on long-entity-list chunks)."""
    key = os.environ["OPENAI_API_KEY"]
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    if reasoning:
        body["max_completion_tokens"] = 16000
    else:
        body["max_tokens"] = max_tokens
        body["temperature"] = 0.0
    resp, latency = _http_post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        body,
    )
    return resp["choices"][0]["message"]["content"], latency


CLAUDE_CLI_MAX_ATTEMPTS = int(os.environ.get("JUDGE_CLI_MAX_ATTEMPTS") or "6")
CLAUDE_CLI_BACKOFF_S = float(os.environ.get("JUDGE_CLI_BACKOFF_S") or "3.0")


def call_claude_cli(system: str, user: str) -> tuple[str, int]:
    """Call claude-opus-4-8 via the local `claude` CLI (no ANTHROPIC_API_KEY needed).

    Concatenates system + user into one stdin prompt so the judge sees the full
    instructions without a separate --system-prompt flag. Uses the current Claude
    Code session's authentication.
    """
    full_prompt = f"{system}\n\n---\n\n{user}"
    t0 = time.monotonic()
    # The endpoint intermittently returns 500s (observed ~2 of 3 calls on
    # 2026-08-24). A single failure used to drop the chunk, and enough dropped
    # chunks leave a scene with NO valid judge at all, so retry with backoff.
    last_err = ""
    for attempt in range(CLAUDE_CLI_MAX_ATTEMPTS):
        proc = subprocess.run(
            ["claude", "-p", "--model", "claude-opus-4-8", "--output-format", "json",
             "--no-session-persistence"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=300,
            cwd="/tmp",  # run outside project dir so project stop-hooks don't fire
        )
        if proc.returncode == 0:
            break
        last_err = (proc.stderr or proc.stdout or "").strip()[:400]
        if attempt < CLAUDE_CLI_MAX_ATTEMPTS - 1:
            # Jittered backoff: a retry storm from 32 workers is what produces
            # the 500s in the first place.
            time.sleep(CLAUDE_CLI_BACKOFF_S * (2 ** attempt) * (0.5 + random.random()))
    else:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode} after "
            f"{CLAUDE_CLI_MAX_ATTEMPTS} attempts: {last_err}"
        )
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        outer = json.loads(proc.stdout)
        text = outer.get("result", proc.stdout)
    except Exception:
        text = proc.stdout
    return text, latency_ms


def call_anthropic(model: str, system: str, user: str) -> tuple[str, int]:
    key = os.environ["ANTHROPIC_API_KEY"]
    body = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": 6000,
    }
    resp, latency = _http_post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        body,
        timeout=300,
    )
    parts = []
    for part in resp.get("content", []):
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts), latency


def make_judges() -> tuple[list[Judge], list[str]]:
    # Per-judge calibration suffix appended to SYSTEM_PROMPT only for this judge.
    # Empirically (pilot runs across ITS and warehouse data) gpt-4.1 produces tp lists
    # roughly half the size of gpt-5's because it treats minor wording differences as
    # mismatches. The suffix below clarifies "roughly agree" — it is a legitimate
    # reading of the global C3 rule, not a contradiction — and is paired with a 2x
    # max_tokens bump so the model has room to enumerate more matches.
    GPT_4_1_CALIBRATION = (
        "\n\nCalibration note (this judge only):\n"
        "When applying C3 ('match requires same real-world object, attributes need only "
        "roughly agree'), default to TP for entity and event pairs that share the same "
        "category and most attributes, even if some wording differs (e.g. 'worker in "
        "blue shirt' vs 'man wearing blue top'; 'enter intersection' vs 'enters the "
        "junction'). Reserve FP/FN for cases where the category disagrees or a primary "
        "attribute (color, role, vehicle subtype, PPE compliance) actually differs. "
        "Err on the inclusive side rather than the exclusive side when in doubt."
    )

    configured: list[tuple[str | None, Judge]] = [
        (
            "OPENAI_API_KEY",
            Judge(
                id="gpt-5",
                display="gpt-5",
                provider="openai",
                call=lambda s, u: call_openai("gpt-5", s, u, reasoning=True),
            ),
        ),
        (
            "OPENAI_API_KEY",
            Judge(
                id="gpt-4.1",
                display="gpt-4.1",
                provider="openai",
                call=lambda s, u: call_openai("gpt-4.1", s, u, reasoning=False, max_tokens=8000),
                system_suffix=GPT_4_1_CALIBRATION,
            ),
        ),
        (
            "ANTHROPIC_API_KEY",
            Judge(
                id="claude-opus-4-7",
                display="claude-opus-4-7",
                provider="anthropic",
                call=lambda s, u: call_anthropic("claude-opus-4-7", s, u),
            ),
        ),
        (
            "ANTHROPIC_API_KEY",
            Judge(
                id="claude-sonnet-4-6",
                display="claude-sonnet-4-6",
                provider="anthropic",
                call=lambda s, u: call_anthropic("claude-sonnet-4-6", s, u),
            ),
        ),
        # claude-opus-4-8 via local claude CLI — no API key needed; uses the current
        # Claude Code session's authentication. Only registered when the `claude`
        # binary is on PATH.
        *(
            [(
                None,
                Judge(
                    id="claude-opus-4-8",
                    display="claude-opus-4-8",
                    provider="claude-cli",
                    call=lambda s, u: call_claude_cli(s, u),
                ),
            )]
            if shutil.which("claude") else []
        ),
    ]
    judges: list[Judge] = []
    skipped: list[str] = []
    for env_var, judge in configured:
        if env_var is None:
            judges.append(judge)
        elif os.environ.get(env_var):
            judges.append(judge)
        else:
            skipped.append(f"{judge.display} (missing {env_var})")
    return judges, skipped


# ---------------------------------------------------------------------------
# JSON extraction (some models wrap in markdown)
# ---------------------------------------------------------------------------


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        # strip fenced code block
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    # locate the first { and last } and try to parse the slice
    first = raw.find("{")
    last = raw.rfind("}")
    if first == -1 or last == -1 or last < first:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")
    return json.loads(raw[first : last + 1])


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        return 0.0
    if tp == 0:
        return 0.0
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def get_counts(node: dict, key: str) -> tuple[int, int, int]:
    block = node.get(key, {}) or {}
    return (
        len(block.get("tp", []) or []),
        len(block.get("fp", []) or []),
        len(block.get("fn", []) or []),
    )


GT_ENTITY_ID_RE = re.compile(r"^\s*-\s*id:\s*E\d+\s*$", re.MULTILINE)


def _normalize_axis(v):
    """Judges now emit axis/composite scores on [0, 1]. Older cached runs used
    [0, 5]; auto-detect by value range so legacy caches stay readable.
    Pass-through for None / non-numeric."""
    if not isinstance(v, (int, float)):
        return v
    return float(v) / 5.0 if v > 1.0 else float(v)


def count_gt_entities(gt_caption: str) -> int:
    """Count `- id: EN` lines inside the ## ENTITIES block of a GT caption."""
    # restrict to the ENTITIES section
    section_re = re.compile(r"##\s*ENTITIES(.*?)(?:^##|\Z)", re.S | re.M)
    m = section_re.search(gt_caption)
    body = m.group(1) if m else gt_caption
    return len(GT_ENTITY_ID_RE.findall(body))


def validate_entity_lists(node: dict, gt_entity_count: int) -> str | None:
    """Check the entity_matches block for a candidate.

    A well-formed response must partition the GT entity set into TP ∪ FN
    (no overlap, no double-counting). We accept tp+fn == GT_count as the
    pass criterion. Anything else flags the response.

    Returns a short reason string when malformed, else None.
    """
    block = node.get("entity_matches", {}) or {}
    tp_list = block.get("tp", []) or []
    fn_list = block.get("fn", []) or []
    tp_count = len(tp_list)
    fn_count = len(fn_list)
    total = tp_count + fn_count
    if total > gt_entity_count:
        return f"tp+fn={total} exceeds |GT entities|={gt_entity_count} (double-counted GT entities across both lists)"
    if total < gt_entity_count:
        return f"tp+fn={total} less than |GT entities|={gt_entity_count} (some GT entities are in neither list)"
    return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_one(
    judge: Judge,
    chunk_id: int,
    gt_md: str,
    ref_md: str,
    hyp_md: str,
    swap: bool,
    out_dir: Path,
    domain_context: str = "",
) -> dict:
    user_msg = build_user_message(chunk_id, gt_md, ref_md, hyp_md, swap, domain_context=domain_context)
    system = SYSTEM_PROMPT + judge.system_suffix
    raw, latency_ms = judge.call(system, user_msg)
    judge_dir = out_dir / "raw" / judge.id
    judge_dir.mkdir(parents=True, exist_ok=True)
    try:
        parsed = extract_json(raw)
    except Exception:
        (judge_dir / f"chunk_{chunk_id:03d}.RAW.txt").write_text(raw)
        raise
    parsed["_judge_id"] = judge.id
    parsed["_chunk_id"] = chunk_id
    parsed["_swap"] = swap
    parsed["_judge_latency_ms"] = latency_ms
    parsed["_raw_excerpt"] = raw[:500]
    (judge_dir / f"chunk_{chunk_id:03d}.json").write_text(json.dumps(parsed, indent=2))
    return parsed


def parse_server_log_timings(log_path: Path) -> dict[int, float]:
    """Parse a VLM server log into {chunk_id: vLLM_elapsed_seconds}.

    The log emits a `[timing] vLLM generate elapsed=X.Xs` line immediately followed by a
    `[BigVLMCaption] chunk_id=N` line for that same generation. We pair them in order.
    """
    if not log_path.exists():
        return {}
    elapsed_seq: list[float] = []
    chunk_seq: list[int] = []
    for line in log_path.read_text(errors="ignore").splitlines():
        if line.startswith("["):  # skip vLLM banner duplicates
            continue
        m = SERVER_TIMING_RE.search(line)
        if m:
            elapsed_seq.append(float(m.group(1)))
            continue
        m = SERVER_CHUNK_RE.search(line)
        if m:
            chunk_seq.append(int(m.group(1)))
    out: dict[int, float] = {}
    for elapsed, cid in zip(elapsed_seq, chunk_seq):
        out[cid] = elapsed
    return out


def parse_total_wall_clock_from_runner(label: str, runner_out: Path) -> float | None:
    """The runner.out only logs `Captions complete in X.Xs` for the most recent run
    (typically HYP in this scene's runner.out). Returns None when not present."""
    if not runner_out.exists():
        return None
    text = runner_out.read_text(errors="ignore")
    # Heuristic: only HYP's number is here; we can't disambiguate REF reliably.
    if label.upper() != "HYP":
        return None
    matches = WALL_CLOCK_RE.findall(text)
    return float(matches[-1]) if matches else None


def compute_latency_views(records: list[ChunkRecord], label: str, server_log: Path) -> dict:
    records = sorted(records, key=lambda r: r.chunk_id)
    chunk_timings = parse_server_log_timings(server_log)  # {chunk_id: elapsed_s}
    per_chunk: list[dict] = []
    chunk_latencies: list[float] = []
    for r in records:
        lat = chunk_timings.get(r.chunk_id)
        if lat is not None:
            chunk_latencies.append(lat)
        per_chunk.append({
            "chunk_id": r.chunk_id,
            "chunk_start_pts": r.chunk_start_pts,
            "chunk_end_pts": r.chunk_end_pts,
            "latency_s": lat,  # vLLM generate elapsed for this chunk
        })
    video_time = sum(r.chunk_end_pts - r.chunk_start_pts for r in records)
    # total wall-clock prefers runner.out's "Captions complete in" (true e2e),
    # otherwise falls back to sum-of-per-chunk (overestimates: chunks run concurrently in vLLM).
    total_wall = parse_total_wall_clock_from_runner(label, RUNNER_OUT)
    wall_source = "runner.out 'Captions complete in'"
    if total_wall is None and chunk_latencies:
        total_wall = sum(chunk_latencies)
        wall_source = "sum of per-chunk vLLM elapsed (chunks ran concurrently, so this is an upper bound)"
    return {
        "label": label,
        "per_chunk": per_chunk,
        "n_chunks": len(records),
        "n_chunks_with_timing": len(chunk_latencies),
        "total_wall_clock_s": total_wall,
        "wall_source": wall_source,
        "sum_per_chunk_s": sum(chunk_latencies) if chunk_latencies else None,
        "mean_per_chunk_s": statistics.mean(chunk_latencies) if chunk_latencies else None,
        "median_per_chunk_s": statistics.median(chunk_latencies) if chunk_latencies else None,
        "p95_per_chunk_s": (
            statistics.quantiles(chunk_latencies, n=20)[18]
            if len(chunk_latencies) >= 2
            else (chunk_latencies[0] if chunk_latencies else None)
        ),
        "real_time_factor": (total_wall / video_time) if (total_wall and video_time) else None,
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main() -> int:
    global GT_PATH, REF_PATH, HYP_PATH, SERVER_LOG_DIR, RUNNER_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(GT_PATH), help="Path to GT caption file")
    ap.add_argument("--ref", default=str(REF_PATH), help="Path to REF caption file")
    ap.add_argument("--hyp", default=str(HYP_PATH), help="Path to HYP caption file")
    ap.add_argument("--server-log-dir", default=str(SERVER_LOG_DIR),
                    help="Dir holding ref_server.log / hyp_server.log (optional, for per-chunk latency)")
    ap.add_argument("--runner-out", default=str(RUNNER_OUT),
                    help="Path to runner.out (optional, for HYP wall-clock total)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-root",
                    default=os.environ.get("CAPTION_EVAL_OUT_ROOT", str(DEFAULT_OUT_ROOT)))
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--context", default="",
                    help="Domain context string, prepended to each judge's user message under a "
                         "===DOMAIN CONTEXT=== block. Example: 'Warehouse aisle surveillance. Critical "
                         "events are pallet drops, PPE violations, restricted-zone entry.' Keep it concise.")
    ap.add_argument("--context-file", default="",
                    help="Path to a file whose contents are used as --context. Overrides --context if both set.")
    ap.add_argument("--caption-format", default="vllm", choices=sorted(CAPTION_FORMATS),
                    help="Caption file header format. Default 'vllm' = '<ts> INFO [VLMCaption] chunk_id=N ... caption=<body>'. "
                         "Use 'plain' for 'chunk_id=N' headers (one per line) with the markdown body following.")
    ap.add_argument("--chunk-regex", default="",
                    help="Escape hatch: a custom multiline regex for the chunk-header line. Must capture "
                         "named group 'chunk_id' (and may capture 'ts', 'start', 'end'). Overrides --caption-format.")
    ap.add_argument("--tie-margin", type=float, default=0.0,
                    help="Δ composite (on the 0-1 scale) within ±this counts as TIE for the derived "
                         "'Closer to GT' cells in the final summary table. Default 0.0 (sign-only: "
                         "any nonzero Δ picks a winner; exact 0 = TIE). Set e.g. 0.05 to require a "
                         "5%% margin before declaring a winner. Should match score.py's --tie-margin "
                         "for consistent verdicts across the two scripts.")
    args = ap.parse_args()

    GT_PATH = Path(args.gt)
    REF_PATH = Path(args.ref)
    HYP_PATH = Path(args.hyp)
    SERVER_LOG_DIR = Path(args.server_log_dir)
    RUNNER_OUT = Path(args.runner_out)

    # Resolve domain context: --context-file takes precedence over --context.
    if args.context_file:
        domain_context = Path(args.context_file).read_text().strip()
    else:
        domain_context = args.context.strip()

    random.seed(args.seed)
    run_id = args.run_id or f"run_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run id: {run_id}\nOutput dir: {out_dir}")
    print(f"Inputs: gt={GT_PATH}\n        ref={REF_PATH}\n        hyp={HYP_PATH}")
    if domain_context:
        print(f"Domain context: {domain_context[:120]}{'...' if len(domain_context) > 120 else ''}")
    # Persist context for audit and warn if it differs from a prior run with the same id.
    ctx_path = out_dir / "domain_context.txt"
    prev_ctx = ctx_path.read_text().strip() if ctx_path.exists() else ""
    if prev_ctx and prev_ctx != domain_context:
        print(f"WARNING: --context differs from prior run with run-id={run_id}. Cache hits will reuse "
              f"the OLD context's judge responses. Use a fresh --run-id to re-judge with the new context.",
              file=sys.stderr)
    ctx_path.write_text(domain_context + "\n")

    header_re = resolve_header_re(args.caption_format, args.chunk_regex)
    fmt_label = args.caption_format if not args.chunk_regex else f"custom (--chunk-regex)"
    print(f"Parsing captions (format: {fmt_label})...")
    gt = parse_caption_file(GT_PATH, header_re=header_re)
    ref = parse_caption_file(REF_PATH, header_re=header_re)
    hyp = parse_caption_file(HYP_PATH, header_re=header_re)
    chunk_ids = sorted(set(r.chunk_id for r in gt) & set(r.chunk_id for r in ref) & set(r.chunk_id for r in hyp))
    print(f"  GT={len(gt)} REF={len(ref)} HYP={len(hyp)} common chunks={chunk_ids}")

    gt_by = {r.chunk_id: r for r in gt}
    ref_by = {r.chunk_id: r for r in ref}
    hyp_by = {r.chunk_id: r for r in hyp}
    gt_entity_counts = {c: count_gt_entities(gt_by[c].caption_md) for c in gt_by}
    print(f"  GT entity counts per chunk: {gt_entity_counts}")

    judges, skipped_judges = make_judges()
    print(f"Judges: {[j.display for j in judges]}")
    if skipped_judges:
        print(f"Skipped judges: {skipped_judges}")
    if not judges:
        raise RuntimeError("No judges configured; set at least one of OPENAI_API_KEY, ANTHROPIC_API_KEY.")

    # Save the prompt
    (out_dir / f"prompt_{PROMPT_VERSION}.md").write_text(
        f"# System\n\n{SYSTEM_PROMPT}\n\n# JSON schema\n\n{JSON_SCHEMA_HINT}\n"
    )

    # Swap plan (deterministic given --seed): {(judge_id, chunk_id): bool}
    swap_plan: dict[tuple[str, int], bool] = {}
    for j in judges:
        for c in chunk_ids:
            swap_plan[(j.id, c)] = random.random() < 0.5

    # Run all (judge, chunk) pairs
    results: dict[tuple[str, int], dict] = {}
    errors: list[dict] = []
    jobs = [(j, c) for j in judges for c in chunk_ids]
    print(f"Dispatching {len(jobs)} jobs (max_workers={args.max_workers})...")

    def do(j: Judge, c: int) -> tuple[str, int, dict | None, str | None]:
        cached_path = out_dir / "raw" / j.id / f"chunk_{c:03d}.json"
        if cached_path.exists():
            try:
                parsed = json.loads(cached_path.read_text())
                print(f"  [CACHE] {j.id} / chunk {c}")
                return j.id, c, parsed, None
            except Exception:
                pass  # cache corrupt → re-fetch
        t0 = time.time()
        try:
            parsed = run_one(
                j, c,
                gt_by[c].caption_md,
                ref_by[c].caption_md,
                hyp_by[c].caption_md,
                swap_plan[(j.id, c)],
                out_dir,
                domain_context=domain_context,
            )
            dt_s = time.time() - t0
            print(f"  [OK]   {j.id} / chunk {c}  ({dt_s:.1f}s)")
            return j.id, c, parsed, None
        except Exception as e:
            tb = traceback.format_exc()
            print(f"  [FAIL] {j.id} / chunk {c}: {e}", file=sys.stderr)
            return j.id, c, None, f"{e}\n{tb}"

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = [pool.submit(do, j, c) for (j, c) in jobs]
        for fut in as_completed(futures):
            jid, c, parsed, err = fut.result()
            if parsed is not None:
                results[(jid, c)] = parsed
            else:
                errors.append({"judge": jid, "chunk_id": c, "error": err})

    if errors:
        (out_dir / "errors.json").write_text(json.dumps(errors, indent=2))
        print(f"WARNING: {len(errors)} job(s) failed; see errors.json")

    # -------------------------------------------------------------------
    # Build per_judge.csv and per_chunk_summary.csv
    # -------------------------------------------------------------------
    per_judge_rows: list[dict] = []
    per_chunk_rows: list[dict] = []

    for c in chunk_ids:
        row: dict[str, Any] = {"chunk_id": c}
        for j in judges:
            r = results.get((j.id, c))
            if not r:
                row[f"{j.id}__ref_composite"] = None
                row[f"{j.id}__hyp_composite"] = None
                row[f"{j.id}__derived_winner"] = None
                row[f"{j.id}__judge_winner"] = None
                continue
            ref_block = r.get("ref_vs_gt", {}) or {}
            hyp_block = r.get("hyp_vs_gt", {}) or {}
            ref_comp = _normalize_axis(ref_block.get("composite"))
            hyp_comp = _normalize_axis(hyp_block.get("composite"))
            judge_winner = r.get("winner")
            if isinstance(ref_comp, (int, float)) and isinstance(hyp_comp, (int, float)):
                diff = hyp_comp - ref_comp  # Δ = HYP − REF (consistent w/ skill convention)
                derived = "hyp" if diff > args.tie_margin else "ref" if diff < -args.tie_margin else "tie"
            else:
                derived = None
            row[f"{j.id}__ref_composite"] = ref_comp
            row[f"{j.id}__hyp_composite"] = hyp_comp
            row[f"{j.id}__derived_winner"] = derived
            row[f"{j.id}__judge_winner"] = judge_winner

            # entity / event counts
            for cand in ("ref_vs_gt", "hyp_vs_gt"):
                block = r.get(cand, {}) or {}
                ent_tp, ent_fp, ent_fn = get_counts(block, "entity_matches")
                evt_tp, evt_fp, evt_fn = get_counts(block, "event_matches")
                malformed_reason = validate_entity_lists(block, gt_entity_counts.get(c, 0))
                per_judge_rows.append({
                    "judge": j.id,
                    "chunk_id": c,
                    "candidate": "REF" if cand == "ref_vs_gt" else "HYP",
                    "scene_match": _normalize_axis(block.get("scene_match")),
                    "entities_recall": _normalize_axis(block.get("entities_recall")),
                    "entities_precision_no_hallucination": _normalize_axis(block.get("entities_precision_no_hallucination")),
                    "events_actions": _normalize_axis(block.get("events_actions")),
                    "temporal_alignment": _normalize_axis(block.get("temporal_alignment")),
                    "composite": _normalize_axis(block.get("composite")),
                    "ent_tp": ent_tp, "ent_fp": ent_fp, "ent_fn": ent_fn,
                    "ent_f1_chunk": round(f1(ent_tp, ent_fp, ent_fn), 4),
                    "evt_tp": evt_tp, "evt_fp": evt_fp, "evt_fn": evt_fn,
                    "evt_f1_chunk": round(f1(evt_tp, evt_fp, evt_fn), 4),
                    "rationale": (block.get("rationale") or "")[:200],
                    "judge_winner": judge_winner,
                    "winner_confidence": r.get("winner_confidence"),
                    "swap": swap_plan[(j.id, c)],
                    "judge_latency_ms": r.get("_judge_latency_ms"),
                    "gt_entity_count": gt_entity_counts.get(c),
                    "entity_list_malformed": malformed_reason or "",
                })
        per_chunk_rows.append(row)

    # write per_judge.csv
    if per_judge_rows:
        pj_path = out_dir / "per_judge.csv"
        with pj_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_judge_rows[0].keys()))
            w.writeheader()
            w.writerows(per_judge_rows)

    if per_chunk_rows:
        pc_path = out_dir / "per_chunk_summary.csv"
        all_keys: list[str] = ["chunk_id"]
        for row in per_chunk_rows:
            for k in row.keys():
                if k != "chunk_id" and k not in all_keys:
                    all_keys.append(k)
        # also normalize: ensure every row has every key
        for row in per_chunk_rows:
            for k in all_keys:
                row.setdefault(k, None)
        with pc_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            w.writerows(per_chunk_rows)

    # -------------------------------------------------------------------
    # f1_per_judge.csv (micro & macro, entity & event, REF & HYP)
    # -------------------------------------------------------------------
    f1_rows = []
    for j in judges:
        judge_rows = [row for row in per_judge_rows if row["judge"] == j.id]
        if not judge_rows:
            continue
        agg: dict[str, dict[str, list]] = {
            "REF": {"ent_tp":[], "ent_fp":[], "ent_fn":[], "evt_tp":[], "evt_fp":[], "evt_fn":[],
                    "ent_f1_chunk":[], "evt_f1_chunk":[], "malformed":[]},
            "HYP": {"ent_tp":[], "ent_fp":[], "ent_fn":[], "evt_tp":[], "evt_fp":[], "evt_fn":[],
                    "ent_f1_chunk":[], "evt_f1_chunk":[], "malformed":[]},
        }
        for row in judge_rows:
            a = agg[row["candidate"]]
            for k in ("ent_tp","ent_fp","ent_fn","evt_tp","evt_fp","evt_fn","ent_f1_chunk","evt_f1_chunk"):
                a[k].append(row[k])
            a["malformed"].append(bool(row.get("entity_list_malformed")))
        out: dict[str, Any] = {"judge": j.id}
        for cand in ("REF", "HYP"):
            a = agg[cand]
            ent_micro = f1(sum(a["ent_tp"]), sum(a["ent_fp"]), sum(a["ent_fn"]))
            evt_micro = f1(sum(a["evt_tp"]), sum(a["evt_fp"]), sum(a["evt_fn"]))
            ent_macro = statistics.mean(a["ent_f1_chunk"]) if a["ent_f1_chunk"] else 0.0
            evt_macro = statistics.mean(a["evt_f1_chunk"]) if a["evt_f1_chunk"] else 0.0
            out[f"{cand}_ent_f1_micro"] = round(ent_micro, 4)
            out[f"{cand}_ent_f1_macro"] = round(ent_macro, 4)
            out[f"{cand}_evt_f1_micro"] = round(evt_micro, 4)
            out[f"{cand}_evt_f1_macro"] = round(evt_macro, 4)
            malformed_count = sum(1 for x in a["malformed"] if x)
            out[f"{cand}_entity_malformed_chunks"] = malformed_count
            out[f"{cand}_entity_malformed_fraction"] = round(
                malformed_count / len(a["malformed"]), 3
            ) if a["malformed"] else 0.0
        # mean composite — keep these regardless of malformed lists; composite is the model's
        # own judgment and is unaffected by the tp/fp/fn double-counting bug.
        ref_comps = [row["composite"] for row in judge_rows
                     if row["candidate"] == "REF" and isinstance(row["composite"], (int, float))]
        hyp_comps = [row["composite"] for row in judge_rows
                     if row["candidate"] == "HYP" and isinstance(row["composite"], (int, float))]
        out["REF_composite_mean"] = round(statistics.mean(ref_comps), 3) if ref_comps else None
        out["HYP_composite_mean"] = round(statistics.mean(hyp_comps), 3) if hyp_comps else None
        f1_rows.append(out)

    if f1_rows:
        with (out_dir / "f1_per_judge.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(f1_rows[0].keys()))
            w.writeheader()
            w.writerows(f1_rows)

    # -------------------------------------------------------------------
    # Latency CSVs
    # -------------------------------------------------------------------
    ref_lat = compute_latency_views(ref, "REF", SERVER_LOG_DIR / "ref_server.log")
    hyp_lat = compute_latency_views(hyp, "HYP", SERVER_LOG_DIR / "hyp_server.log")

    # per_chunk CSV
    with (out_dir / "latency_ref_hyp_per_chunk.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chunk_id", "chunk_start_pts", "chunk_end_pts",
                    "ref_latency_s", "hyp_latency_s", "delta_hyp_minus_ref_s", "hyp_speedup_x"])
        for rr, hr in zip(ref_lat["per_chunk"], hyp_lat["per_chunk"]):
            rl, hl = rr["latency_s"], hr["latency_s"]
            delta = (hl - rl) if (rl is not None and hl is not None) else None  # Δ = HYP − REF
            spd = (rl / hl) if (rl and hl) else None
            w.writerow([rr["chunk_id"], rr["chunk_start_pts"], rr["chunk_end_pts"],
                        "" if rl is None else f"{rl:.1f}",
                        "" if hl is None else f"{hl:.1f}",
                        "" if delta is None else f"{delta:+.1f}",
                        "" if spd is None else f"{spd:.2f}"])

    with (out_dir / "latency_ref_hyp_combined.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "total_wall_clock_s", "n_chunks",
                    "mean_per_chunk_s", "median_per_chunk_s", "p95_per_chunk_s",
                    "real_time_factor"])
        for d in (ref_lat, hyp_lat):
            w.writerow([
                d["label"],
                "" if d["total_wall_clock_s"] is None else f"{d['total_wall_clock_s']:.1f}",
                d["n_chunks"],
                "" if d["mean_per_chunk_s"] is None else f"{d['mean_per_chunk_s']:.1f}",
                "" if d["median_per_chunk_s"] is None else f"{d['median_per_chunk_s']:.1f}",
                "" if d["p95_per_chunk_s"] is None else f"{d['p95_per_chunk_s']:.1f}",
                "" if d["real_time_factor"] is None else f"{d['real_time_factor']:.2f}",
            ])

    # -------------------------------------------------------------------
    # Agreement matrix (Pearson r on per-chunk composite, REF & HYP)
    # -------------------------------------------------------------------
    completed_judge_ids = {row["judge"] for row in f1_rows}
    completed_judges = [j for j in judges if j.id in completed_judge_ids]
    judge_ids = [j.id for j in completed_judges]
    agreement_lines: list[str] = ["# Agreement Report", "", f"Prompt version: **{PROMPT_VERSION}**", ""]
    if judge_ids:
        for cand in ("REF", "HYP"):
            series: dict[str, list[float]] = {}
            for jid in judge_ids:
                series[jid] = []
                for c in chunk_ids:
                    v = next((row["composite"] for row in per_judge_rows
                              if row["judge"] == jid and row["chunk_id"] == c and row["candidate"] == cand), None)
                    series[jid].append(v if isinstance(v, (int, float)) else float("nan"))
            agreement_lines.append(f"## Pearson r on GT-vs-{cand} composite (per-chunk)")
            agreement_lines.append("")
            agreement_lines.append("| " + " | ".join(["judge"] + judge_ids) + " |")
            agreement_lines.append("|" + "|".join(["---"] * (len(judge_ids) + 1)) + "|")
            for a in judge_ids:
                cells = [a]
                for b in judge_ids:
                    xs = [x for x, y in zip(series[a], series[b]) if not math.isnan(x) and not math.isnan(y)]
                    ys = [y for x, y in zip(series[a], series[b]) if not math.isnan(x) and not math.isnan(y)]
                    r = pearson(xs, ys)
                    cells.append("1.000" if a == b else ("n/a" if r is None else f"{r:.3f}"))
                agreement_lines.append("| " + " | ".join(cells) + " |")
            agreement_lines.append("")
    else:
        agreement_lines.append("No judge completed enough calls to compute agreement.")
        agreement_lines.append("")

    # Top disagreement chunks: max |Δ composite| any pair, either candidate
    disagreement_rows: list[tuple[float, int, str, dict]] = []
    for c in chunk_ids:
        comps_ref = {jid: next((row["composite"] for row in per_judge_rows
                                if row["judge"] == jid and row["chunk_id"] == c and row["candidate"] == "REF"), None)
                     for jid in judge_ids}
        comps_hyp = {jid: next((row["composite"] for row in per_judge_rows
                                if row["judge"] == jid and row["chunk_id"] == c and row["candidate"] == "HYP"), None)
                     for jid in judge_ids}
        def spread(d):
            vs = [v for v in d.values() if isinstance(v, (int, float))]
            return (max(vs) - min(vs)) if len(vs) >= 2 else 0.0
        s_ref = spread(comps_ref)
        s_hyp = spread(comps_hyp)
        m = max(s_ref, s_hyp)
        which = "REF" if s_ref >= s_hyp else "HYP"
        disagreement_rows.append((m, c, which, {"REF": comps_ref, "HYP": comps_hyp}))
    disagreement_rows.sort(reverse=True)

    agreement_lines += ["## Top disagreement chunks", "",
                        "| chunk | candidate w/ max spread | spread | composites |",
                        "|---|---|---|---|"]
    for spread, c, which, comps in disagreement_rows[:10]:
        breakdown = ", ".join(f"{jid}={v}" for jid, v in comps[which].items())
        agreement_lines.append(f"| {c} | {which} | {spread:.2f} | {breakdown} |")
    agreement_lines.append("")

    # Tie-breaker conflicts (judge.winner vs derived)
    conflicts: list[str] = []
    for row in per_judge_rows:
        if row["candidate"] != "REF": continue  # check once per (judge, chunk)
        c = row["chunk_id"]
        jid = row["judge"]
        r = results.get((jid, c))
        if not r: continue
        ref_comp = _normalize_axis(r.get("ref_vs_gt", {}).get("composite"))
        hyp_comp = _normalize_axis(r.get("hyp_vs_gt", {}).get("composite"))
        judge_winner = r.get("winner")
        if isinstance(ref_comp, (int, float)) and isinstance(hyp_comp, (int, float)):
            diff = hyp_comp - ref_comp  # Δ = HYP − REF
            derived = "hyp" if diff > args.tie_margin else "ref" if diff < -args.tie_margin else "tie"
            if judge_winner and judge_winner != derived:
                conflicts.append(f"- chunk {c}, {jid}: judge_winner='{judge_winner}' but derived='{derived}' "
                                 f"(REF={ref_comp}, HYP={hyp_comp}). Derived value wins.")
    if conflicts:
        agreement_lines += ["## Self-reported vs derived winner conflicts", ""] + conflicts + [""]

    (out_dir / "agreement.md").write_text("\n".join(agreement_lines))

    # -------------------------------------------------------------------
    # final_summary_table.md
    # -------------------------------------------------------------------
    completed_display = [j.display for j in completed_judges]
    failed_display = [j.display for j in judges if j.id not in completed_judge_ids]
    errors_by_judge: dict[str, int] = {}
    for err in errors:
        errors_by_judge[err["judge"]] = errors_by_judge.get(err["judge"], 0) + 1

    lines: list[str] = [
        f"# Multi-Judge Caption Scoring — Final Summary",
        "",
        f"**Run id:** `{run_id}`",
        f"**Prompt version:** `{PROMPT_VERSION}`",
        f"**GT file:** `{GT_PATH}`",
        f"**REF file:** `{REF_PATH}`",
        f"**HYP file:** `{HYP_PATH}`",
        f"**Attempted judges:** {', '.join(j.display for j in judges)}",
        f"**Completed judges:** {', '.join(completed_display) if completed_display else 'none'}",
        f"**Chunks evaluated:** {chunk_ids}",
        f"**Errors:** {len(errors)}",
    ]
    if skipped_judges:
        lines.append(f"**Skipped judges:** {', '.join(skipped_judges)}")
    if failed_display:
        lines.append(f"**Judges without completed rows:** {', '.join(failed_display)}")
    if errors_by_judge:
        err_bits = ", ".join(f"{jid}={n}" for jid, n in sorted(errors_by_judge.items()))
        lines.append(f"**Failed calls by judge:** {err_bits}")
    lines += [
        "",
        "Sign convention: **Δ = HYP − REF**. Positive Δ ⇒ HYP higher / slower; negative ⇒ REF higher / slower. "
        "(HYP is treated as the proposed system being evaluated against REF as baseline.)",
        "",
        "## Block A1 — Per-chunk captioning latency (vLLM generate elapsed, from server logs)",
        "",
        "_Note: the chunk log timestamps in `gt.txt`/`ref.txt`/`hyp.txt` are second-precision and all share the same value, so per-chunk latency cannot be derived from them. We use `[timing] vLLM generate elapsed=...` lines in `server_logs/{ref,hyp}_server.log` instead. GT has no server log (it's an upstream / human-curated artifact)._",
        "",
        "| chunk_id | chunk window (s) | REF latency (s) | HYP latency (s) | Δ (HYP − REF) | HYP speedup vs REF |",
        "|---|---|---|---|---|---|",
    ]
    for rr, hr in zip(ref_lat["per_chunk"], hyp_lat["per_chunk"]):
        rl, hl = rr["latency_s"], hr["latency_s"]
        win = f"{rr['chunk_start_pts']:.1f} – {rr['chunk_end_pts']:.1f}"
        rl_s = "N/A" if rl is None else f"{rl:.2f}"
        hl_s = "N/A" if hl is None else f"{hl:.2f}"
        if rl is not None and hl is not None:
            delta = f"{hl - rl:+.2f}"
            spd = f"{rl/hl:.2f}×" if hl else "—"
        else:
            delta = "—"; spd = "—"
        lines.append(f"| {rr['chunk_id']} | {win} | {rl_s} | {hl_s} | {delta} | {spd} |")

    lines += [
        "",
        "## Block A2 — Combined latency",
        "",
        "| Candidate | Total wall-clock | # chunks | Mean / chunk | Median / chunk | p95 / chunk | Real-time factor |",
        "|---|---|---|---|---|---|---|",
    ]
    def fmt_s(v):
        return "—" if v is None else f"{v:.1f} s"
    def fmt_f(v):
        return "—" if v is None else f"{v:.2f}"
    for d in (ref_lat, hyp_lat):
        lines.append(
            f"| {d['label']} | {fmt_s(d['total_wall_clock_s'])} | {d['n_chunks']} | "
            f"{fmt_s(d['mean_per_chunk_s'])} | {fmt_s(d['median_per_chunk_s'])} | "
            f"{fmt_s(d['p95_per_chunk_s'])} | {fmt_f(d['real_time_factor'])} |"
        )

    lines += [
        "",
        f"_Latency source — REF: {ref_lat['wall_source']}; HYP: {hyp_lat['wall_source']}._",
    ]
    # Only emit a head-to-head speedup ratio when both candidates use the *same* source.
    if (
        ref_lat["total_wall_clock_s"]
        and hyp_lat["total_wall_clock_s"]
        and ref_lat["wall_source"] == hyp_lat["wall_source"]
    ):
        ratio = ref_lat["total_wall_clock_s"] / hyp_lat["total_wall_clock_s"]
        faster = "faster" if ratio > 1 else "slower"
        lines.append("")
        lines.append(f"_HYP is **{ratio:.2f}×** {faster} than REF on this scene set._")
    elif ref_lat["total_wall_clock_s"] and hyp_lat["total_wall_clock_s"]:
        lines.append("")
        lines.append("_Cannot compute a head-to-head HYP/REF speedup ratio because REF and HYP totals come from different sources — see latency_source note above._")

    # Block B — quality scores
    # If a judge's tp/fn lists violate `tp + fn == |GT entities|` on >=50% of chunks
    # for a given candidate, its entity F1 numbers for that candidate are unreliable —
    # show MALFORMED instead of a number. Composites stay as-is.
    MALFORMED_THRESHOLD = 0.5

    def cell_or_malformed(row, key, malformed_frac_key):
        if row.get(malformed_frac_key, 0) >= MALFORMED_THRESHOLD:
            return "**MALFORMED**"
        return f"{row[key]}"

    lines += [
        "",
        "## Block B — Per-judge quality scores",
        "",
        "_Entity F1 cells are flagged **MALFORMED** when ≥50% of a judge's chunks produced tp/fn lists that don't partition GT (i.e. `tp + fn ≠ |GT entities|` — double-counting or under-counting). Composite scores are left untouched because they're the judge's own holistic 0–1 score, unaffected by the list-shape bug._",
        "",
        "| Judge | REF ent F1 (μ) | HYP ent F1 (μ) | REF ent F1 (M) | HYP ent F1 (M) | REF evt F1 (μ) | HYP evt F1 (μ) | REF comp (mean) | HYP comp (mean) | Closer to GT |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in f1_rows:
        rc = row["REF_composite_mean"]
        hc = row["HYP_composite_mean"]
        if isinstance(rc, (int, float)) and isinstance(hc, (int, float)):
            diff = hc - rc  # Δ = HYP − REF
            closer = "HYP" if diff > args.tie_margin else "REF" if diff < -args.tie_margin else "TIE"
        else:
            closer = "—"
        lines.append(
            f"| {row['judge']} | "
            f"{cell_or_malformed(row, 'REF_ent_f1_micro', 'REF_entity_malformed_fraction')} | "
            f"{cell_or_malformed(row, 'HYP_ent_f1_micro', 'HYP_entity_malformed_fraction')} | "
            f"{cell_or_malformed(row, 'REF_ent_f1_macro', 'REF_entity_malformed_fraction')} | "
            f"{cell_or_malformed(row, 'HYP_ent_f1_macro', 'HYP_entity_malformed_fraction')} | "
            f"{row['REF_evt_f1_micro']} | {row['HYP_evt_f1_micro']} | "
            f"{rc} | {hc} | **{closer}** |"
        )

    # Validation footer: list any judge × candidate combos with malformed chunks.
    malformed_lines = []
    for row in f1_rows:
        for cand in ("REF", "HYP"):
            n_mal = row[f"{cand}_entity_malformed_chunks"]
            if n_mal > 0:
                malformed_lines.append(
                    f"- **{row['judge']} / {cand}:** {n_mal} of {len(chunk_ids)} chunks have malformed entity lists "
                    f"(tp + fn ≠ |GT entities|). See `per_judge.csv` column `entity_list_malformed` for per-chunk reasons."
                )
    if malformed_lines:
        lines += ["", "### Entity-list validation issues", ""] + malformed_lines

    # Block C — head-to-head
    lines += [
        "",
        "## Block C — Head-to-head verdict per judge (quality + latency)",
        "",
        "| Judge | Closer to GT | REF composite | HYP composite | Δ (HYP − REF) | REF latency (total) | HYP latency (total) |",
        "|---|---|---|---|---|---|---|",
    ]
    ref_total = ref_lat["total_wall_clock_s"]
    hyp_total = hyp_lat["total_wall_clock_s"]
    ref_total_s = "—" if ref_total is None else f"{ref_total:.1f} s"
    hyp_total_s = "—" if hyp_total is None else f"{hyp_total:.1f} s"
    for row in f1_rows:
        rc = row["REF_composite_mean"]; hc = row["HYP_composite_mean"]
        if isinstance(rc, (int, float)) and isinstance(hc, (int, float)):
            diff = hc - rc
            closer = "HYP" if diff > args.tie_margin else "REF" if diff < -args.tie_margin else "TIE"
            diff_s = f"{diff:+.2f}"
        else:
            closer = "—"; diff_s = "—"
        lines.append(
            f"| {row['judge']} | **{closer}** | {rc} | {hc} | {diff_s} | "
            f"{ref_total_s} | {hyp_total_s} |"
        )

    # Block D — consensus
    ref_count = sum(1 for row in f1_rows
                    if isinstance(row["REF_composite_mean"], (int, float))
                    and isinstance(row["HYP_composite_mean"], (int, float))
                    and (row["REF_composite_mean"] - row["HYP_composite_mean"]) > args.tie_margin)
    hyp_count = sum(1 for row in f1_rows
                    if isinstance(row["REF_composite_mean"], (int, float))
                    and isinstance(row["HYP_composite_mean"], (int, float))
                    and (row["REF_composite_mean"] - row["HYP_composite_mean"]) < -args.tie_margin)
    tie_count = len(f1_rows) - ref_count - hyp_count
    n_judges = len(f1_rows)
    threshold = math.ceil(n_judges * 3 / 5) if n_judges else 0  # >= 3/5 by plan; scale for fewer judges
    if n_judges == 0:
        consensus = "NO VALID JUDGES"
    elif ref_count >= threshold and hyp_count == 0:
        consensus = "REF"
    elif hyp_count >= threshold and ref_count == 0:
        consensus = "HYP"
    elif ref_count == 0 and hyp_count == 0:
        consensus = "TIE"
    elif ref_count > 0 and hyp_count > 0:
        consensus = "SPLIT"
    else:
        consensus = "INCONCLUSIVE"

    lines += [
        "",
        f"## Block D — Cross-judge consensus  (n={n_judges} judges this run)",
        "",
        "| | Judges saying REF closer | Judges saying HYP closer | Judges saying TIE | Consensus |",
        "|---|---|---|---|---|",
        f"| Count | {ref_count} | {hyp_count} | {tie_count} | **{consensus}** |",
        "",
    ]
    if n_judges == 0:
        lines += [
            "_Note: no judge completed, so no consensus threshold was applied._",
            "",
        ]
    elif n_judges != 5:
        lines += [
            f"_Note: {n_judges} judge(s) completed out of the 5-judge plan; consensus threshold scaled to >={threshold}/{n_judges}._",
            "",
        ]
    else:
        lines.append("")

    # F1-on-F1 footer — excludes judges whose entity F1 is MALFORMED for that candidate,
    # since a malformed 0.00 would inflate σ for the wrong reason.
    def collect_valid(metric: str, cand: str) -> tuple[list[float], list[str]]:
        vals, excluded = [], []
        for row in f1_rows:
            if row.get(f"{cand}_entity_malformed_fraction", 0) >= MALFORMED_THRESHOLD:
                excluded.append(row["judge"])
                continue
            v = row[metric]
            if isinstance(v, (int, float)):
                vals.append(v)
        return vals, excluded

    ref_micro, ref_excluded = collect_valid("REF_ent_f1_micro", "REF")
    hyp_micro, hyp_excluded = collect_valid("HYP_ent_f1_micro", "HYP")
    excl_note = ""
    if ref_excluded or hyp_excluded:
        parts = []
        if ref_excluded:
            parts.append(f"REF: excluded {', '.join(ref_excluded)} (malformed entity lists)")
        if hyp_excluded:
            parts.append(f"HYP: excluded {', '.join(hyp_excluded)} (malformed entity lists)")
        excl_note = "  \n_" + "; ".join(parts) + "._"
    if ref_micro and hyp_micro:
        rs = statistics.pstdev(ref_micro) if len(ref_micro) > 1 else 0.0
        hs = statistics.pstdev(hyp_micro) if len(hyp_micro) > 1 else 0.0
        interp = (
            "σ ≤ 0.05 = judges agree" if max(rs, hs) <= 0.05
            else "σ > 0.10 = judges materially disagree → prompt iteration warranted"
            if max(rs, hs) > 0.10
            else "0.05 < σ ≤ 0.10 = moderate spread; consider prompt iteration"
        )
        lines.append(
            f"> Across the {len(ref_micro)} judges with valid entity F1 (of {n_judges} total this run), "
            f"REF entity F1 (micro) ranged from `{min(ref_micro):.3f}` to `{max(ref_micro):.3f}` (σ={rs:.3f}). "
            f"HYP entity F1 (micro) ranged from `{min(hyp_micro):.3f}` to `{max(hyp_micro):.3f}` (σ={hs:.3f}). "
            f"**Interpretation:** {interp}.{excl_note}"
        )
    else:
        lines.append(
            f"> Cross-judge F1 σ not computed — too few judges with valid entity lists "
            f"(REF n={len(ref_micro)}, HYP n={len(hyp_micro)}).{excl_note}"
        )

    summary_text = "\n".join(lines)
    (out_dir / "final_summary_table.md").write_text(summary_text)
    print("\n" + "=" * 80)
    print(summary_text)
    print("=" * 80)
    print(f"\nAll outputs under: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
