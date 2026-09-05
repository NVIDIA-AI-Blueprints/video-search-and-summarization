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
"""Final scoring step: adds deterministic F1 for ## CRITICAL_EVENTS and
## INTERACTIONS to the per-judge entity/event scores already produced by
multi_judge.py, then combines all signals into a single 0-1 composite and
emits a REF-vs-HYP verdict per judge. (Judges now score on the [0, 1] scale
directly, so 0.30 reads as "30% of full scale" without conversion confusion.
Legacy cached runs whose composites are on [0, 5] are auto-rescaled.)

Default (strict) methodology:
  - CE match: (type, |time_gt - time_cand| <= TIME_TOL_S)
  - INT match: (relation, unordered participant set, |time_gt - time_cand| <= TIME_TOL_S)
  - F1 macro denominator restricted to chunks where GT has the relevant field;
    empty-on-both-sides chunks do not contribute a free 1.0.
  - Composite is mean over the axes that are defined for this candidate (an
    axis with no GT examples across the whole run is dropped, not zeroed).
  - --time-tolerance controls the time slack in seconds (default 5.0).

Legacy methodology (--legacy) preserves the original behavior:
  - CE matched on type only; INT matched on relation verb only.
  - Empty chunks contribute F1=1.0 to the macro (vacuous credit).
  - Fixed 5-axis equal-weight composite, no axis dropping.

The strict methodology is the default because the legacy behavior is
structurally biased toward TIE on this kind of data (flat CE/INT axes
dilute real entity/event signal below the tie threshold). See the
'Methodology' section of SKILL.md and README.md for the full rationale.

Reads:
    multi_judge.py run dir (raw/<judge>/chunk_NNN.json)
    GT / REF / HYP caption files

Writes to the same run dir:
    scores.csv   — per (judge, chunk, candidate)
    summary.csv  — per (judge, candidate) macro aggregates + verdict

Run:
    python3 score.py --run-dir multi_judge_runs/<run_id> \\
        --gt examples/captions/gt.txt \\
        --ref examples/captions/ref.txt \\
        --hyp examples/captions/hyp.txt

Or rely on defaults (will use the toolkit's own examples/).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from statistics import mean

TOOLKIT_DIR = Path(__file__).resolve().parent
DEFAULT_GT = TOOLKIT_DIR / "examples" / "captions" / "gt.txt"
DEFAULT_REF = TOOLKIT_DIR / "examples" / "captions" / "ref.txt"
DEFAULT_HYP = TOOLKIT_DIR / "examples" / "captions" / "hyp.txt"


# Built-in caption-file header regexes, mirrored from multi_judge.py.
# Each must capture named group `chunk_id`.
CAPTION_FORMATS: dict[str, str] = {
    "vllm": (
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+INFO\s+\[VLMCaption\]\s+"
        r"chunk_id=(?P<chunk_id>\d+).*?caption="
    ),
    "plain": (
        r"^chunk_id=(?P<chunk_id>\d+)"
        r"(?:[\s|]+start=(?P<start>[0-9.]+))?"
        r"(?:[\s|]+end=(?P<end>[0-9.]+))?"
        r"\s*$"
    ),
}


def split_chunks(path: Path, header_re: re.Pattern[str] | None = None) -> dict[int, str]:
    """Split a caption file into {chunk_id: caption_body}.

    `header_re` overrides the default (vllm format) so the same parser handles
    plain-text inputs or user-supplied regexes. Regex must capture `chunk_id`.
    """
    pat = header_re if header_re is not None else re.compile(CAPTION_FORMATS["vllm"], re.MULTILINE | re.DOTALL)
    text = path.read_text()
    matches = list(pat.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(matches):
        cid = int(m.group("chunk_id"))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[cid] = text[start:end]
    return out


def resolve_header_re(caption_format: str, chunk_regex: str) -> re.Pattern[str]:
    """Build the chunk-header regex from CLI flags. `chunk_regex` (escape hatch) wins
    if non-empty, else look up `caption_format` in CAPTION_FORMATS."""
    if chunk_regex:
        return re.compile(chunk_regex, re.MULTILINE | re.DOTALL)
    if caption_format not in CAPTION_FORMATS:
        sys.exit(f"Unknown --caption-format {caption_format!r}; valid: {sorted(CAPTION_FORMATS)} "
                 f"(or pass --chunk-regex).")
    return re.compile(CAPTION_FORMATS[caption_format], re.MULTILINE | re.DOTALL)


def section(body: str, name: str) -> str:
    m = re.search(rf"^##\s*{name}\s*\n(.*?)(?=^##\s|\Z)", body, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_critical_events(body: str) -> list[dict]:
    sec = section(body, "CRITICAL_EVENTS")
    if not sec or sec.lower().startswith("none"):
        return []
    events: list[dict] = []
    cur: dict | None = None
    for line in sec.splitlines():
        s = line.strip()
        if s.startswith("- type:"):
            if cur:
                events.append(cur)
            cur = {"type": s.split(":", 1)[1].strip().lower()}
        elif cur is not None:
            if s.startswith("- time:"):
                cur["time"] = s.split(":", 1)[1].strip()
            elif s.startswith("- participants:"):
                cur["participants"] = [p.strip() for p in s.split(":", 1)[1].split(",") if p.strip()]
            elif s.startswith("- description:"):
                cur["description"] = s.split(":", 1)[1].strip()
    if cur:
        events.append(cur)
    return events


def parse_interactions(body: str) -> list[dict]:
    sec = section(body, "INTERACTIONS")
    if not sec or sec.lower().startswith("none"):
        return []
    rels: list[dict] = []
    for line in sec.splitlines():
        s = line.strip().lstrip("-").strip()
        if not s or s.lower() == "none":
            continue
        m = re.search(r"E\d+\s+(\w+)\s+E\d+", s)
        if m:
            rels.append({"relation": m.group(1).lower(), "raw": s})
    return rels


def _parse_time_field(t: str) -> float | None:
    """Parse a CE 'time' value. Accepts '75.5', '75.5-77.0', '75.5–77.0', etc. Returns midpoint."""
    if not t:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", t)
    if not nums:
        return None
    vals = [float(x) for x in nums]
    return sum(vals) / len(vals)


def _interaction_participants(raw: str) -> frozenset[str]:
    """Extract {E1, E2, ...} from a raw interaction line like '[75.5 s] E1 collision E2'."""
    return frozenset(re.findall(r"E\d+", raw))


def _interaction_time(raw: str) -> float | None:
    """Extract the first timestamp from an interaction's '[t s] ...' prefix, if any."""
    m = re.search(r"\[\s*([\d.]+)", raw)
    return float(m.group(1)) if m else None


