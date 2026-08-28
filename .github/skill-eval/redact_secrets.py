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
run with `--no-verification` (verification would send candidate secrets to
their providers, which is itself exfiltration) and `--fail-on-scan-errors`
(an ordinary scan error otherwise exits 0, and an unscanned file must not
read as a clean one). The pinned-image docker path is preferred over
whatever `trufflehog` happens to be on PATH, so the publish gate does not
drift with the host.

The tree pass is scan -> replace -> verify -> quarantine -> verify:

* literal `Raw`/`RawV2` values are replaced wherever they appear;
* a finding whose values never matched literally anywhere is quarantined at
  the source file — TruffleHog's `Raw` is often only the identifier half of
  a multipart credential (AWS id vs `id:secret` in `RawV2`), and replacing
  the identifier blinds the rescan while the secret half survives;
* files still flagged on the rescan (encoded, composite, or binary
  representations) are quarantined — content replaced by a stub;
* a rescan finding that cannot be mapped to a file inside the tree is fatal,
  and the final scan must come back empty, or the publish refuses.

At a public-output boundary the scanner failing means the publish fails:
`redact_tree` raises on a missing scanner, timeout, or scan error, and its
callers (leg_report before it reads anything, the workflow pack step before
`tar`) let that propagate. `skills_eval_agent.py` already fail-closes a leg
whose report crashes, so a scanner outage reads as BLOCKED, not as green.
Builtin layers (the `nvapi-` NGC shape — as bytes too, so a binary carrying
an ASCII token is quarantined even when the scanner misses it; exact values
of secret-named env vars) run unconditionally on top.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "[REDACTED]"
#: Keep the prefix so a rationale still says what kind of secret it saw.
NVAPI = re.compile(r"nvapi-[A-Za-z0-9_-]{16,}")
_NVAPI_BYTES = re.compile(rb"nvapi-[A-Za-z0-9_-]{16,}")
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
_TRUFFLEHOG_ARGS = ["filesystem", "--json", "--no-update", "--no-verification",
                    "--fail-on-scan-errors"]
_TRUFFLEHOG_TIMEOUT_S = 300


class ScannerUnavailable(RuntimeError):
    """TruffleHog could not run to completion; the publish must not proceed."""


def _env_secret_values() -> list[str]:
    values = [v for k, v in os.environ.items()
              if _SECRET_NAME.search(k) and v and len(v) >= _MIN_SECRET_LEN
              # A value that is a substring of the marker (e.g. TOKEN=REDACTED)
              # would grow "[REDACTED]" on every pass; masking it is a no-op
              # security-wise and breaks idempotency.
              and v not in MARKER]
    # Longest first, so a secret containing another leaves no usable tail.
    return sorted(set(values), key=len, reverse=True)


