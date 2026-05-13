#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Driver for the Skills NV-BASE GitHub Actions workflow.

Steps
-----
1. Locate the pre-installed `nv-base` binary. Try in order:
   a. $NVBASE_BIN (workflow sets this to the pre-install path).
   b. `nv-base` on PATH (in case the bin dir is already in PATH).

   The driver does NOT install nv-base at run time. The self-hosted
   runner is bootstrapped once by an operator with access to NV-BASE's
   distribution; see .github/skills-nv-base/README.md.

2. Run `nv-base skills-check <skill-root>` and capture stdout+stderr.

3. Parse SCHEMA-HIGH findings, filter out the IDs listed in
   $NVBASE_ALLOW_HIGH (comma-separated; defaults to `author_missing`,
   which is template-omitted on purpose).

4. Emit GitHub Actions `::error file=…::msg` annotations for every
   blocking finding. Print a summary line. Exit 1 if any remain, else 0.

Stdlib-only. The runner is expected to have Python 3.12 and nv-base
pre-installed; nothing else.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── nv-base resolution ─────────────────────────────────────────────────


def find_nv_base() -> str:
    """Return the pre-installed `nv-base` executable path, or exit 2."""
    explicit = os.environ.get("NVBASE_BIN", "").strip()
    if explicit:
        if Path(explicit).is_file() and os.access(explicit, os.X_OK):
            print(f"::notice::nv-base resolved via NVBASE_BIN: {explicit}",
                  flush=True)
            return explicit
        print(
            f"::error::NVBASE_BIN is set to {explicit!r} but the file is "
            f"missing or not executable. Bootstrap the runner per "
            f".github/skills-nv-base/README.md.",
            flush=True,
        )
        sys.exit(2)

    found = shutil.which("nv-base")
    if found:
        print(f"::notice::nv-base found on PATH: {found}", flush=True)
        return found

    print(
        "::error::nv-base not available on this runner. Neither $NVBASE_BIN "
        "nor PATH resolves to a `nv-base` binary. Bootstrap the runner per "
        ".github/skills-nv-base/README.md.",
        flush=True,
    )
    sys.exit(2)


# ── nv-base output parser ──────────────────────────────────────────────

# The skills-check output groups findings under
#   [FAIL] Validation failed
#     •  [SCHEMA-HIGH] <message> in skills/<name>/<file>
#     [WARN]  [SCHEMA-MEDIUM] <message> in skills/<name>/<file>
#     [WARN]  [SCHEMA-LOW]    <message> in skills/<name>/<file>
#
# Some lines wrap after "in" so the file path is on the next line.
# `_join_wrapped` folds those continuations back so the parser sees
# one logical line per finding.

FINDING_RE = re.compile(
    r"\[SCHEMA-(?P<sev>HIGH|MEDIUM|LOW)\]\s+(?P<rest>.+)$"
)

# Crude finding-ID guesser from the message text. NV-BASE doesn't print
# a stable check-ID in skills-check output (only the message), so we
# map the common ones back to the canonical names from `nv-base
# validate --type skill`. New rules: add a regex here.
ID_MAP = [
    (re.compile(r"Author not specified in metadata", re.I), "author_missing"),
    (re.compile(r"description must not contain XML tags", re.I), "frontmatter_field"),
    (re.compile(r"SKILL\.md has \d+ lines", re.I), "line_count"),
    (re.compile(r"Missing recommended section", re.I), "body_recommended_section"),
    (re.compile(r"Unexpected '\w+' in skill root", re.I), "unexpected_file"),
]


def finding_id(msg: str) -> str:
    for pat, name in ID_MAP:
        if pat.search(msg):
            return name
    return "unknown"


def _join_wrapped(text: str):
    """Yield logical lines, joining continuation lines onto their parent.
    A "new" finding line starts with `•`, `[WARN]`, `[FAIL]`, `[OK]`, etc.
    Anything else is a wrapped continuation of the previous line.
    """
    cur = ""
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip():
            if cur:
                yield cur
                cur = ""
            continue
        stripped = s.lstrip()
        starts_finding = (
            stripped.startswith("•")
            or stripped.startswith("[")
            or stripped.startswith("- ")
        )
        if starts_finding:
            if cur:
                yield cur
            cur = s
        else:
            if cur:
                cur += " " + stripped
            else:
                cur = s
    if cur:
        yield cur


def parse_findings(text: str):
    findings = []
    for line in _join_wrapped(text):
        m = FINDING_RE.search(line)
        if not m:
            continue
        sev = m.group("sev")
        rest = m.group("rest").strip()
        # split on last " in " to separate message from path (the path
        # field always reads "… in skills/<name>/<file>")
        if " in " in rest:
            msg, path = rest.rsplit(" in ", 1)
        else:
            msg, path = rest, ""
        path = path.strip().rstrip(",.")
        # Drop trailing periods from the message
        msg = msg.rstrip(",.").strip()
        findings.append({
            "sev": sev,
            "id": finding_id(msg),
            "msg": msg,
            "file": path,
        })
    return findings


# ── Annotations + exit ─────────────────────────────────────────────────

def emit_annotations(findings, allow_high_ids):
    blocking = 0
    by_sev = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        by_sev[f["sev"]] = by_sev.get(f["sev"], 0) + 1
        if f["sev"] != "HIGH":
            continue  # only HIGH gates today
        if f["id"] in allow_high_ids:
            print(
                f"::warning file={f['file'] or 'skills/'}::"
                f"[NV-BASE allow-listed] {f['id']}: {f['msg']}",
                flush=True,
            )
            continue
        blocking += 1
        loc = f["file"] or "skills/"
        print(
            f"::error file={loc},line=1::"
            f"[NV-BASE {f['id']}] {f['msg']}",
            flush=True,
        )
    print(
        f"\nNV-BASE summary: HIGH={by_sev['HIGH']} "
        f"MEDIUM={by_sev['MEDIUM']} LOW={by_sev['LOW']}  "
        f"blocking={blocking}",
        flush=True,
    )
    return blocking


def main():
    if len(sys.argv) < 2:
        print("usage: run_check.py <skill-root>", file=sys.stderr)
        sys.exit(2)
    skill_root = sys.argv[1]

    allow = {
        s.strip()
        for s in os.environ.get("NVBASE_ALLOW_HIGH", "author_missing").split(",")
        if s.strip()
    }
    print(f"::notice::NVBASE_ALLOW_HIGH = {sorted(allow)}", flush=True)

    nv_base = find_nv_base()

    print(f"::group::nv-base skills-check {skill_root}", flush=True)
    r = subprocess.run(
        [nv_base, "skills-check", skill_root],
        capture_output=True, text=True,
    )
    print(r.stdout, end="", flush=True)
    print(r.stderr, end="", flush=True)
    print("::endgroup::", flush=True)

    findings = parse_findings(r.stdout + "\n" + r.stderr)
    blocking = emit_annotations(findings, allow)

    if blocking:
        print(
            f"::error::NV-BASE skills-check failed: {blocking} blocking "
            f"HIGH finding(s). Fix or add to NVBASE_ALLOW_HIGH (in "
            f".github/workflows/skills-nv-base.yml) if intentional.",
            flush=True,
        )
        sys.exit(1)
    print("NV-BASE skills-check: 0 blocking HIGH findings.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