def strict_ce_match(gt_ce: list[dict], cand_ce: list[dict], time_tol_s: float) -> tuple[int, int, int]:
    """Match CE on (type ==, |Δtime| ≤ time_tol_s). Greedy: each candidate item
    matches at most one GT item. Returns (tp, fp, fn)."""
    used = [False] * len(cand_ce)
    tp = 0
    for g in gt_ce:
        g_type = g.get("type", "").lower()
        g_t = _parse_time_field(g.get("time", ""))
        for j, c in enumerate(cand_ce):
            if used[j]:
                continue
            if c.get("type", "").lower() != g_type:
                continue
            c_t = _parse_time_field(c.get("time", ""))
            if g_t is not None and c_t is not None and abs(g_t - c_t) > time_tol_s:
                continue
            used[j] = True
            tp += 1
            break
    fn = len(gt_ce) - tp
    fp = sum(1 for u in used if not u)
    return tp, fp, fn


def strict_int_match(gt_int: list[dict], cand_int: list[dict], time_tol_s: float) -> tuple[int, int, int]:
    """Match INT on (relation ==, unordered participant set ==, |Δtime| ≤ time_tol_s when present).
    Greedy. Returns (tp, fp, fn)."""
    used = [False] * len(cand_int)
    tp = 0
    for g in gt_int:
        g_rel = g.get("relation", "").lower()
        g_set = _interaction_participants(g.get("raw", ""))
        g_t = _interaction_time(g.get("raw", ""))
        for j, c in enumerate(cand_int):
            if used[j]:
                continue
            if c.get("relation", "").lower() != g_rel:
                continue
            if _interaction_participants(c.get("raw", "")) != g_set:
                continue
            c_t = _interaction_time(c.get("raw", ""))
            if g_t is not None and c_t is not None and abs(g_t - c_t) > time_tol_s:
                continue
            used[j] = True
            tp += 1
            break
    fn = len(gt_int) - tp
    fp = sum(1 for u in used if not u)
    return tp, fp, fn