def _run_scanner(target: Path) -> subprocess.CompletedProcess:
    """One scanner invocation; docker-pinned preferred over PATH drift."""
    if shutil.which("docker"):
        with tempfile.NamedTemporaryFile(suffix=".cid") as cid:
            cmd = ["docker", "run", "--rm", "--network", "none",
                   f"--cidfile={cid.name}.live",
                   "-v", f"{target}:/scan:ro", _TRUFFLEHOG_IMAGE,
                   *_TRUFFLEHOG_ARGS, "/scan"]
            try:
                return subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=_TRUFFLEHOG_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Killing the docker CLI does not kill the container.
                try:
                    container = Path(f"{cid.name}.live").read_text().strip()
                    if container:
                        subprocess.run(["docker", "rm", "-f", container],
                                       capture_output=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                raise
            finally:
                Path(f"{cid.name}.live").unlink(missing_ok=True)
    if shutil.which("trufflehog"):
        version = subprocess.run(["trufflehog", "--version"], capture_output=True,
                                 text=True, timeout=30)
        print(f"redact_secrets: docker unavailable, using PATH scanner "
              f"({(version.stdout or version.stderr).strip()})", file=sys.stderr)
        return subprocess.run(["trufflehog", *_TRUFFLEHOG_ARGS, str(target)],
                              capture_output=True, text=True,
                              timeout=_TRUFFLEHOG_TIMEOUT_S)
    raise ScannerUnavailable("trufflehog: no docker and no binary on PATH")


def _scan(target: Path) -> list[dict]:
    """One TruffleHog pass; findings as dicts. Raises rather than degrades."""
    try:
        proc = _run_scanner(target)
    except subprocess.TimeoutExpired as exc:
        raise ScannerUnavailable(
            f"trufflehog: timed out after {_TRUFFLEHOG_TIMEOUT_S}s") from exc
    # 183 is "findings present" under --fail; success without it is 0. Any
    # other exit — including --fail-on-scan-errors firing — means part of
    # the tree went unscanned, and an unscanned tree must not publish.
    if proc.returncode not in (0, 183):
        raise ScannerUnavailable(
            f"trufflehog: exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    findings = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # --json output is NDJSON findings only; anything else on stdout
            # means we are not parsing what we think we are.
            raise ScannerUnavailable(
                f"trufflehog: unparseable stdout line: {line[:120]!r}")
        if isinstance(obj, dict) and (obj.get("Raw") or obj.get("RawV2")):
            findings.append(obj)
    return findings


def _finding_file(finding: dict, target: Path) -> Path:
    """The file a finding points at, proven to live inside the tree.

    A finding that cannot be pinned to a file under the target cannot be
    redacted or quarantined, so it is fatal — returning None here would
    let the publish proceed with a known live finding.
    """
    meta = finding.get("SourceMetadata") or {}
    fs = (meta.get("Data") or {}).get("Filesystem") or {}
    raw_path = fs.get("file")
    if not raw_path:
        raise RuntimeError(
            f"redact_secrets: finding without a file path "
            f"(detector {finding.get('DetectorName')!r}); refusing to publish")
    path = Path(raw_path)
    if path.parts[:2] == ("/", "scan"):  # docker mount prefix
        path = target / Path(*path.parts[2:])
    resolved = path.resolve()
    if not resolved.is_relative_to(target.resolve()):
        raise RuntimeError(
            f"redact_secrets: finding path {raw_path!r} escapes the tree; "
            f"refusing to publish")
    if not resolved.is_file():
        raise RuntimeError(
            f"redact_secrets: finding path {raw_path!r} does not exist; "
            f"refusing to publish")
    return resolved


def _neutralize_symlinks(root: Path) -> list[str]:
    """Replace every symlink in the tree with a stub, links first.

    The rewriter and the quarantine both write through paths; a symlink would
    make this security gate mutate a file elsewhere on the runner, a
    directory symlink would let rglob edit files outside the tree, and the
    link target string itself is archive metadata that can carry a secret no
    content scan sees. Depth-first so a link inside a linked directory is
    never traversed.
    """
    lines = []
    links = sorted((p for p in root.rglob("*") if p.is_symlink()),
                   key=lambda p: len(p.parts), reverse=True)
    for link in links:
        link.unlink()
        link.write_text(
            "[REDACTED: symlink removed by redact_secrets.py -- targets are "
            "not part of the published tree]\n", encoding="utf-8")
        lines.append(f"symlink removed: {link.relative_to(root)}")
    return lines


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


def _binary_has_builtin_hit(data: bytes, secrets: list[str]) -> bool:
    if _NVAPI_BYTES.search(data):
        return True
    return any(s.encode("utf-8", "ignore") in data
               for s in [*secrets, *_env_secret_values()])


def _replace_literals(root: Path, secrets: list[str]) -> tuple[list[str], set[str]]:
    """Rewrite text files in place; quarantine binaries with builtin hits.

    Returns (summary lines, the secret values that were actually found and
    replaced somewhere). A value never seen literally is the multipart /
    encoded case the caller must quarantine at the finding's source file.
    """
    lines: list[str] = []
    replaced: set[str] = set()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.is_symlink():  # pre-pass removes these; never write through one
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # The builtin layers still apply to binaries: an ASCII nvapi-
            # token inside a non-UTF-8 file leaks exactly the same, and a
            # byte-level rewrite would corrupt the file, so quarantine it.
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if _binary_has_builtin_hit(data, secrets):
                lines.append(_quarantine(path, root, ["builtin-binary"]))
            continue
        cleaned = original
        for value in secrets:
            if value in cleaned:
                cleaned = cleaned.replace(value, MARKER)
                replaced.add(value)
        cleaned = redact_text(cleaned)
        if cleaned != original:
            path.write_text(cleaned, encoding="utf-8")
            lines.append(f"redacted: {path.relative_to(root)}")
    return lines, replaced


def _quarantine(path: Path, root: Path, detectors: list[str]) -> str:
    """Replace a file the literal pass could not clean with a stub.

    Encoded (base64/UTF-16), composite (RawV2 joins fields that sit on
    different lines), and binary representations all defeat substring
    replacement; the only safe artifact is no artifact. `.json` files get a
    JSON stub so downstream readers (collect_leg, row_state) parse a
    document that says what happened instead of crashing on prose.
    """
    names = ", ".join(sorted(set(detectors))) or "unknown"
    message = (f"quarantined by redact_secrets.py -- trufflehog detector(s) "
               f"{names} still matched after literal redaction")
    if path.suffix == ".json":
        path.write_text(json.dumps({"redacted": message}) + "\n", encoding="utf-8")
    else:
        path.write_text(f"[REDACTED: {message}]\n", encoding="utf-8")
    return f"quarantined: {path.relative_to(root)} ({names})"


def redact_tree(root: Path) -> list[str]:
    """Scan -> replace -> verify -> quarantine -> verify; summary lines back.

    Raises ScannerUnavailable if TruffleHog cannot run, and RuntimeError if
    a finding cannot be pinned to a file in the tree or survives quarantine —
    in every such case the caller must not publish. Summary lines name files
    and detectors, never secret values.
    """
    root = Path(root)
    summary = _neutralize_symlinks(root)
    findings = _scan(root)
    # Every finding is pinned to a file inside the tree BEFORE any rewrite:
    # replacement can blind the rescan, after which a finding with missing
    # or escaping metadata would never be checked at all.
    located = [(f, _finding_file(f, root)) for f in findings]
    secrets = sorted(
        {s for f in findings for s in (f.get("Raw"), f.get("RawV2"))
         if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN},
        key=len, reverse=True)
    summary.insert(len(summary), f"trufflehog: {len(findings)} finding(s), "
                                 f"{len(secrets)} distinct value(s)")
    lines, replaced = _replace_literals(root, secrets)
    summary += lines
    if not findings:
        if len(summary) <= 1:
            summary.append("clean: no redactions needed")
        return summary

    # Multipart guard, judged per finding on its COMPLETE representation.
    # TruffleHog's Raw is often only the identifier half of a credential and
    # RawV2 the identifier+secret composite (AWS: `id` vs `id:secret`). When
    # the halves sit on different lines, replacing the identifier blinds the
    # rescan while the secret half survives — so replacing only Raw is not
    # enough: the finding counts as neutralized only if its RawV2 (when it
    # has one) was itself replaced somewhere.
    for f, path in located:
        raw, rawv2 = f.get("Raw"), f.get("RawV2")
        required = next(
            (v for v in (rawv2, raw)
             if isinstance(v, str) and len(v) >= _MIN_SECRET_LEN), None)
        if required is not None and required not in replaced:
            summary.append(_quarantine(
                path, root,
                [f"{f.get('DetectorName') or 'unknown'} (incomplete match)"]))

    # Rescan: representations the literal pass cannot reach.
    survivors: dict[Path, list[str]] = {}
    for f in _scan(root):
        path = _finding_file(f, root)  # unmappable or escaping paths raise
        survivors.setdefault(path, []).append(str(f.get("DetectorName") or "unknown"))
    for path, detectors in sorted(survivors.items()):
        summary.append(_quarantine(path, root, detectors))

    # Final verification is unconditional once findings existed: the publish
    # only proceeds over a tree the scanner can no longer flag.
    if final := _scan(root):
        raise RuntimeError(
            f"redact_secrets: {len(final)} finding(s) survived quarantine; "
            f"refusing to publish {root}")
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
