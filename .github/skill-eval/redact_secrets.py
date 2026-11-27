# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redact secrets from everything the eval harness posts to GitHub.

Two things leave the runner for a public surface: the results tree that the
workflow tars into the run artifact, and the Markdown/JSON that leg_report.py
renders into the PR comment and leg summary. A judge that proves "the agent
printed NGC_CLI_API_KEY" by quoting the key publishes a live credential
through both (PR #1647 run 32535909071 — full 70-char key in the public
artifact and, for six days, in the PR comment). Within per-trial `agent/`
directories, `archive_results_tree` allowlists only structured trajectories
(`trajectory.json` / `trajectory.jsonl`) after fail-closed redaction, while
excluding raw `sessions/`, `setup/`, and unparsed CLI logs (`codex.txt`,
`claude-code.txt`, `openclaw*`).

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
import tarfile
import tempfile
from pathlib import Path

#: Word boundary that also fires immediately after JSON-escaped `\n`/`\r`/`\t`
#: inside `trajectory.json` tool-output strings (`\nFOO_TOKEN=...` has ASCII `n`
#: before `F`, which defeats `\b`).
_BOUNDARY = r"(?:(?<![A-Za-z0-9_])|(?<=\\[nrt]))"
_BOUNDARY_BYTES = rb"(?:(?<![A-Za-z0-9_])|(?<=\\[nrt]))"

