#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
skill_compliance_check.py
=========================
Validates skill directories against the Agent Skills Playbook standard,
modified for this repo. Vendored from `agent_skills_playbook` with
the modifications listed below.

Modifications vs upstream
-------------------------
- STR-004 (references/README.md required) removed — not part of the
  agentskills.io spec and not used by Anthropic's reference skills.
- STR-003 softened to WARN: "evals/ directory not found" only.
  No filename restriction (upstream required `evals/evals.json`, which
  is one community runner's convention, not a standard).
- EVAL-001..005 family removed entirely. The repo's existing
  `skills-eval` workflow is the source of truth for actual eval
  execution; this script stays Tier-1 schema/static only.
- REQUIRED_FM_FIELDS trimmed to ["name", "description"] to match
  https://agentskills.io/specification. Upstream's flat `owner`,
  `service`, `version`, `reviewed` requirement does not match the
  spec this repo's skills follow.
- APPROVED_TEAM_PREFIXES kept as-is (already VSS-flavored).

Usage
-----
# Check all skills in a directory
python skill_compliance_check.py --skills-dir path/to/skills/

# Check a single skill by folder name
python skill_compliance_check.py --skills-dir path/to/skills/ --skill inference-job-submit

# Strict mode: treat warnings as errors (useful for CI on new skills)
python skill_compliance_check.py --skills-dir path/to/skills/ --strict

Exit codes
----------
0  All checks passed (zero ERRORs)
1  One or more ERROR-level violations found

Rule categories
---------------
STR   Structural (required files, line limits)
NAM   Naming conventions (skill-naming-guideline rules 1–7)
FM    Frontmatter fields and formats (incl. description quality)
SEC   Security (credential scanning, PII detection, Unicode smuggling)

Skill-naming-guideline rule mapping
-----------------------------------
Playbook rule                                  Compliance code(s)
1. Verb + object (no noun blob)                NAM-003
2. Outcome over implementation                  FM-011 (description-side)
3. Team prefix in leading position              NAM-003 / NAM-006
4. Disambiguate sibling skills                  NAM-007 (collision detection)
5. No personal namespacing                      NAM-001 (kebab) + reviewer
6. Full words over cryptic acronyms             NAM-003 (verb whitelist)
7. ≤ 4 tokens / ≤ 30 characters                 NAM-004 / NAM-005
Description: "Use when…" with ≥ 3 phrases       FM-010
Description: not implementation-led             FM-011

NV-BASE alignment
-----------------
SEC-001  Credential / secret scanning        (NV-BASE §2.1 — Gitleaks equivalent)
SEC-002  PII detection (emails, IPs)         (NV-BASE §2.2 — data residency)
SEC-003  Unicode / Trojan-Source smuggling   (NV-BASE §2.3 — supply-chain attack)
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# ── Severity ─────────────────────────────────────────────────────────────────

ERROR   = "ERROR"
WARNING = "WARNING"
INFO    = "INFO"
PASS    = "PASS"


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    severity: str          # ERROR | WARNING | INFO
    rule: str              # e.g. "SEC-001"
    message: str
    file: Optional[str] = None
    line: Optional[int] = None


@dataclass
class SkillResult:
    skill_name: str
    skill_path: Path
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == WARNING]


# ── Constants ─────────────────────────────────────────────────────────────────

# Required frontmatter fields per https://agentskills.io/specification.
# Upstream playbook also required owner/service/version/reviewed; those are
# kept as conditional checks (FM-004..007 fire only if the field is present),
# never required.
REQUIRED_FM_FIELDS = ["name", "description"]

VALID_DATA_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}

GENERIC_SKILL_NAMES = {
    "my-skill", "util", "utils", "helper", "helpers",
    "service1-thing", "test", "new-skill", "skill", "example",
}

KEBAB_CASE_RE  = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
SEMVER_RE      = re.compile(r"^\d+\.\d+\.\d+$")
DATE_RE        = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ── Naming guideline (skill-naming-guideline rules 1–7) ─────────────────────
# These constants mirror the playbook's "Approved verbs", "Approved prefixes",
# and "Reserved bare names" lists. Edit them via PR review when the playbook
# gets updated; do NOT silently drift.

