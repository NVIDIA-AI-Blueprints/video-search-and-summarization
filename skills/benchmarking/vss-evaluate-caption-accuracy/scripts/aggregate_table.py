#!/usr/bin/env python3
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
"""Build the accuracy + time-saved table for a PAIRED REF/HYP capture.

Joins two independent sources per scene:

  1. capture   — results/<desc>/server_logs/{ref,hyp}_<scene>.log
                 chunk count, frames per chunk, image-mode (1-frame) chunks,
                 and the "total processing time - X" wall clock.
  2. judging   — <judge-root>/<scene>/summary.csv written by score.py
                 entity/event F1 macros and the 0-1 combined score per candidate.

Frame counts come from the per-chunk `[Timing][VLMReq] ... chunk_id=N ... n_frames=M`
lines, deduplicated by chunk_id — some builds log that line once per chunk and
others twice (two handlers), so a naive line count is off by 2x on one of them.

Totals are chunk-weighted: a 60-chunk scene must not carry the same weight as a
14-chunk one. Pass --baseline-run/--baseline-judge-root to also emit the
incremental delta against a control run (e.g. the same config with the knob off).

Usage:
  python3 aggregate_paired_table.py \
      --run-dir  skills/accuracy_perf_evaluation/results/mcp200-2026-08-24 \
      --judge-root skills/video-caption-eval/runs_mcp200_20260824 \
      --baseline-run skills/accuracy_perf_evaluation/results/newlib-2026-08-17 \
      --baseline-judge-root skills/video-caption-eval/runs_newlib_20260817 \
      --label "min-changed-pixels=200" --baseline-label "min-changed-pixels=0"
"""
import argparse
import csv
import re
from pathlib import Path

SCENES = ["admin", "bus", "hospital", "its", "new_warehouse", "warehouse"]
JUDGE = "claude-opus-4-8"

_VLMREQ_RE = re.compile(r"chunk_id=(\d+).*?n_frames=(\d+)")
_TIME_RE = re.compile(r"total processing time - ([0-9.]+)")
# Fallbacks for logs captured after the instrumentation cleanup (commit cd18c8c
# dropped every [Timing][*] log, so n_frames= no longer exists). [BigVLMCaption]
# still carries chunk_id, and the plugin's own EOS line still reports the frame
# count it selected for each chunk.
_CAPTION_RE = re.compile(r"\[BigVLMCaption\].*?chunk_id=(\d+)")
_FSELECT_EOS_RE = re.compile(r"EOS OF-only -> (\d+) frame")


def parse_server_log(path: Path) -> dict:
    """chunks / image-mode chunks / mean frames / wall-clock seconds for one run."""
    if not path.exists():
        return {}
    frames_by_chunk = {}
    caption_chunk_ids = set()
    fselect_counts = []
    seconds = None
    for line in path.read_text(errors="replace").splitlines():
        if "[Timing][VLMReq]" in line and "n_frames=" in line:
            m = _VLMREQ_RE.search(line)
            if m:
                frames_by_chunk[int(m.group(1))] = int(m.group(2))
            continue
        m = _CAPTION_RE.search(line)
        if m:
            caption_chunk_ids.add(int(m.group(1)))
            continue
        m = _FSELECT_EOS_RE.search(line)
        if m:
            fselect_counts.append(int(m.group(1)))
            continue
        m = _TIME_RE.search(line)
        if m:
            seconds = float(m.group(1))  # last one wins: the final chunk's total
    if not frames_by_chunk:
        # Post-cleanup log. Chunk count comes from the emitted captions; frame
        # counts are only recoverable on the HYP arm, where the plugin logs one
        # "EOS OF-only -> N frame(s)" per chunk. Pipeline warmup runs through the
        # same element first, so keep only the trailing <chunks> verdicts. REF is
        # bypass-mode (no plugin), so it reports chunks and seconds but no frames.
        if not caption_chunk_ids:
            return {"seconds": seconds} if seconds is not None else {}
        out = {"chunks": len(caption_chunk_ids), "seconds": seconds}
        scene_counts = fselect_counts[-len(caption_chunk_ids):]
        if len(scene_counts) == len(caption_chunk_ids):
            out["image_mode"] = sum(1 for c in scene_counts if c == 1)
            out["frames_total"] = sum(scene_counts)
            out["frames_mean"] = sum(scene_counts) / len(scene_counts)
        return out
    counts = list(frames_by_chunk.values())
    return {
        "chunks": len(counts),
        "image_mode": sum(1 for c in counts if c == 1),
        "frames_total": sum(counts),
        "frames_mean": sum(counts) / len(counts),
        "seconds": seconds,
    }


