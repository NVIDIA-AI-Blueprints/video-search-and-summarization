#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Consolidate per-leg review artifacts into one report (the `report` job).

Reads every per-leg `review-<slug>.json` produced by review_agent.py, merges
across paradigms via review_findings.consolidate(), and emits three outputs:
  - skills-review-consolidated.json  (full machine-readable array)
  - skills-review-consolidated.md    (human triage doc, bucketed by category)
  - a compact sticky-comment body    (printed / handed to post_review_comment.py)

Stdlib only. The sticky-comment poster (post_review_comment.py) adds the marker
and upserts; this module only computes content (pure functions are unit-tested).

Env (main()):
    REVIEW_ARTIFACTS_DIR   dir holding the per-leg review-*.json (default: cwd)
    CONSOLIDATED_JSON      output path (default: skills-review-consolidated.json)
    CONSOLIDATED_MD        output path (default: skills-review-consolidated.md)
    COMMENT_BODY           output path for the comment body (default: review-comment.md)
    RUN_URL                optional Actions run URL for the comment footer
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import review_findings as rf  # noqa: E402

SEV_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
CATEGORY_ORDER = ["deploy-breaking", "security", "api-contract", "packaging-style", "tone"]
CATEGORY_LABEL = {
    "deploy-breaking": "Deploy-breaking",
    "security": "Security",
    "api-contract": "API contract",
    "packaging-style": "Packaging / authoring",
    "tone": "Tone / voice",
}
MAX_ROWS_PER_TABLE = 15
N_PARADIGMS = len(rf.PARADIGM_DEFAULT_CATEGORY)  # agreement is out of this many lenses