def f1(tp: int, fp: int, fn: int, *, vacuous: bool = True) -> float:
    """F1.

    With vacuous=True (default, legacy behavior): when GT and candidate both
    have zero items, return 1.0. Used for entity/event F1 from the judges'
    own labeling where the case is rare and the convention is mild.

    With vacuous=False (strict mode for CE/INT): return NaN when both sides
    are empty so the caller can drop the chunk from the macro denominator,
    instead of giving both candidates free credit on empty chunks.
    """
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0 if vacuous else float("nan")
    if tp == 0:
        return 0.0
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def _normalize_composite(comp) -> float:
    """Judges now emit composites on [0, 1]. Older cached runs used [0, 5];
    auto-detect by value range so existing caches keep working."""
    v = float(comp)
    return v / 5.0 if v > 1.0 else v


def _nan_safe_mean(values):
    """Mean over non-NaN values. Returns NaN if all values are NaN."""
    real = [v for v in values if v == v]  # NaN != NaN
    return mean(real) if real else float("nan")


def match_by_key(gt_items: list[dict], cand_items: list[dict], key: str) -> tuple[int, int, int]:
    used = [False] * len(cand_items)
    tp = 0
    for g in gt_items:
        for j, c in enumerate(cand_items):
            if used[j]:
                continue
            if c[key] == g[key]:
                used[j] = True
                tp += 1
                break
    fn = len(gt_items) - tp
    fp = sum(1 for u in used if not u)
    return tp, fp, fn