# Rule 1 — leading verb (after optional team prefix). Names must start with one
# of these. Add new verbs to the playbook first, then here.
APPROVED_VERBS = {
    "create", "generate", "deploy", "analyze", "bootstrap", "install",
    "setup", "migrate", "inspect", "bump", "audit", "fix", "review",
    "list", "run", "profile", "manage", "scaffold", "summarize",
    "search", "query", "ask", "call", "ingest", "tune", "format",
}

# Rule 3 — team prefixes that may lead the name. Hyphenated prefixes like
# "rtvi-cv" are matched as multi-token leading sequences.
APPROVED_TEAM_PREFIXES = {
    "vss", "deepstream", "vios", "rtvi-cv", "bb", "lvs", "amc", "l4t-mm",
}

# Single-token names that almost always collide in a flat skill namespace.
# If a skill folder is just one of these words, NAM-006 fires as ERROR.
RESERVED_BARE_NAMES = {
    "deploy", "deployment", "install", "setup", "build",
    "test", "tests", "review", "audit", "release", "publish",
    "report", "reports", "alerts", "alert",
    "config", "configure", "summary", "summarize",
    "rebase", "merge", "commit", "push", "pull",
    "log", "logs", "status",
}

# Rule 7
MAX_NAME_TOKENS = 4
MAX_NAME_CHARS  = 30

# Trigger-clause keywords (used by FM-010 to find the "Use when…" substring)
TRIGGER_CLAUSE_KEYWORDS = (
    "use when", "trigger when", "use this skill when", "use this when",
    "trigger this skill when",
)

# Description anti-patterns (FM-011) — leading sentence reads as
# implementation-first rather than outcome-first.
IMPLEMENTATION_LEAD_PATTERNS = [
    r"^\s*Uses?\s+the\s+\w+\s+(?:API|REST|endpoint|library|module|server)",
    r"^\s*Calls?\s+(?:into\s+)?the\s+\w+",
    r"^\s*Wraps?\s+the\s+\w+",
    r"^\s*Integrates?\s+with\s+\w+\s+(?:to|in order to)",
    r"^\s*A\s+(?:thin\s+)?wrapper\s+(?:around|over|for)",
    r"^\s*Invokes?\s+(?:the\s+)?\w+\s+endpoint",
]

SCAN_EXTENSIONS = {".md", ".yml", ".yaml", ".py", ".sh", ".json", ".txt", ".env"}

# ── SEC-002: PII detection ────────────────────────────────────────────────────
# Email regex — intentionally broad; excludes obvious placeholder domains
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
# Placeholder email domains that are safe to ignore
EMAIL_SAFE_DOMAINS = {
    "example.com", "example.org", "example.net",
    "domain.com", "yourdomain.com", "company.com",
    "placeholder.com", "test.com", "acme.com",
}
# IPv4 address (excludes loopback, link-local, and private RFC 1918 ranges
# which are low-risk in docs — only real routable IPs are flagged)
IPV4_ROUTABLE_RE = re.compile(
    r"\b(?!(?:10|127)\.\d+\.\d+\.\d+)"      # exclude 10.x and 127.x
    r"(?!172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)"  # exclude 172.16-31.x
    r"(?!192\.168\.\d+\.\d+)"               # exclude 192.168.x
    r"(?!0\.0\.0\.0)"                        # exclude 0.0.0.0
    r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# ── SEC-003: Unicode smuggling (Trojan Source / CVE-2021-42574) ───────────────
# Bidirectional control characters that can reverse apparent code direction
BIDI_CONTROL_CHARS = {
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202b",  # RIGHT-TO-LEFT EMBEDDING
    "\u202c",  # POP DIRECTIONAL FORMATTING
    "\u202d",  # LEFT-TO-RIGHT OVERRIDE
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE (highest risk)
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\u2067",  # RIGHT-TO-LEFT ISOLATE
    "\u2068",  # FIRST STRONG ISOLATE
    "\u2069",  # POP DIRECTIONAL ISOLATE
}
# Zero-width / invisible characters that can hide malicious content
INVISIBLE_CHARS = {
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u2060",  # WORD JOINER
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM outside file start)
    "\u00ad",  # SOFT HYPHEN
}
UNICODE_SMUGGLING_CHARS = BIDI_CONTROL_CHARS | INVISIBLE_CHARS
UNICODE_SMUGGLING_RE = re.compile(
    "[" + "".join(re.escape(c) for c in sorted(UNICODE_SMUGGLING_CHARS)) + "]"
)
CHAR_NAMES = {
    "\u202a": "LEFT-TO-RIGHT EMBEDDING (U+202A)",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING (U+202B)",
    "\u202c": "POP DIRECTIONAL FORMATTING (U+202C)",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE (U+202D)",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE (U+202E)",
    "\u2066": "LEFT-TO-RIGHT ISOLATE (U+2066)",
    "\u2067": "RIGHT-TO-LEFT ISOLATE (U+2067)",
    "\u2068": "FIRST STRONG ISOLATE (U+2068)",
    "\u2069": "POP DIRECTIONAL ISOLATE (U+2069)",
    "\u200b": "ZERO WIDTH SPACE (U+200B)",
    "\u200c": "ZERO WIDTH NON-JOINER (U+200C)",
    "\u200d": "ZERO WIDTH JOINER (U+200D)",
    "\u2060": "WORD JOINER (U+2060)",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM (U+FEFF)",
    "\u00ad": "SOFT HYPHEN (U+00AD)",
}