def load_legs(artifacts_dir: pathlib.Path) -> list[dict]:
    """Load + normalize findings from every review-*.json under artifacts_dir.

    A leg's artifact is {skill, paradigm, [status], findings:[...]}. Malformed
    or unreadable legs are skipped with a warning — one bad leg never sinks the
    report (advisory posture).
    """
    findings: list[dict] = []
    for path in sorted(artifacts_dir.rglob("review-*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"::warning::skills-review: skipping unreadable leg {path}: {exc}")
            continue
        skill = data.get("skill")
        paradigm = data.get("paradigm")
        if not skill or not paradigm:
            print(f"::warning::skills-review: leg {path} missing skill/paradigm")
            continue
        findings.extend(rf.normalize(paradigm, data.get("findings"), skill=skill))
    return findings


def _counts(consolidated: list[dict]) -> dict:
    c = {s: 0 for s in rf.SEVERITIES}
    for f in consolidated:
        c[f["severity"]] += 1
    return c


def _loc(f: dict) -> str:
    return f"`{f['file']}" + (f":{f['line']}" if f["line"] else "") + "`"


def _row(f: dict) -> str:
    title = f["title"].replace("|", "\\|")  # keep markdown table cells intact
    return (f"| {SEV_EMOJI[f['severity']]} {f['severity']} | "
            f"{f['agreement']}/{N_PARADIGMS} | {f['skill']} | {_loc(f)} | {title} |")


def build_comment(consolidated: list[dict], run_url: str | None = None) -> str:
    """Compact sticky-comment body — corroborated rows + verify list; tail stays in artifact."""
    cnt = _counts(consolidated)
    # Count only what the Verify table below actually shows (critical/high). Low
    # single-lens findings are needs_verification too, but they stay in the
    # artifact, so counting them here would overstate the displayed list.
    n_verify = sum(1 for f in consolidated
                   if f["needs_verification"] and f["severity"] in ("critical", "high"))
    n_skills = len({f["skill"] for f in consolidated})
    lines = [
        "## 🔬 Skills Review — 6-paradigm consolidation",
        "",
        f"**{len(consolidated)} findings across {n_skills} skill(s)** · "
        f"🔴 critical={cnt['critical']} · 🟠 high={cnt['high']} · "
        f"🟡 medium={cnt['medium']} · 🔵 low={cnt['low']}",
        f"**{n_verify} need verification** (critical or single-lens). Full report in the run artifact.",
    ]
    if not consolidated:
        lines.append("\n_No findings._")
        return "\n".join(lines)

    def table(title, rows):
        if not rows:
            return
        lines.extend(["", f"### {title}", "| Sev | Agree | Skill | File | Finding |",
                      "|---|---:|---|---|---|"])
        for f in rows[:MAX_ROWS_PER_TABLE]:
            lines.append(_row(f))
        if len(rows) > MAX_ROWS_PER_TABLE:
            lines.append(f"| | | | | …and {len(rows) - MAX_ROWS_PER_TABLE} more (see artifact) |")

    act_now = [f for f in consolidated if f["severity"] in ("critical", "high")]
    corroborated = [f for f in consolidated
                    if f["severity"] in ("medium", "low") and f["agreement"] >= 2]
    table("🔴 Critical / high (act now)", act_now)
    table("🟠 Corroborated (agreement ≥ 2)", corroborated)

    # Verify list in the comment = the dangerous-but-unconfirmed (critical/high
    # that are single-lens or critical). Low single-lens findings are pure long
    # tail — they stay in the artifact, not the scannable comment.
    verify = [f for f in consolidated
              if f["needs_verification"] and f["severity"] in ("critical", "high")]
    if verify:
        lines.extend(["", "### ⚠️ Verify before acting (critical or single-lens)"])
        for f in verify[:MAX_ROWS_PER_TABLE]:
            lines.append(f"- {SEV_EMOJI[f['severity']]} **{f['skill']}** {_loc(f)} — "
                         f"{f['title']} _(agreement {f['agreement']})_")
        if len(verify) > MAX_ROWS_PER_TABLE:
            lines.append(f"- …and {len(verify) - MAX_ROWS_PER_TABLE} more (see artifact)")

    foot = "_Skills Review (advisory)_"
    if run_url:
        foot += f" · [run]({run_url})"
    lines.extend(["", "---", foot])
    return "\n".join(lines)


def build_markdown(consolidated: list[dict]) -> str:
    """Full human triage doc, grouped by category bucket (like the manual triage doc)."""
    cnt = _counts(consolidated)
    lines = [
        "# Skills Review — consolidated report",
        "",
        f"{len(consolidated)} findings · 🔴 {cnt['critical']} · 🟠 {cnt['high']} · "
        f"🟡 {cnt['medium']} · 🔵 {cnt['low']} · "
        f"{sum(1 for f in consolidated if f['needs_verification'])} need verification",
    ]
    by_cat: dict[str, list[dict]] = {}
    for f in consolidated:
        by_cat.setdefault(f["category"], []).append(f)
    for cat in CATEGORY_ORDER + [c for c in by_cat if c not in CATEGORY_ORDER]:
        rows = by_cat.get(cat)
        if not rows:
            continue
        lines.extend(["", f"## {CATEGORY_LABEL.get(cat, cat)} ({len(rows)})"])
        for f in rows:
            verify = " ⚠️ VERIFY" if f["needs_verification"] else ""
            lines.append(
                f"\n### {SEV_EMOJI[f['severity']]} {f['severity'].upper()} · "
                f"agreement {f['agreement']} ({', '.join(f['paradigms'])}){verify}")
            lines.append(f"- **{f['skill']}** {_loc(f)} — {f['title']}")
            if f["rationale"]:
                lines.append(f"- Rationale: {f['rationale']}")
            if f["suggested_fix"]:
                lines.append(f"- Fix: {f['suggested_fix']}")
            if len(f["members"]) > 1:
                lines.append(f"- Raised by {len(f['members'])} members:")
                for m in f["members"]:
                    r = f" — {m['rationale']}" if m["rationale"] else ""
                    lines.append(f"  - `{m['paradigm']}` ({m['severity']}){r}")
    return "\n".join(lines) + "\n"


def main() -> int:
    artifacts = pathlib.Path(os.environ.get("REVIEW_ARTIFACTS_DIR", "."))
    consolidated = rf.consolidate(load_legs(artifacts))

    json_path = os.environ.get("CONSOLIDATED_JSON", "skills-review-consolidated.json")
    md_path = os.environ.get("CONSOLIDATED_MD", "skills-review-consolidated.md")
    body_path = os.environ.get("COMMENT_BODY", "review-comment.md")
    pathlib.Path(json_path).write_text(json.dumps(consolidated, indent=2) + "\n")
    pathlib.Path(md_path).write_text(build_markdown(consolidated))
    pathlib.Path(body_path).write_text(build_comment(consolidated, os.environ.get("RUN_URL")))

    cnt = _counts(consolidated)
    print(f"skills-review: {len(consolidated)} findings "
          f"(critical={cnt['critical']} high={cnt['high']} "
          f"medium={cnt['medium']} low={cnt['low']}) -> {json_path}, {md_path}, {body_path}")
    return 0  # advisory: never fail the gate


if __name__ == "__main__":
    sys.exit(main())
