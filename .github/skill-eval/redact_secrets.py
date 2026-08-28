# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redact secrets from everything the eval harness posts to GitHub.

Two things leave the runner for a public surface: the results tree that the
workflow tars into the run artifact, and the Markdown/JSON that leg_report.py
renders into the PR comment and leg summary. A judge that proves "the agent
printed NGC_CLI_API_KEY" by quoting the key publishes a live credential
through both (PR #1647 run 32535909071 — full 70-char key in the public
artifact and, for six days, in the PR comment). The tarball already excludes
`agent/` trajectories for exactly this reason (PR #516); this module covers
the verifier files and rendered reports that stayed in.

The scanner is TruffleHog — the same tool the repo's pre-commit hook trusts —
run with `--no-verification`: verification would send candidate secrets to
their providers, which is itself exfiltration, and a candidate is worth
masking whether or not it still works. The tree pass is scan -> replace ->
rescan: findings whose raw value is a literal substring are replaced in
place; files still flagged on the rescan (encoded, composite, or binary
representations the literal pass cannot reach) are quarantined -- their
content replaced by a stub -- and a final scan must come back empty.

At a public-output boundary the scanner failing means the publish fails:
`redact_tree` raises on a missing scanner, timeout, or scan error, and its
callers (leg_report before it reads anything, the workflow pack step before
`tar`) let that propagate. `skills_eval_agent.py` already fail-closes a leg
whose report crashes, so a scanner outage reads as BLOCKED, not as green.
Builtin layers (the `nvapi-` NGC shape; exact values of secret-named env
vars) run unconditionally on top -- TruffleHog's NGC detector exists, but a
shape guard costs nothing and catches truncated quotes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = "[REDACTED]"
#: Keep the prefix so a rationale still says what kind of secret it saw.
NVAPI = re.compile(r"nvapi-[A-Za-z0-9_-]{16,}")
#: Component match, not substring: NGC_CLI_API_KEY yes, KEYCLOAK_URL no.
_SECRET_NAME = re.compile(
    r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|CREDENTIALS)(_|$)", re.IGNORECASE)
#: Values shorter than this are config flags ("true", "1", a region name),
#: not credentials; masking them would shred the evidence text. This is a
#: high-entropy-token heuristic, not a guarantee for short passwords.
_MIN_SECRET_LEN = 8

#: Match the pre-commit hook's pinned rev — `latest` would make the publish
#: gate drift under us.
_TRUFFLEHOG_IMAGE = "trufflesecurity/trufflehog:3.94.2"
_TRUFFLEHOG_TIMEOUT_S = 300


class ScannerUnavailable(RuntimeError):
    """TruffleHog could not run to completion; the publish must not proceed."""


def _env_secret_values() -> list[str]:
    values = [v for k, v in os.environ.items()
              if _SECRET_NAME.search(k) and v and len(v) >= _MIN_SECRET_LEN]
    # Longest first, so a secret containing another leaves no usable tail.
    return sorted(set(values), key=len, reverse=True)


def _trufflehog_cmd(target: Path) -> list[str]:
    if shutil.which("trufflehog"):
        return ["trufflehog", "filesystem", "--json", "--no-update",
                "--no-verification", str(target)]
    if shutil.which("docker"):
        return ["docker", "run", "--rm", "-v", f"{target}:/scan:ro",
                _TRUFFLEHOG_IMAGE, "filesystem", "--json",
                "--no-update", "--no-verification", "/scan"]
    raise ScannerUnavailable("trufflehog: no binary on PATH and no docker")


def _scan(target: Path) -> list[dict]:
    """One TruffleHog pass; findings as dicts. Raises rather than degrades."""
    cmd = _trufflehog_cmd(target)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TRUFFLEHOG_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise ScannerUnavailable(
            f"trufflehog: timed out after {_TRUFFLEHOG_TIMEOUT_S}s") from exc
    # 183 is "findings present" under --fail; without it success is 0. Any
    # other exit is a scan error, and an unscanned tree must not publish.
    if proc.returncode not in (0, 183):
        raise ScannerUnavailable(
            f"trufflehog: exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    findings = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (obj.get("Raw") or obj.get("RawV2")):
            findings.append(obj)
    return findings


def _finding_file(finding: dict, target: Path) -> Path | None:
    """The file a finding points at, mapped back from the docker mount."""
    meta = finding.get("SourceMetadata") or {}
    data = meta.get("Data") or {}
    fs = data.get("Filesystem") or {}
    raw_path = fs.get("file")
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.parts[:2] == ("/", "scan"):  # docker mount prefix
        path = target / Path(*path.parts[2:])
    return path


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    """Mask known secret values and secret-shaped tokens in one string."""
    for value in (extra_secrets or []):
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN and value in text:
            text = text.replace(value, MARKER)
    for value in _env_secret_values():
        if value in text:
            text = text.replace(value, MARKER)
    return NVAPI.sub(f"nvapi-{MARKER}", text)


def redact_obj(obj, extra_secrets: list[str] | None = None):
    """`redact_text` over every string in a JSON-shaped structure.

    Runs BEFORE serialization on purpose: `json.dumps` escapes quotes,
    backslashes and non-ASCII, after which an env value no longer occurs
    literally in the document and substring replacement misses it.
    """
    if isinstance(obj, str):
        return redact_text(obj, extra_secrets)
    if isinstance(obj, list):
        return [redact_obj(x, extra_secrets) for x in obj]
    if isinstance(obj, dict):
        return {redact_obj(k, extra_secrets): redact_obj(v, extra_secrets)
                for k, v in obj.items()}
    return obj


def _replace_literals(root: Path, secrets: list[str]) -> list[str]:
    changed = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        cleaned = redact_text(original, extra_secrets=secrets)
        if cleaned != original:
            path.write_text(cleaned, encoding="utf-8")
            changed.append(f"redacted: {path.relative_to(root)}")
    return changed


def _quarantine(path: Path, root: Path, detectors: list[str]) -> str:
    """Replace a file the literal pass could not clean with a stub.

    Encoded (base64/UTF-16), composite (RawV2 joins fields that sit on
    different lines), and binary representations all defeat substring
    replacement; the only safe artifact is no artifact.
    """
    names = ", ".join(sorted(set(detectors))) or "unknown"
    path.write_text(
        f"[REDACTED: quarantined by redact_secrets.py -- trufflehog "
        f"detector(s) {names} still matched after literal redaction]\n",
        encoding="utf-8")
    return f"quarantined: {path.relative_to(root)} ({names})"


def redact_tree(root: Path) -> list[str]:
    """Scan -> replace -> rescan -> quarantine -> verify; summary lines back.

    Raises ScannerUnavailable if TruffleHog cannot run, and RuntimeError if
    findings survive quarantine — in both cases the caller must not publish
    the tree. Summary lines name files and detectors, never secret values.
    """
    root = Path(root)
    findings = _scan(root)
    secrets = sorted(
        {s for f in findings for s in (f.get("Raw"), f.get("RawV2"))
         if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN},
        key=len, reverse=True)
    summary = [f"trufflehog: {len(findings)} finding(s), "
               f"{len(secrets)} distinct value(s)"]
    summary += _replace_literals(root, secrets)

    if findings:
        survivors: dict[Path, list[str]] = {}
        for f in _scan(root):
            path = _finding_file(f, root)
            detector = str(f.get("DetectorName") or "unknown")
            if path is not None and path.is_file():
                survivors.setdefault(path, []).append(detector)
        for path, detectors in sorted(survivors.items()):
            summary.append(_quarantine(path, root, detectors))
        if survivors and (final := _scan(root)):
            raise RuntimeError(
                f"redact_secrets: {len(final)} finding(s) survived quarantine; "
                f"refusing to publish {root}")
    else:
        # No scanner findings — still run the builtin layers over the tree.
        summary += _replace_literals(root, [])

    if len(summary) == 1:
        summary.append("clean: no redactions needed")
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", type=Path, required=True,
                    help="Directory to redact in place before it is packed/posted")
    args = ap.parse_args(argv)
    if not args.tree.is_dir():
        print(f"redact_secrets: {args.tree} is not a directory", file=sys.stderr)
        return 1
    try:
        for line in redact_tree(args.tree):
            print(f"redact_secrets: {line}")
    except (ScannerUnavailable, RuntimeError) as exc:
        print(f"redact_secrets: FATAL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
