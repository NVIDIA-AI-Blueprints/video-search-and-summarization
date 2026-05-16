#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Driver for the Skills NV-BASE GitHub Actions workflow.

Runs `nv-base validate --type skill --external --report json` against the
skill root, then emits GitHub Actions annotations from the structured
JSON report. Exits non-zero when validate reports failure.

Why `validate` and not `skills-check`:
  * `--profile external` already demotes `author_missing` HIGH → MEDIUM
    via the bundled external.yaml policy, so no env-var allow-list is
    needed.
  * `--report json` is deterministic; no CLI-text scraping.
  * `--checks` lets us scope to schema + security families without the
    LLM-backed Tier-2/3 stages (those need an Anthropic credential).

Stdlib only. The runner is expected to have Python 3.12 and nv-base
pre-installed; nothing else.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
            "missing or not executable. Bootstrap the runner per "
            ".github/skills-nv-base/README.md.",
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


# ── Annotation helpers ─────────────────────────────────────────────────

# Top-level report shape (nv-base validate --report json):
#   {
#     "overall_passed": bool,
#     "results": [
#       {
#         "validator": "SCHEMA" | "SECURITY" | "SECRETS" | "UNICODE" | "PII",
#         "passed": bool,
#         "findings": [
#           {"severity": "critical|high|medium|low|info",
#            "check_name": str, "message": str,
#            "file_path": "[skill-name] relative/path.md",
#            "line_number": int, "suggestion": str, ...},
#           ...
#         ],
#         "summary": {...}
#       },
#       ...
#     ]
#   }
#
# `severity_counts` at the top level can be stale (observed all-zero
# despite critical findings in results[].findings); we recount from the
# findings themselves rather than trust it.


def _normalize_path(raw: str) -> str:
    """Turn '[skill-name] references/x.md' into 'skills/skill-name/references/x.md'.
    Returns raw if the prefix shape isn't present.
    """
    if not raw:
        return "skills/"
    s = raw.strip()
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            skill = s[1:end].strip()
            rest = s[end + 1:].lstrip()
            return f"skills/{skill}/{rest}" if rest else f"skills/{skill}/"
    return s


def annotate_finding(validator: str, f: dict) -> str:
    """Emit one GHA annotation. Return the finding's severity (lowercased)."""
    sev = (f.get("severity") or "info").lower()
    check = f.get("check_name") or f.get("check") or "unknown"
    msg = f.get("message") or ""
    suggestion = f.get("suggestion") or ""
    full_msg = f"{msg} — {suggestion}" if suggestion else msg
    path = _normalize_path(f.get("file_path") or "")
    line = f.get("line_number") or 1
    level = "error" if sev in ("critical", "high") else "warning"
    print(
        f"::{level} file={path},line={line}::"
        f"[NV-BASE/{validator}/{check}] {full_msg}",
        flush=True,
    )
    return sev


def emit_from_report(report: dict) -> int:
    """Walk the report and emit annotations. Return blocking count."""
    blocking = 0
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    for v in report.get("results", []) or []:
        validator = v.get("validator") or "?"
        for f in v.get("findings", []) or []:
            sev = annotate_finding(validator, f)
            counts[sev] = counts.get(sev, 0) + 1
            if sev in ("critical", "high"):
                blocking += 1

    print(
        f"\nNV-BASE summary: critical={counts['critical']} "
        f"high={counts['high']} medium={counts['medium']} "
        f"low={counts['low']}  blocking={blocking}",
        flush=True,
    )
    return blocking


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: run_check.py <skill-root>", file=sys.stderr)
        sys.exit(2)
    skill_root = sys.argv[1]

    nv_base = find_nv_base()

    out_dir = Path(tempfile.mkdtemp(prefix="nvbase-"))
    cmd = [
        nv_base, "validate", skill_root,
        "--type", "skill",
        "--external",
        "--no-dedup",
        "--checks", "schema,secrets,pii,unicode",
        "--report", "json",
        "-o", str(out_dir),
        "-c",  # continue on failure so all issues land in the report
    ]
    print(f"::group::nv-base {' '.join(cmd[1:])}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, end="", flush=True)
    print(r.stderr, end="", flush=True)
    print("::endgroup::", flush=True)

    report_files = sorted(out_dir.glob("*.json"))
    if not report_files:
        print(
            "::error::nv-base did not write a JSON report. "
            f"Exit code {r.returncode}. See log above.",
            flush=True,
        )
        sys.exit(r.returncode or 2)

    report = json.loads(report_files[-1].read_text())
    blocking = emit_from_report(report)

    if not report.get("overall_passed", True) or blocking:
        print(
            f"::error::NV-BASE validate failed: {blocking} blocking "
            "high/critical finding(s).",
            flush=True,
        )
        sys.exit(1)
    print("NV-BASE validate: 0 blocking findings.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