def parse_judge_summary(path: Path) -> dict:
    """{'REF': {...}, 'HYP': {...}} from a score.py summary.csv."""
    if not path.exists():
        return {}
    out = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            if row.get("judge") != JUDGE:
                continue
            out[row["candidate"].upper()] = {
                "combined": float(row["combined_score_macro_0_1"]),
                "entity_f1": float(row["entity_f1_macro"]),
                "event_f1": float(row["event_f1_macro"]),
            }
    return out


def collect(run_dir: Path, judge_root: Path, scenes, variants=None) -> dict:
    """variants maps scene -> suffix (e.g. {'bus': '_v2'}) for HYP re-captures.

    A repeat HYP capture writes hyp_<scene><suffix>.log and is judged into
    <judge-root>/<scene><suffix>/, while REF stays the one it was paired with.
    """
    variants = variants or {}
    per_scene = {}
    for s in scenes:
        logs = run_dir / "server_logs"
        sfx = variants.get(s, "")
        rec = {
            "hyp": parse_server_log(logs / f"hyp_{s}{sfx}.log"),
            "ref": parse_server_log(logs / f"ref_{s}.log"),
            "judge": parse_judge_summary(judge_root / f"{s}{sfx}" / "summary.csv"),
            "variant": sfx,
        }
        per_scene[s] = rec
    return per_scene


def weighted(per_scene: dict, pick) -> float:
    """Chunk-weighted mean of pick(rec), skipping scenes where it is unavailable."""
    num = den = 0.0
    for rec in per_scene.values():
        n = rec["hyp"].get("chunks") or rec["ref"].get("chunks")
        v = pick(rec)
        if n and v is not None:
            num += n * v
            den += n
    return num / den if den else float("nan")


def _fmt(v, spec="%.4f"):
    return "n/a" if v is None else spec % v