def discover_judges(run_dir: Path) -> list[str]:
    raw = run_dir / "raw"
    if not raw.exists():
        sys.exit(f"No raw/ directory under {run_dir}; nothing to score.")
    return sorted(p.name for p in raw.iterdir() if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True,
                    help="Path to a multi_judge.py run directory (must contain raw/<judge>/chunk_NNN.json).")
    ap.add_argument("--gt", default=os.environ.get("CAPTION_EVAL_GT", str(DEFAULT_GT)))
    ap.add_argument("--ref", default=os.environ.get("CAPTION_EVAL_REF", str(DEFAULT_REF)))
    ap.add_argument("--hyp", default=os.environ.get("CAPTION_EVAL_HYP", str(DEFAULT_HYP)))
    ap.add_argument("--tie-margin", type=float, default=0.0,
                    help="Δ composite (on the 0-1 scale) within ±this is reported as TIE. Default 0.0 "
                         "(sign-only verdict: any nonzero Δ picks a winner; exact 0 = TIE). "
                         "Set e.g. 0.05 to require a 5%% margin before declaring a winner.")
    ap.add_argument("--time-tolerance", type=float, default=5.0,
                    help="Strict mode: CE/INT match accepts |Δtime| ≤ this many seconds. Default 5.0. "
                         "Skill's LLM-judge temporal_alignment axis uses ±2s; ±5 is more generous to the candidates.")
    ap.add_argument("--legacy", action="store_true",
                    help="Use the original (pre-strict) scoring: type-only CE match, relation-only INT match, "
                         "vacuous F1=1.0 on empty chunks. The legacy behavior is structurally biased toward "
                         "TIE on datasets where both candidates fail the same critical chunks — kept for "
                         "reproducibility, not recommended.")
    ap.add_argument("--caption-format", default="vllm", choices=sorted(CAPTION_FORMATS),
                    help="Caption file header format. Default 'vllm'. Use 'plain' for 'chunk_id=N' headers.")
    ap.add_argument("--chunk-regex", default="",
                    help="Escape hatch: custom multiline regex for the chunk-header line. Must capture "
                         "named group 'chunk_id'. Overrides --caption-format.")
    ap.add_argument("--exclude-judge", action="append", default=["nemotron-vl"],
                    help="Judge id to exclude from scoring (cache files on disk are kept, but their rows are "
                         "dropped from the verdict). Repeatable. Default excludes 'nemotron-vl' — it produced "
                         "persistent entity-list partition violations and outlier judging during pilot runs. "
                         "Pass `--exclude-judge ''` once to clear the default if you want to include nemotron.")
    args = ap.parse_args()

    # Allow `--exclude-judge ''` to clear the default exclusion list.
    exclude_set = {j for j in args.exclude_judge if j}

    header_re = resolve_header_re(args.caption_format, args.chunk_regex)
    run_dir = Path(args.run_dir).resolve()
    gt = split_chunks(Path(args.gt), header_re=header_re)
    ref = split_chunks(Path(args.ref), header_re=header_re)
    hyp = split_chunks(Path(args.hyp), header_re=header_re)
    chunk_ids = sorted(set(gt) & set(ref) & set(hyp))
    judges_discovered = discover_judges(run_dir)
    judges = [j for j in judges_discovered if j not in exclude_set]
    excluded = [j for j in judges_discovered if j in exclude_set]

    mode = "legacy (vacuous F1, type-only match)" if args.legacy else f"strict (±{args.time_tolerance}s time tol)"
    print(f"Run dir : {run_dir}")
    print(f"Judges  : {judges}")
    if excluded:
        print(f"Excluded: {excluded}  (pass `--exclude-judge ''` to clear default)")
    print(f"Chunks  : {chunk_ids}")
    print(f"Mode    : {mode}")

    # Deterministic per-chunk critical_event / interaction F1, judge-independent.
    # In strict mode: time-aware CE match, participant-set+time INT match, NaN F1 on
    # chunks where GT has no items of that kind (chunks dropped from macro denominator).
    # In legacy mode: type-only / relation-only match with vacuous F1=1.0 on empty chunks.
    det: dict[tuple[int, str], dict] = {}
    det_rows: list[dict] = []
    for c in chunk_ids:
        gt_ce = parse_critical_events(gt[c])
        gt_int = parse_interactions(gt[c])
        for label, body in (("REF", ref[c]), ("HYP", hyp[c])):
            c_ce = parse_critical_events(body)
            c_int = parse_interactions(body)
            if args.legacy:
                ce_tp, ce_fp, ce_fn = match_by_key(gt_ce, c_ce, "type")
                in_tp, in_fp, in_fn = match_by_key(gt_int, c_int, "relation")
                ce_f1 = f1(ce_tp, ce_fp, ce_fn, vacuous=True)
                in_f1 = f1(in_tp, in_fp, in_fn, vacuous=True)
            else:
                ce_tp, ce_fp, ce_fn = strict_ce_match(gt_ce, c_ce, args.time_tolerance)
                in_tp, in_fp, in_fn = strict_int_match(gt_int, c_int, args.time_tolerance)
                # NaN on chunks where GT has no items of that kind — drop from macro.
                ce_f1 = f1(ce_tp, ce_fp, ce_fn, vacuous=False) if gt_ce else float("nan")
                in_f1 = f1(in_tp, in_fp, in_fn, vacuous=False) if gt_int else float("nan")
            row = {
                "chunk_id": c, "candidate": label,
                "gt_ce": len(gt_ce), "cand_ce": len(c_ce),
                "ce_tp": ce_tp, "ce_fp": ce_fp, "ce_fn": ce_fn,
                "ce_f1": ce_f1,
                "gt_int": len(gt_int), "cand_int": len(c_int),
                "in_tp": in_tp, "in_fp": in_fp, "in_fn": in_fn,
                "in_f1": in_f1,
            }
            det_rows.append(row)
            det[(c, label)] = row

    # Combine each judge's entity/event scores with the deterministic
    # critical_event / interaction F1 into a uniform 0-1 composite per
    # (judge, chunk, candidate).
    out_rows: list[dict] = []
    for jid in judges:
        for c in chunk_ids:
            chunk_path = run_dir / "raw" / jid / f"chunk_{c:03d}.json"
            if not chunk_path.exists():
                print(f"  [skip] {jid} chunk {c}: missing {chunk_path.name}", file=sys.stderr)
                continue
            data = json.loads(chunk_path.read_text())
            for label, key in (("REF", "ref_vs_gt"), ("HYP", "hyp_vs_gt")):
                block = data.get(key, {}) or {}
                ent_tp = len(block.get("entity_matches", {}).get("tp", []))
                ent_fp = len(block.get("entity_matches", {}).get("fp", []))
                ent_fn = len(block.get("entity_matches", {}).get("fn", []))
                ev_tp = len(block.get("event_matches", {}).get("tp", []))
                ev_fp = len(block.get("event_matches", {}).get("fp", []))
                ev_fn = len(block.get("event_matches", {}).get("fn", []))
                # Entity/event F1 from judges: vacuous=True (consistent w/ pre-strict
                # behavior). These rarely hit the vacuous case anyway — GT typically has
                # entities and events for every chunk.
                ent_f1 = f1(ent_tp, ent_fp, ent_fn, vacuous=True)
                ev_f1 = f1(ev_tp, ev_fp, ev_fn, vacuous=True)
                comp = block.get("composite") or 0
                d = det[(c, label)]
                # Per-chunk composite: mean over axes that are defined (non-NaN) on
                # this chunk for this candidate. In strict mode, CE/INT F1 are NaN
                # on chunks where GT has no items of that kind; those axes simply
                # don't contribute (rather than contributing free 1.0).
                axis_vals = [_normalize_composite(comp), ent_f1, ev_f1, d["ce_f1"], d["in_f1"]]
                combined = _nan_safe_mean(axis_vals)
                out_rows.append({
                    "judge": jid, "chunk_id": c, "candidate": label,
                    "judge_composite": comp,
                    "entity_f1": round(ent_f1, 4),
                    "event_f1": round(ev_f1, 4),
                    "critical_event_f1": (None if d["ce_f1"] != d["ce_f1"] else round(d["ce_f1"], 4)),
                    "interaction_f1": (None if d["in_f1"] != d["in_f1"] else round(d["in_f1"], 4)),
                    "combined_score_0_1": round(combined, 4),
                    "gt_ce": d["gt_ce"],
                    "gt_int": d["gt_int"],
                })

    if not out_rows:
        sys.exit("No judge rows scored.")

    scores_path = run_dir / "scores.csv"
    with scores_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    summary: list[dict] = []
    for jid in judges:
        for label in ("REF", "HYP"):
            rs = [r for r in out_rows if r["judge"] == jid and r["candidate"] == label]
            if not rs:
                continue
            # NaN-safe per-axis macros: chunks where the axis is undefined (None) are skipped.
            ce_vals = [r["critical_event_f1"] for r in rs if r["critical_event_f1"] is not None]
            in_vals = [r["interaction_f1"] for r in rs if r["interaction_f1"] is not None]
            ce_macro = mean(ce_vals) if ce_vals else None
            in_macro = mean(in_vals) if in_vals else None
            judge_macro = mean(r["judge_composite"] for r in rs)
            ent_macro = mean(r["entity_f1"] for r in rs)
            evt_macro = mean(r["event_f1"] for r in rs)
            # Macro-first composite: combined = mean of axis macros (skipping axes with
            # no data run-wide). This is what drives the verdict — see comment in main
            # docstring. Each axis contributes equally regardless of how many chunks it
            # was defined on, which preserves the discriminative weight of CE/INT even
            # when they only have data on a subset of chunks.
            axis_macros: list[float] = [_normalize_composite(judge_macro), ent_macro, evt_macro]
            if ce_macro is not None:
                axis_macros.append(ce_macro)
            if in_macro is not None:
                axis_macros.append(in_macro)
            comb_macro_0_1 = mean(axis_macros)
            summary.append({
                "judge": jid, "candidate": label,
                "judge_composite_mean": round(judge_macro, 3),
                "entity_f1_macro": round(ent_macro, 4),
                "event_f1_macro": round(evt_macro, 4),
                "critical_event_f1_macro": round(ce_macro, 4) if ce_macro is not None else None,
                "interaction_f1_macro": round(in_macro, 4) if in_macro is not None else None,
                "combined_score_macro_0_1": round(comb_macro_0_1, 4),
            })

    summary_path = run_dir / "summary.csv"
    with summary_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    print("\n=== Critical events present in GT ===")
    for c in chunk_ids:
        gt_ce = parse_critical_events(gt[c])
        gt_int = parse_interactions(gt[c])
        types = [e["type"] for e in gt_ce]
        print(f"  chunk {c}: critical_events={len(gt_ce)} {types}  interactions={len(gt_int)}")

    mode_note = "legacy (vacuous F1=1.0 on empty)" if args.legacy else f"strict (±{args.time_tolerance}s, empty→—)"
    print(f"\n=== Deterministic critical_event / interaction F1 — {mode_note} ===")
    print(f"{'chunk':<6}{'cand':<5}{'gt_ce':<6}{'cand_ce':<8}{'ce_f1':<8}{'gt_int':<7}{'cand_int':<9}{'int_f1'}")
    for r in det_rows:
        ce = "  —  " if r["ce_f1"] != r["ce_f1"] else f"{r['ce_f1']:.2f}"
        it = "  —  " if r["in_f1"] != r["in_f1"] else f"{r['in_f1']:.2f}"
        print(f"{r['chunk_id']:<6}{r['candidate']:<5}{r['gt_ce']:<6}{r['cand_ce']:<8}{ce:<8}{r['gt_int']:<7}{r['cand_int']:<9}{it}")

    print("\n=== Verdict per judge ===")
    print(f"{'judge':<25}{'REF (0-1)':<11}{'HYP (0-1)':<11}{'Δ (HYP-REF)':<13}verdict")
    for jid in judges:
        r = next((x for x in summary if x["judge"] == jid and x["candidate"] == "REF"), None)
        h = next((x for x in summary if x["judge"] == jid and x["candidate"] == "HYP"), None)
        if not r or not h:
            continue
        delta = h["combined_score_macro_0_1"] - r["combined_score_macro_0_1"]
        # Use strict inequalities so tie_margin=0 yields a true sign-only verdict
        # (Δ > 0 → HYP, Δ < 0 → REF, Δ == 0 → TIE). With tie_margin > 0, |Δ| within
        # the margin is reported as TIE.
        verdict = "HYP" if delta > args.tie_margin else "REF" if delta < -args.tie_margin else "TIE"
        print(f"{jid:<25}{r['combined_score_macro_0_1']:<11}{h['combined_score_macro_0_1']:<11}{delta:+.3f}{'':<7}{verdict}")

    print(f"\nWrote {scores_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