MARKER = "[REDACTED]"
#: Keep the prefix so a rationale still says what kind of secret it saw.
NVAPI = re.compile(r"nvapi-[A-Za-z0-9_-]{16,}")
_NVAPI_BYTES = re.compile(rb"nvapi-[A-Za-z0-9_-]{16,}")
_SK_TOKEN = re.compile(_BOUNDARY + r"sk-(?:proj-|ant-[A-Za-z0-9-]+-)?[A-Za-z0-9_-]{20,}")
_SK_TOKEN_BYTES = re.compile(_BOUNDARY_BYTES + rb"sk-(?:proj-|ant-[A-Za-z0-9-]+-)?[A-Za-z0-9_-]{20,}")
_HF_TOKEN = re.compile(_BOUNDARY + r"hf_[A-Za-z0-9]{20,}")
_HF_TOKEN_BYTES = re.compile(_BOUNDARY_BYTES + rb"hf_[A-Za-z0-9]{20,}")
_GH_TOKEN = re.compile(_BOUNDARY + r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_GH_TOKEN_BYTES = re.compile(_BOUNDARY_BYTES + rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
#: Ephemeral worker/gateway tokens that never exist in coordinator .env (e.g.
#: OpenClaw dashboard `#token=<hex>`, `Authorization: Bearer ...`, `openclaw.json`
#: token fields, or `.env` / `docker compose config` dumps in trajectory.json).
_URL_TOKEN = re.compile(r"([#?&]token=)(?!\[REDACTED\])[A-Za-z0-9._~+/-]{16,}")
_BEARER_HEADER = re.compile(
    r"(" + _BOUNDARY + r"Authorization\s*:\s*Bearer\s+)(?!\[REDACTED\])[A-Za-z0-9._~+/-]{16,}",
    re.IGNORECASE,
)
_JSON_SECRET_FIELD = re.compile(
    r'(\\?"(?:token|gateway_token|auth_token|api_key|apiKey|secret|password)\\?"'
    r'\s*:\s*\\?")(?!\[REDACTED\])[A-Za-z0-9._~+/-]{16,}(\\?")'
)
_ENV_SECRET_ASSIGN = re.compile(
    r"(" + _BOUNDARY + r"[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIAL)="
    r'(?:\\?[\x27"])?)(?!\[REDACTED\])(?!\$)[A-Za-z0-9._~+/-]{16,}'
)
#: Only structured trajectories may leave `<trial>/agent/` for the public artifact.
_AGENT_ARTIFACT_ALLOWLIST = frozenset({"trajectory.json", "trajectory.jsonl"})
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


def _secret_variants(value: str) -> list[str]:
    """Literal and JSON-escaped forms of `value`, longest first.

    `trajectory.json` serializes tool inputs/outputs as JSON strings (and often
    nests serialized JSON inside tool outputs), escaping quotes, backslashes,
    and control characters so the raw secret no longer occurs as a literal
    substring on disk.
    """
    esc1 = json.dumps(value)[1:-1]
    esc2 = json.dumps(esc1)[1:-1]
    return sorted({value, esc1, esc2}, key=len, reverse=True)


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    """Mask known secret values and secret-shaped tokens in one string."""
    for value in (extra_secrets or []):
        if (isinstance(value, str) and len(value) >= _MIN_SECRET_LEN
                and value not in MARKER):
            for variant in _secret_variants(value):
                if variant in text:
                    text = text.replace(variant, MARKER)
    for value in _env_secret_values():
        for variant in _secret_variants(value):
            if variant in text:
                text = text.replace(variant, MARKER)
    text = NVAPI.sub(f"nvapi-{MARKER}", text)
    text = _SK_TOKEN.sub(f"sk-{MARKER}", text)
    text = _HF_TOKEN.sub(f"hf_{MARKER}", text)
    text = _GH_TOKEN.sub(MARKER, text)
    text = _URL_TOKEN.sub(rf"\1{MARKER}", text)
    text = _BEARER_HEADER.sub(rf"\1{MARKER}", text)
    text = _JSON_SECRET_FIELD.sub(rf"\1{MARKER}\2", text)
    text = _ENV_SECRET_ASSIGN.sub(rf"\1{MARKER}", text)
    return text


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
    if any(rx.search(data) for rx in (
        _NVAPI_BYTES, _SK_TOKEN_BYTES, _HF_TOKEN_BYTES, _GH_TOKEN_BYTES
    )):
        return True
    return any(
        variant.encode("utf-8", "ignore") in data
        for s in [*secrets, *_env_secret_values()]
        for variant in _secret_variants(s)
    )


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
            matched = False
            for variant in _secret_variants(value):
                if variant in cleaned:
                    cleaned = cleaned.replace(variant, MARKER)
                    matched = True
            if matched:
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


def _should_archive(rel_path: Path) -> bool:
    """Allowlist only structured trajectories inside per-trial `agent/` dirs.

    Everything outside per-trial `agent/` (`result.json`, `config.json`,
    `trial.log`, `verifier/**`, `artifacts/**`, `trace-urls.tsv`, ...) stays in.
    Under `agent/` (outside `artifacts/`), only `trajectory.json` and
    `trajectory.jsonl` may enter the public artifact — raw `sessions/`
    (including `session-env` and `shell-snapshots`), `setup/`, `codex.txt`,
    `claude-code.txt`, `openclaw*`, and any future CLI scratch stay excluded.
    """
    for idx, part in enumerate(rel_path.parts):
        if part == "agent" and "artifacts" not in rel_path.parts[:idx]:
            tail = rel_path.parts[idx + 1:]
            return len(tail) == 1 and tail[0] in _AGENT_ARTIFACT_ALLOWLIST
    return True


def archive_results_tree(root: Path, tarball: Path) -> list[str]:
    """Redact `root` fail-closed, then pack allowlisted files into `tarball`.

    Unlinks `tarball` FIRST so a failed scan on a persistent self-hosted runner
    rerun can never leave a previous attempt's stale archive behind for
    `if: always()` upload. Skips creating `tarball` when `root` holds no
    `result.json` within depth 3.
    """
    root = Path(root)
    tarball = Path(tarball)
    tarball.unlink(missing_ok=True)
    summary = redact_tree(root)
    results = [
        p for p in sorted(root.rglob("result.json"))
        if p.is_file() and not p.is_symlink()
        and len(p.relative_to(root).parts) <= 3
    ]
    if not results:
        summary.append("archive: results dir exists but empty — nothing to archive")
        return summary
    tarball.parent.mkdir(parents=True, exist_ok=True)
    tmp_tar = tarball.parent / f".{tarball.name}.tmp.{os.getpid()}"
    tmp_tar.unlink(missing_ok=True)
    prefix = Path(root.name)
    traj_count = 0
    try:
        with tarfile.open(tmp_tar, "w:gz", dereference=False) as tf:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                rel = path.relative_to(root)
                if not _should_archive(rel):
                    continue
                if path.name in _AGENT_ARTIFACT_ALLOWLIST and "agent" in rel.parts:
                    traj_count += 1
                tf.add(path, arcname=str(prefix / rel), recursive=False)
        os.replace(tmp_tar, tarball)
    finally:
        tmp_tar.unlink(missing_ok=True)
    summary.append(
        f"archived {len(results)} result.json file(s) and "
        f"{traj_count} redacted agent trajectory file(s) -> {tarball}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", type=Path, required=True,
                    help="Directory to redact in place before it is packed/posted")
    ap.add_argument("--tarball", type=Path, default=None,
                    help="Optional .tar.gz path to write after fail-closed redaction")
    args = ap.parse_args(argv)
    if not args.tree.is_dir():
        if args.tarball is not None:
            args.tarball.unlink(missing_ok=True)
        print(f"redact_secrets: {args.tree} is not a directory", file=sys.stderr)
        return 1
    try:
        lines = (
            archive_results_tree(args.tree, args.tarball)
            if args.tarball is not None
            else redact_tree(args.tree)
        )
        for line in lines:
            print(f"redact_secrets: {line}")
    except (ScannerUnavailable, RuntimeError) as exc:
        print(f"redact_secrets: FATAL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
