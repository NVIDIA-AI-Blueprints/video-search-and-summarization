#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalize + consolidate skills-review findings across paradigms.

Stdlib only (mirrors the skills-nv-base post_comment.py constraint). Two jobs:

  canonical_finding(raw, skill, paradigm)
      Coerce one paradigm's raw finding into the canonical schema
      (findings-schema.json). Accepts severity as a word
      (critical/high/medium/low) or a P-code (P0..P3) or a [P1]/[P2] marker.

  consolidate(findings)
      Dedupe across paradigms by (skill, file, ~fuzzy title), then for each
      cluster compute:
        agreement  = count of DISTINCT paradigms (NOT raw member count)
        severity   = MAX across members (worst-case wins)
        needs_verification = (severity == critical) OR (agreement == 1)
      These encode the manual-triage rules: cross-paradigm agreement = high
      confidence; always verify criticals and single-lens outliers.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

SEVERITIES = ("critical", "high", "medium", "low")
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}  # 0 = most severe
# One P-code map serves both ce (P0..P3) and gstack/codex ([P1]/[P2]) markers:
# P0->critical, P1->high, P2->medium, P3->low.
PCODE_TO_SEV = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low", "P4": "low"}
CATEGORIES = ("deploy-breaking", "api-contract", "security", "packaging-style", "tone")
# Soft default category per paradigm (consolidation majority-vote overrides it).
PARADIGM_DEFAULT_CATEGORY = {
    "review": "deploy-breaking",
    "gstack-review": "deploy-breaking",
    "codex": "deploy-breaking",
    "ce-code-review": "deploy-breaking",
    "ce-doc-review": "packaging-style",
    "best-practices": "packaging-style",
}
FUZZY_THRESHOLD = 0.82      # SequenceMatcher ratio on the bag-of-words key
OVERLAP_THRESHOLD = 0.6     # |A∩B| / min(|A|,|B|) on title token sets


def norm_severity(value) -> str:
    """Accept a word, a P-code, or a [P1]/[P2] marker -> canonical severity."""
    s = str(value or "").strip().strip("[]").upper()
    if s.lower() in SEV_ORDER:
        return s.lower()
    if s in PCODE_TO_SEV:
        return PCODE_TO_SEV[s]
    return "medium"  # unknown -> middle, never crash


def _int_line(value) -> int:
    try:
        n = int(value)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def canonical_finding(raw: dict, *, skill: str, paradigm: str) -> dict | None:
    """Coerce one raw finding into the canonical schema, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    file = str(raw.get("file") or "").strip()
    if not title or not file:
        return None  # a finding with no title/file can't be deduped or shown
    category = raw.get("category")
    if category not in CATEGORIES:
        category = PARADIGM_DEFAULT_CATEGORY.get(paradigm, "deploy-breaking")
    conf = raw.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "skill": skill,
        "paradigm": paradigm,
        "file": file,
        "line": _int_line(raw.get("line")),
        "title": title,
        "severity": norm_severity(raw.get("severity")),
        "category": category,
        "rationale": str(raw.get("rationale") or "").strip(),
        "suggested_fix": str(raw.get("suggested_fix") or "").strip(),
        "confidence": conf,
    }


def normalize(paradigm: str, raw_findings, *, skill: str) -> list[dict]:
    """Coerce a paradigm's raw finding list into canonical findings (drops unusable)."""
    out = []
    for raw in raw_findings or []:
        f = canonical_finding(raw, skill=skill, paradigm=paradigm)
        if f is not None:
            out.append(f)
    return out


def norm_title(title: str) -> str:
    """Order-independent bag-of-words key for fuzzy title matching."""
    t = re.sub(r"[`'\"]", "", title.lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(sorted(set(t.split())))


def _similar(a: str, b: str) -> bool:
    na, nb = norm_title(a), norm_title(b)
    if na == nb:
        return True
    # Token-overlap (containment) catches paraphrases that share the defect's
    # nouns ("container vss-foo-alerts ...") but reorder/reword around them —
    # where SequenceMatcher on the sorted string alone falls short. Distinct
    # defects on the same file share ~no tokens, so this won't over-merge them.
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and len(ta & tb) / min(len(ta), len(tb)) >= OVERLAP_THRESHOLD:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= FUZZY_THRESHOLD


def _cluster(group: list[dict]) -> list[list[dict]]:
    """Greedy union within one (skill, file) group by fuzzy title."""
    clusters: list[list[dict]] = []
    for f in group:
        for c in clusters:
            if any(_similar(f["title"], m["title"]) for m in c):
                c.append(f)
                break
        else:
            clusters.append([f])
    return clusters


def _confidence_tier(agreement: int) -> str:
    if agreement >= 3:
        return "high"
    if agreement == 2:
        return "medium"
    return "low"


def consolidate(findings: list[dict]) -> list[dict]:
    """Merge canonical findings into ranked consolidated findings."""
    groups: dict[tuple, list[dict]] = {}
    for f in findings:
        groups.setdefault((f["skill"], f["file"]), []).append(f)

    out: list[dict] = []
    for (skill, file), group in groups.items():
        for cluster in _cluster(group):
            paradigms = sorted({m["paradigm"] for m in cluster})
            agreement = len(paradigms)
            severity = min((m["severity"] for m in cluster), key=lambda s: SEV_ORDER[s])
            lines = [m["line"] for m in cluster if m["line"] > 0]
            line = min(lines) if lines else 0
            # category: majority vote; tie broken by the most-severe member's category
            cat_counts = Counter(m["category"] for m in cluster)
            top = max(cat_counts.values())
            tied = [c for c, n in cat_counts.items() if n == top]
            if len(tied) == 1:
                category = tied[0]
            else:
                category = min(cluster, key=lambda m: SEV_ORDER[m["severity"]])["category"]
            # representative title/rationale: the longest-titled member (most descriptive)
            rep = max(cluster, key=lambda m: len(m["title"]))
            rationale = max((m["rationale"] for m in cluster), key=len, default="")
            fix = next((m["suggested_fix"] for m in cluster if m["suggested_fix"]), "")
            out.append({
                "key": f"{skill}|{file}|{norm_title(rep['title'])}",
                "skill": skill,
                "file": file,
                "line": line,
                "title": rep["title"],
                "severity": severity,
                "category": category,
                "agreement": agreement,
                "paradigms": paradigms,
                "confidence_tier": _confidence_tier(agreement),
                "needs_verification": (severity == "critical") or (agreement == 1),
                "rationale": rationale,
                "suggested_fix": fix,
                "members": cluster,
            })

    out.sort(key=lambda c: (SEV_ORDER[c["severity"]], -c["agreement"], c["skill"], c["file"]))
    return out