def emit_main_table(per_scene: dict, label: str) -> list:
    lines = [
        f"### Accuracy and processing time — {label}",
        "",
        "| Scene | chunks | image-mode | frames/chunk REF→HYP | REF acc | HYP acc | Δ acc "
        "| REF s | HYP s | Δt | Δt% |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, rec in per_scene.items():
        h, r, j = rec["hyp"], rec["ref"], rec["judge"]
        ref_acc = j.get("REF", {}).get("combined")
        hyp_acc = j.get("HYP", {}).get("combined")
        d_acc = None if ref_acc is None or hyp_acc is None else hyp_acc - ref_acc
        rs, hs = r.get("seconds"), h.get("seconds")
        dt = None if rs is None or hs is None else hs - rs
        dtp = None if not rs or dt is None else 100.0 * dt / rs
        lines.append(
            f"| {s}{rec.get('variant', '')} | {h.get('chunks', r.get('chunks', 'n/a'))} | "
            f"{h.get('image_mode', 'n/a')} | "
            f"{_fmt(r.get('frames_mean'), '%.1f')}→{_fmt(h.get('frames_mean'), '%.1f')} | "
            f"{_fmt(ref_acc)} | {_fmt(hyp_acc)} | {_fmt(d_acc, '%+.4f')} | "
            f"{_fmt(rs, '%.2f')} | {_fmt(hs, '%.2f')} | {_fmt(dt, '%+.2f')} | "
            f"{_fmt(dtp, '%+.1f%%')} |"
        )
    tot_chunks = sum(r["hyp"].get("chunks", 0) for r in per_scene.values())
    tot_im = sum(r["hyp"].get("image_mode", 0) for r in per_scene.values())
    tot_rs = sum(r["ref"].get("seconds") or 0.0 for r in per_scene.values())
    tot_hs = sum(r["hyp"].get("seconds") or 0.0 for r in per_scene.values())
    w_ref = weighted(per_scene, lambda r: r["judge"].get("REF", {}).get("combined"))
    w_hyp = weighted(per_scene, lambda r: r["judge"].get("HYP", {}).get("combined"))
    lines.append(
        f"| **TOTAL/wtd** | **{tot_chunks}** | **{tot_im}** | | **{w_ref:.4f}** | "
        f"**{w_hyp:.4f}** | **{w_hyp - w_ref:+.4f}** | **{tot_rs:.2f}** | "
        f"**{tot_hs:.2f}** | **{tot_hs - tot_rs:+.2f}** | "
        f"**{100.0 * (tot_hs - tot_rs) / tot_rs:+.1f}%** |"
    )
    return lines


def emit_f1_table(per_scene: dict, label: str) -> list:
    lines = [
        f"### LLM-as-judge F1 detail ({JUDGE}) — {label}",
        "",
        "| Scene | entity F1 REF | entity F1 HYP | Δ | event F1 REF | event F1 HYP | Δ |",
        "|---|---|---|---|---|---|---|",
    ]
    for s, rec in per_scene.items():
        j = rec["judge"]
        r, h = j.get("REF", {}), j.get("HYP", {})
        for axis in ("entity_f1", "event_f1"):
            r.setdefault(axis, None)
            h.setdefault(axis, None)
        lines.append(
            f"| {s} | {_fmt(r['entity_f1'])} | {_fmt(h['entity_f1'])} | "
            f"{_fmt(None if None in (r['entity_f1'], h['entity_f1']) else h['entity_f1'] - r['entity_f1'], '%+.4f')} | "
            f"{_fmt(r['event_f1'])} | {_fmt(h['event_f1'])} | "
            f"{_fmt(None if None in (r['event_f1'], h['event_f1']) else h['event_f1'] - r['event_f1'], '%+.4f')} |"
        )
    for axis, name in (("entity_f1", "entity"), ("event_f1", "event")):
        wr = weighted(per_scene, lambda r, a=axis: r["judge"].get("REF", {}).get(a))
        wh = weighted(per_scene, lambda r, a=axis: r["judge"].get("HYP", {}).get(a))
        lines.append(f"| **wtd {name} F1** | **{wr:.4f}** | **{wh:.4f}** | **{wh - wr:+.4f}** | | | |")
    return lines


def emit_baseline_table(cur: dict, base: dict, label: str, base_label: str) -> list:
    lines = [
        f"### Incremental effect — {label} vs {base_label}",
        "",
        "| Scene | image-mode base→cur | HYP acc base | HYP acc cur | Δ acc | "
        "HYP s base | HYP s cur | Δ HYP s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in cur:
        c, b = cur[s], base.get(s, {"hyp": {}, "judge": {}})
        ca = c["judge"].get("HYP", {}).get("combined")
        ba = b["judge"].get("HYP", {}).get("combined")
        cs, bs = c["hyp"].get("seconds"), b["hyp"].get("seconds")
        lines.append(
            f"| {s} | {b['hyp'].get('image_mode', 'n/a')}→{c['hyp'].get('image_mode', 'n/a')} | "
            f"{_fmt(ba)} | {_fmt(ca)} | "
            f"{_fmt(None if None in (ba, ca) else ca - ba, '%+.4f')} | "
            f"{_fmt(bs, '%.2f')} | {_fmt(cs, '%.2f')} | "
            f"{_fmt(None if None in (bs, cs) else cs - bs, '%+.2f')} |"
        )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--judge-root", required=True)
    ap.add_argument("--baseline-run")
    ap.add_argument("--baseline-judge-root")
    ap.add_argument("--label", default="current")
    ap.add_argument("--baseline-label", default="baseline")
    ap.add_argument("--scenes", nargs="*", default=SCENES)
    ap.add_argument(
        "--variant", action="append", default=[], metavar="SCENE=SUFFIX",
        help="use a repeat HYP capture for SCENE, e.g. --variant bus=_v2 "
             "(reads hyp_bus_v2.log and <judge-root>/bus_v2/). Repeatable.",
    )
    ap.add_argument("--out", help="write the markdown here as well as stdout")
    args = ap.parse_args()

    variants = {}
    for spec in args.variant:
        scene, _, sfx = spec.partition("=")
        variants[scene] = sfx

    cur = collect(Path(args.run_dir), Path(args.judge_root), args.scenes, variants)
    blocks = emit_main_table(cur, args.label) + [""] + emit_f1_table(cur, args.label)
    if args.baseline_run and args.baseline_judge_root:
        base = collect(Path(args.baseline_run), Path(args.baseline_judge_root), args.scenes)
        blocks += [""] + emit_baseline_table(cur, base, args.label, args.baseline_label)
    text = "\n".join(blocks)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