# Patterns that look like real credentials
CREDENTIAL_PATTERNS: List[Tuple[str, str]] = [
    (r"-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----",           "private key"),
    (r"(?i)(api[_\-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_\-]{20,}", "API key"),
    (r"(?i)password\s*[:=]\s*['\"][^'\"]{6,}['\"]",                "hardcoded password"),
    (r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*",                        "Bearer token"),
    (r"(?i)secret[_\-]?key\s*[:=]\s*['\"]?[A-Za-z0-9+/=_\-]{20,}","secret key"),
    (r"(?i)\btoken\s*[:=]\s*['\"]?[A-Za-z0-9+/=_\-.]{20,}['\"]?",  "hardcoded token"),
    (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{10,}", "JWT token"),
    (r"sk-[A-Za-z0-9]{20,}",                                        "OpenAI-style key"),
    (r"(?i)aws[_\-]?(access[_\-]?key|secret)[_\-]?id\s*[:=]\s*['\"]?[A-Za-z0-9+/=]{16,}", "AWS credential"),
    (r"(?i)Authorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{8,}",        "Basic auth credential"),
    (r"(?i)PRIVATE_KEY\s*[:=]\s*['\"][^'\"]{20,}['\"]",            "private key value"),
]

# Lines matching these are considered safe (placeholders / env var references)
SAFE_LINE_PATTERNS: List[str] = [
    r"<YOUR_TOKEN>",
    r"<token>",
    r"<[A-Z_]+>",               # any <PLACEHOLDER>
    r"\$\{[A-Z_]+\}",           # ${ENV_VAR}
    r"\$[A-Z][A-Z_]{2,}",       # $ENV_VAR (e.g. $SERVICE_API_TOKEN)
    r"vault kv get",             # vault retrieval command, not a secret itself
    r"^\s*#",                    # full-line comments (anchored; inline '#' must not exempt the whole line)
    r"your[_\-]",                # "your_api_key", "your-token" etc.
    r"\b(example|placeholder|dummy|fake|sample)\b",  # whole-word only; NOT "test"
]
SAFE_LINE_RE = re.compile("|".join(SAFE_LINE_PATTERNS), re.IGNORECASE)

# ── Frontmatter parser (stdlib only, no PyYAML needed) ───────────────────────

def parse_frontmatter(content: str) -> Optional[dict]:
    """
    Extract simple key: value pairs from YAML frontmatter (between --- markers).
    Handles multi-line block scalars by collapsing them into a single string.
    Does not support nested keys — only top-level scalar fields are needed.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None

    result: dict = {}
    current_key: Optional[str] = None
    collecting: List[str] = []

    def flush():
        if current_key is not None:
            result[current_key] = " ".join(collecting).strip().strip("\"'")

    for line in lines[1:end_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Continuation of a block scalar (indented line under a key ending with >)
        if current_key and line.startswith((" ", "\t")) and ":" not in line:
            collecting.append(stripped)
            continue
        flush()
        collecting = []
        current_key = None
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip().lstrip(">").strip().strip("\"'")
            if val:
                collecting.append(val)

    flush()
    return result


# ── Credential scanner ────────────────────────────────────────────────────────

def scan_file_for_credentials(path: Path) -> List[Tuple[int, str, str]]:
    """
    Return (line_number, snippet, credential_type) for lines that look like
    they contain real credentials. Skips placeholder / env-var lines.
    """
    hits: List[Tuple[int, str, str]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return hits

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or SAFE_LINE_RE.search(line):
            continue
        for pattern, cred_type in CREDENTIAL_PATTERNS:
            m = re.search(pattern, line)
            if m:
                snippet = m.group(0)[:80].replace("\n", " ")
                hits.append((lineno, snippet, cred_type))
                break  # one finding per line is enough
    return hits


# ── PII scanner ──────────────────────────────────────────────────────────────

def scan_file_for_pii(path: Path) -> List[Tuple[int, str, str]]:
    """
    Return (line_number, snippet, pii_type) for lines that appear to contain
    real email addresses or routable IPv4 addresses. Placeholder / example
    domains are excluded. Returns WARNING-level findings only.
    """
    hits: List[Tuple[int, str, str]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return hits

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or SAFE_LINE_RE.search(line):
            continue

        # Email addresses
        for m in EMAIL_RE.finditer(line):
            addr = m.group(0)
            domain = addr.split("@", 1)[1].lower()
            if domain not in EMAIL_SAFE_DOMAINS:
                hits.append((lineno, addr[:80], "email address"))
                break  # one finding per line

        # IPv4 routable addresses (skip lines already flagged for email)
        if not any(h[0] == lineno for h in hits):
            m = IPV4_ROUTABLE_RE.search(line)
            if m:
                hits.append((lineno, m.group(0), "routable IPv4 address"))

    return hits


# ── Unicode smuggling scanner ─────────────────────────────────────────────────

def scan_file_for_unicode_smuggling(path: Path) -> List[Tuple[int, str, str]]:
    """
    Detect bidirectional control characters and zero-width invisible characters
    (CVE-2021-42574 / Trojan Source) in skill files. These can be used to make
    malicious code appear harmless to human reviewers.
    Returns (line_number, char_description, attack_category).
    """
    hits: List[Tuple[int, str, str]] = []
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return hits

    # Skip files with a leading UTF-8 BOM — only flag embedded BOM later
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        return hits

    # Strip a legitimate leading BOM (first char of file)
    if text.startswith("\ufeff"):
        text = text[1:]

    for lineno, line in enumerate(text.splitlines(), start=1):
        m = UNICODE_SMUGGLING_RE.search(line)
        if m:
            char = m.group(0)
            name = CHAR_NAMES.get(char, f"U+{ord(char):04X}")
            category = "bidi-control" if char in BIDI_CONTROL_CHARS else "invisible-char"
            hits.append((lineno, name, category))

    return hits


# ── Individual checks ─────────────────────────────────────────────────────────

def analyse_name_structure(name: str) -> dict:
    """
    Decompose a skill name into (team_prefix, verb, object_tokens) per the
    skill-naming-guideline structural pattern: ``[<team-prefix>-]<verb>-<object>``.

    Hyphenated team prefixes such as ``rtvi-cv`` are matched as a multi-token
    leading sequence so that ``rtvi-cv-call-api`` decomposes into
    ``team_prefix='rtvi-cv'``, ``verb='call'``, ``object_tokens=['api']``.
    """
    tokens = name.split("-")
    info = {
        "tokens": tokens,
        "team_prefix": None,
        "verb": None,
        "verb_index": 0,
        "object_tokens": [],
    }

    # Match the longest prefix first so "rtvi-cv" wins over "rtvi".
    for prefix in sorted(APPROVED_TEAM_PREFIXES, key=lambda p: -len(p)):
        prefix_tokens = prefix.split("-")
        if tokens[: len(prefix_tokens)] == prefix_tokens:
            info["team_prefix"] = prefix
            info["verb_index"] = len(prefix_tokens)
            break

    if info["verb_index"] < len(tokens):
        info["verb"] = tokens[info["verb_index"]]
        info["object_tokens"] = tokens[info["verb_index"] + 1 :]

    return info


def check_naming(skill_path: Path, result: SkillResult) -> None:
    """NAM-* — folder-name conventions (skill-naming-guideline rules 1, 3, 7)."""
    name = skill_path.name

    # NAM-001: kebab-case
    if not KEBAB_CASE_RE.match(name):
        result.findings.append(Finding(
            ERROR, "NAM-001",
            f"Folder name '{name}' is not kebab-case. "
            "Use lowercase letters, numbers, and hyphens only (e.g. inference-job-submit).",
        ))
        # Skip structural checks on a malformed name — they'd produce noise.
        return

    # NAM-002: not a generic placeholder
    if name in GENERIC_SKILL_NAMES:
        result.findings.append(Finding(
            ERROR, "NAM-002",
            f"Folder name '{name}' is too generic. "
            "Use a descriptive <team-prefix>-<verb>-<object> pattern (e.g. vss-deploy-profile).",
        ))

    info = analyse_name_structure(name)
    tokens = info["tokens"]

    # NAM-006: bare reserved names guaranteed to collide in a flat namespace
    if len(tokens) == 1 and name in RESERVED_BARE_NAMES:
        result.findings.append(Finding(
            ERROR, "NAM-006",
            f"Folder name '{name}' is a reserved single-word name that will collide "
            "with any other skill of the same name in an aggregated namespace. "
            "Use the '<team-prefix>-<verb>-<object>' pattern instead "
            "(e.g. 'vss-deploy-profile' rather than 'deploy', "
            "'vss-generate-video-report' rather than 'report').",
        ))

    # NAM-003: leading token (after optional team prefix) must be an approved verb.
    # Three failure modes:
    #   (a) name is only a team prefix, no verb / object  →  e.g. 'vios'
    #   (b) verb-position token isn't in the approved list →  e.g. 'video-search'
    #   (c) prefix consumed by mistake, leaving no object →  handled by (a)
    if info["team_prefix"] and info["verb"] is None:
        result.findings.append(Finding(
            WARNING, "NAM-003",
            f"Skill name '{name}' is only a team prefix ('{info['team_prefix']}') with "
            "no verb or object. Expected '<team-prefix>-<verb>-<object>' "
            "(e.g. 'vss-call-vios-api' rather than 'vios').",
        ))
    elif info["verb"] and info["verb"] not in APPROVED_VERBS:
        if info["team_prefix"]:
            hint = (
                f"Token at position {info['verb_index'] + 1} ('{info['verb']}') "
                f"after team prefix '{info['team_prefix']}-' is not an approved verb."
            )
        else:
            hint = (
                f"Leading token '{info['verb']}' is not an approved verb. "
                "Add a team prefix and a verb, or replace with a verb-led name."
            )
        result.findings.append(Finding(
            WARNING, "NAM-003",
            hint + " Expected '<team-prefix>-<verb>-<object>' or '<verb>-<object>'. "
            f"Approved verbs: {', '.join(sorted(APPROVED_VERBS))}.",
        ))

    # NAM-004: ≤ MAX_NAME_TOKENS tokens
    if len(tokens) > MAX_NAME_TOKENS:
        result.findings.append(Finding(
            WARNING, "NAM-004",
            f"Skill name '{name}' has {len(tokens)} tokens; the playbook recommends "
            f"≤ {MAX_NAME_TOKENS}. Move detail into the description body.",
        ))

    # NAM-005: ≤ MAX_NAME_CHARS characters
    if len(name) > MAX_NAME_CHARS:
        result.findings.append(Finding(
            WARNING, "NAM-005",
            f"Skill name '{name}' is {len(name)} characters; the playbook recommends "
            f"≤ {MAX_NAME_CHARS}. Shorten the name and put detail in the description.",
        ))


def check_structure(skill_path: Path, result: SkillResult) -> None:
    """STR-* — required files and size limits."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        result.findings.append(Finding(ERROR, "STR-001", "SKILL.md is missing."))
        return  # remaining structural checks depend on SKILL.md

    line_count = len(skill_md.read_text(errors="replace").splitlines())
    if line_count > 500:
        result.findings.append(Finding(
            WARNING, "STR-002",
            f"SKILL.md is {line_count} lines (target: ≤500). "
            "Move domain detail into references/ and link to it from SKILL.md.",
        ))

    # STR-003: nudge — `evals/` directory present. No filename or format
    # restriction; the actual eval runner (skills-eval workflow) owns shape.
    evals_dir = skill_path / "evals"
    if not evals_dir.is_dir():
        result.findings.append(Finding(
            WARNING, "STR-003",
            "evals/ directory not found. Ship at least one eval scenario "
            "(any filename / format) so the skill can be regression-tested.",
        ))
    elif not any(evals_dir.iterdir()):
        result.findings.append(Finding(
            WARNING, "STR-003",
            "evals/ directory exists but is empty.",
        ))


def check_frontmatter(skill_path: Path, result: SkillResult) -> None:
    """FM-* — required frontmatter fields, formats, and data classification."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return

    content = skill_md.read_text(errors="replace")
    fm = parse_frontmatter(content)

    if fm is None:
        result.findings.append(Finding(
            ERROR, "FM-001",
            "SKILL.md has no YAML frontmatter. The file must start with --- and "
            "include name and description fields.",
        ))
        return

    # Required fields
    for fname in REQUIRED_FM_FIELDS:
        if not fm.get(fname):
            result.findings.append(Finding(
                ERROR, "FM-002",
                f"Frontmatter is missing required field: '{fname}'.",
            ))

    # name must match folder name
    fm_name = fm.get("name", "")
    if fm_name and fm_name != skill_path.name:
        result.findings.append(Finding(
            ERROR, "FM-003",
            f"Frontmatter 'name' is '{fm_name}' but folder is '{skill_path.name}'. "
            "These must match exactly.",
        ))

    # version must be semver
    version = fm.get("version", "")
    if version and not SEMVER_RE.match(version):
        result.findings.append(Finding(
            WARNING, "FM-004",
            f"Frontmatter 'version' ('{version}') is not valid semver. "
            "Use MAJOR.MINOR.PATCH format, e.g. 1.0.0.",
        ))

    # reviewed must be YYYY-MM-DD
    reviewed = fm.get("reviewed", "")
    if reviewed and not DATE_RE.match(reviewed):
        result.findings.append(Finding(
            WARNING, "FM-005",
            f"Frontmatter 'reviewed' ('{reviewed}') is not a valid date. "
            "Use YYYY-MM-DD format.",
        ))

    # data_classification
    dc = fm.get("data_classification", "").lower()
    if dc and dc not in VALID_DATA_CLASSIFICATIONS:
        result.findings.append(Finding(
            WARNING, "FM-006",
            f"Frontmatter 'data_classification' ('{dc}') is not a recognised value. "
            f"Use one of: {', '.join(sorted(VALID_DATA_CLASSIFICATIONS))}.",
        ))
    if dc in ("confidential", "restricted") and not fm.get("security_reviewed"):
        result.findings.append(Finding(
            ERROR, "FM-007",
            f"data_classification is '{dc}' but 'security_reviewed' field is absent. "
            "A security review sign-off is required before publishing skills that handle "
            "confidential or restricted data.",
        ))

    # description quality: minimum length and trigger-phrase signal
    desc = fm.get("description", "")
    if desc and len(desc) < 40:
        result.findings.append(Finding(
            WARNING, "FM-008",
            f"Frontmatter 'description' is very short ({len(desc)} chars). "
            "Include specific trigger phrases (e.g. 'Trigger when the user says...') "
            "so the skill activates reliably.",
        ))
    trigger_words = {"trigger", "use when", "use this", "invoke"}
    if desc and not any(w in desc.lower() for w in trigger_words):
        result.findings.append(Finding(
            WARNING, "FM-009",
            "Frontmatter 'description' has no trigger guidance. "
            "Add a phrase like 'Use this skill when...' or 'Trigger when...' "
            "so Claude knows when to invoke it.",
        ))

    # FM-010: the "Use when…" clause should list multiple user-phrasing examples.
    # Counts comma-separated items and quoted phrases (whichever is higher) in
    # the substring after the trigger keyword. Mirrors the playbook's "≥ 3
    # user-phrasing examples" checklist item.
    if desc:
        desc_lower = desc.lower()
        clause_start = -1
        for kw in TRIGGER_CLAUSE_KEYWORDS:
            idx = desc_lower.find(kw)
            if idx >= 0:
                clause_start = idx + len(kw)
                break
        if clause_start >= 0:
            clause = desc[clause_start:]
            # Comma-separated items in the clause (one item ≈ one comma + 1).
            commas = clause.count(",")
            # Quoted phrases (single or double quotes), at least 3 chars long.
            quoted = len(re.findall(r"['\"][^'\"]{3,}['\"]", clause))
            scenarios = max(commas + 1, quoted) if (commas or quoted) else 1
            if scenarios < 3:
                result.findings.append(Finding(
                    WARNING, "FM-010",
                    f"Description's trigger clause lists only {scenarios} scenario(s). "
                    "List ≥ 3 user-phrasing examples so the skill activates reliably "
                    "(e.g. 'Use when the user says \"X\", \"Y\", or \"Z\"').",
                ))

    # FM-011: description should not lead with implementation detail.
    # Mirrors the playbook's "Outcome over implementation" rule (Rule 2).
    if desc:
        for pat in IMPLEMENTATION_LEAD_PATTERNS:
            if re.search(pat, desc, flags=re.IGNORECASE):
                result.findings.append(Finding(
                    WARNING, "FM-011",
                    "Description leads with implementation detail "
                    "('Uses the X API…', 'Calls the Y endpoint…', 'Wraps the Z library…'). "
                    "Lead with the user outcome (what the skill produces or solves) "
                    "and put implementation notes in the body.",
                ))
                break


def check_security(skill_path: Path, result: SkillResult) -> None:
    """
    SEC-* — scan all text files for:
      SEC-001  Embedded credentials / secrets      (ERROR)
      SEC-002  PII — email addresses, routable IPs  (WARNING)
      SEC-003  Unicode smuggling / Trojan Source    (ERROR)
    """
    for path in sorted(skill_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        rel = path.relative_to(skill_path)

        # SEC-001: credentials
        for lineno, snippet, cred_type in scan_file_for_credentials(path):
            result.findings.append(Finding(
                ERROR, "SEC-001",
                f"Possible {cred_type} detected. "
                f"Remove it and replace with an env-var reference or vault lookup. "
                f"Snippet: «{snippet}»",
                file=str(rel),
                line=lineno,
            ))

        # SEC-002: PII
        for lineno, snippet, pii_type in scan_file_for_pii(path):
            result.findings.append(Finding(
                WARNING, "SEC-002",
                f"Possible {pii_type} found in skill file. "
                f"Do not embed real PII in skills — use anonymised examples or env-var "
                f"references instead (NV-BASE §2.2). Value: «{snippet}»",
                file=str(rel),
                line=lineno,
            ))

        # SEC-003: Unicode smuggling (Trojan Source / CVE-2021-42574)
        for lineno, char_name, category in scan_file_for_unicode_smuggling(path):
            result.findings.append(Finding(
                ERROR, "SEC-003",
                f"Unicode smuggling character detected ({category}): {char_name}. "
                f"These characters can disguise malicious content from human reviewers "
                f"(CVE-2021-42574 / Trojan Source). Remove or escape them.",
                file=str(rel),
                line=lineno,
            ))


# ── Skill discovery ───────────────────────────────────────────────────────────

def is_skill_dir(path: Path) -> bool:
    """
    A directory is treated as a skill candidate if it contains SKILL.md.
    Hidden directories and __pycache__ etc. are skipped.
    """
    if not path.is_dir():
        return False
    if path.name.startswith(".") or path.name.startswith("_"):
        return False
    return (path / "SKILL.md").exists()


def discover_skills(root: Path) -> List[Path]:
    return sorted(p for p in root.iterdir() if is_skill_dir(p))


# ── Checker orchestrator ──────────────────────────────────────────────────────

CHECKS = [check_naming, check_structure, check_frontmatter, check_security]


def check_skill(skill_path: Path) -> SkillResult:
    result = SkillResult(skill_name=skill_path.name, skill_path=skill_path)
    for check_fn in CHECKS:
        check_fn(skill_path, result)
    return result


def check_collisions(results: List[SkillResult]) -> None:
    """
    NAM-007 — global: detect duplicate skill ``name:`` values across the scan.

    Skills are aggregated into a flat namespace at runtime, so two folders
    with the same frontmatter name (regardless of folder layout) will collide.
    Falls back to the folder name when the SKILL.md or its frontmatter is
    missing.
    """
    name_to_results: dict = {}
    for r in results:
        skill_md = r.skill_path / "SKILL.md"
        n = r.skill_name
        if skill_md.exists():
            try:
                fm = parse_frontmatter(skill_md.read_text(errors="replace")) or {}
                if fm.get("name"):
                    n = fm["name"]
            except OSError:
                pass
        name_to_results.setdefault(n, []).append(r)

    for n, rs in name_to_results.items():
        if len(rs) > 1:
            paths = ", ".join(str(r.skill_path) for r in rs)
            for r in rs:
                r.findings.append(Finding(
                    ERROR, "NAM-007",
                    f"Skill name '{n}' is duplicated across {len(rs)} folders: {paths}. "
                    "Names must be unique within an aggregated marketplace; "
                    "differentiate by team prefix or scope (e.g. 'top5-amsdk-weekly' "
                    "vs 'top5-mind-hub-weekly').",
                ))


# ── Output formatting ─────────────────────────────────────────────────────────

ANSI = {
    ERROR:   "\033[91m",  # red
    WARNING: "\033[93m",  # yellow
    PASS:    "\033[92m",  # green
    INFO:    "\033[96m",  # cyan
    "RESET": "\033[0m",
    "BOLD":  "\033[1m",
    "DIM":   "\033[2m",
}
ICONS = {ERROR: "✗", WARNING: "⚠", PASS: "✓", INFO: "·"}


def c(text: str, key: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{ANSI.get(key, '')}{text}{ANSI['RESET']}"


def print_report(results: List[SkillResult], use_color: bool, strict: bool) -> int:
    total_errors = total_warnings = 0

    for result in results:
        nerrs   = len(result.errors)
        nwarns  = len(result.warnings)
        total_errors   += nerrs
        total_warnings += nwarns

        if nerrs:
            status = c("FAIL", ERROR, use_color)
        elif nwarns:
            status = c("WARN", WARNING, use_color)
        else:
            status = c("PASS", PASS, use_color)

        print(f"\n  {c('Skill:', 'BOLD', use_color)} {result.skill_name}  [{status}]")

        if not result.findings:
            print(f"    {c(ICONS[PASS], PASS, use_color)} No issues found")
        else:
            for f in result.findings:
                loc = f"  ({f.file}:{f.line})" if f.file and f.line else \
                      f"  ({f.file})"          if f.file            else ""
                icon = ICONS.get(f.severity, "·")
                tag  = f"[{f.rule}]"
                line = f"    {icon} {tag} {f.message}{loc}"
                print(c(line, f.severity, use_color))

    divider = "═" * 64
    print(f"\n{divider}")
    print(f"  Skills checked : {len(results)}")

    effective_errors = total_errors + (total_warnings if strict else 0)
    err_color  = ERROR   if total_errors   else PASS
    warn_color = WARNING if total_warnings else PASS

    print(f"  Errors         : {c(str(total_errors),   err_color,  use_color)}")
    print(f"  Warnings       : {c(str(total_warnings), warn_color, use_color)}")
    if strict and total_warnings:
        print(f"  {c('--strict mode: warnings treated as errors', WARNING, use_color)}")
    print(divider)

    if effective_errors:
        print(c("\n  ✗  Pipeline FAILED — fix errors before merging.\n", ERROR, use_color))
        return 1
    print(c("\n  ✓  All checks passed.\n", PASS, use_color))
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skills-dir", default=".", metavar="PATH",
        help="Root directory that contains skill folders (default: current directory)",
    )
    parser.add_argument(
        "--skill", metavar="NAME",
        help="Check only the named skill folder instead of the whole directory",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors (exit code 1 if any warnings exist)",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable ANSI color (auto-disabled when stdout is not a TTY)",
    )
    args = parser.parse_args()

    use_color = not args.no_color and sys.stdout.isatty()
    skills_root = Path(args.skills_dir).resolve()

    if not skills_root.exists():
        print(f"ERROR: skills directory not found: {skills_root}", file=sys.stderr)
        sys.exit(1)

    if args.skill:
        candidates = [skills_root / args.skill]
        for p in candidates:
            if not p.is_dir():
                print(f"ERROR: skill folder not found: {p}", file=sys.stderr)
                sys.exit(1)
    else:
        candidates = discover_skills(skills_root)

    print(f"\n{c('Agent Skills Playbook — Compliance Checker', 'BOLD', use_color)}")
    print(f"Scanning : {skills_root}")
    if args.strict:
        print(f"Mode     : {c('strict (warnings = errors)', WARNING, use_color)}")
    print("─" * 64)

    if not candidates:
        print("\nNo skill directories found (a skill directory must contain SKILL.md).")
        print("Nothing to check — exiting with code 0.\n")
        sys.exit(0)

    results = [check_skill(p) for p in candidates]
    # Cross-skill rules run once over the full result set.
    check_collisions(results)
    exit_code = print_report(results, use_color=use_color, strict=args.strict)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
