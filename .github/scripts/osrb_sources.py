#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inventory the third-party dependencies that live in no language manifest.

``osrb_scan`` walks lockfiles and requirements files, so it sees the PyPI and
npm closure and nothing else. Everything a container drags in — the base image,
the apt/apk package list, a ``git clone`` of upstream source, a Helm subchart, a
pinned GitHub Action — is invisible to it. That blind spot is not theoretical:
the audit that motivated this module found AGPL-3.0 (grafana, minio),
Elastic-2.0 (elasticsearch, kibana, logstash), Confluent Community (cp-kafka)
and the GPL-2.0-or-later codec set (libx264, libx265, libxvidcore, liba52,
libdca) reaching OSRB through exactly these files, none of which any lockfile
mentions. This module reads them.

Six parsers, one per evidence shape, each returning ``osrb_scan.make_row``
rows:

    parse_dockerfile        FROM images, COPY --from external images, OS package
                            installs, pip installs, and fetched source/binaries
    parse_compose           service images that are pulled rather than built
    parse_helm_chart        the Chart.yaml dependencies list
    parse_cmake             find_package / FetchContent / ExternalProject /
                            pkg_check_modules
    parse_precommit         .pre-commit-config.yaml repos, pinned by rev
    parse_actions_workflow  uses: owner/repo@ref in .github/workflows

Licenses are deliberately NOT guessed. There is no offline mapping from
``libx264`` or ``confluentinc/cp-kafka`` to an SPDX id that is right often
enough to be trusted, and a row that claims MIT when the truth is GPL-2.0 is
worse than a row that admits it does not know: the first ends the review, the
second starts one. Every row here therefore carries an empty license and
``risk=Unknown`` (via ``license_risk("")``) unless the file itself states a
license. A hardcoded lookup table would go stale silently, which is the class
of failure this whole change exists to remove.

YAML is read with a narrow, indentation-aware line reader rather than PyYAML,
because CI runs these scripts on a bare ``setup-python`` with no ``pip
install``; importing PyYAML would make the compliance gate fail closed on
every PR. The reader only understands the handful of keys named above.

Diff integration: ``inventory_at_ref`` returns ``{key: row}`` keyed by
``(source_kind, language, package, new_version)``. Comparing the key sets of
two refs yields added/removed; grouping by ``key[:3]`` (everything but the
version) yields updated, which is the same shape ``osrb_scan.diff_language``
already uses for lockfile packages.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compose_image_golden import resolve_nested  # noqa: E402
from osrb_scan import license_risk, make_row, owning_module  # noqa: E402

KIND_CONTAINER = "container"
KIND_COMPOSE = "compose"
KIND_CHART = "chart"
KIND_BUILD = "build"
KIND_CI = "ci"

# `change` value owned by osrb_scan: a dependency-bearing file we could not
# read. Emitted (rather than swallowed) so a parser crash fails the job instead
# of quietly shrinking the inventory.
CHANGE_UNCOVERED = "UNCOVERED_SOURCE"


def _log(msg: str) -> None:
    print(f"[osrb-sources] {msg}", file=sys.stderr)


def _decode(data: bytes) -> str:
    # errors="replace" rather than a raise: a stray non-UTF-8 byte in a comment
    # must not take the compliance gate down for the whole repo.
    return data.decode("utf-8", errors="replace")


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _row(
    *,
    language: str,
    package: str,
    path: str,
    line: int,
    kind: str,
    version: str = "",
    license_expr: str = "",
    repository_url: str = "",
    notes: str = "",
) -> dict[str, str]:
    """Build one inventory row, with the line that is the evidence for it.

    ``source_file`` carries ``#L<n>`` so an OSRB reviewer lands on the exact
    ``FROM`` or ``apt-get install`` instead of being handed a 400-line
    Dockerfile and told a dependency is somewhere in it.
    """
    return make_row(
        language=language,
        package=package,
        new_version=version,
        new_license=license_expr,
        repository_url=repository_url,
        notes=notes,
        source_kind=kind,
        source_file=f"{path}#L{line}" if line else path,
        module=owning_module(path),
        risk=license_risk(license_expr),
    )


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the first row per (language, package, version) within one file.

    A multi-stage Dockerfile installs ``ca-certificates`` in four stages and
    pulls the same base image twice; those are one dependency each, and
    repeating them would inflate every OSRB diff with rows a reviewer has
    already cleared.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = (row["language"], row["package"], row["new_version"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


# ---------------------------------------------------------------------------
# Shell-ish tokenizer used by the Dockerfile parser
# ---------------------------------------------------------------------------

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_URL_RE = re.compile(r"^(?:https?|ftp|ftps|git|git\+https?|git\+ssh|ssh)://\S+$")
_PKG_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")
_BARE_VAR_RE = re.compile(r"\$(?![{(])([A-Za-z_][A-Za-z0-9_]*)")

# Command separators. Splitting on these is what keeps `rm -rf /var/lib/apt` in
# the same RUN from being read as part of the preceding `apt-get install` list.
_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", "\n"}


def _logical_lines(text: str) -> list[tuple[int, str, list[int]]]:
    """Join ``\\``-continued lines, keeping the source line of every character.

    Dockerfile package lists are almost always continuations — the vios base
    image installs 30 apt packages one per line. A parser that reads physical
    lines sees ``apt-get install -y --no-install-recommends`` followed by 30
    lines it cannot classify, and reports zero OS packages. Joining is what
    makes them visible; the per-character line map is what still lets each
    package cite its own line.

    Comment lines inside a continuation are dropped, matching the Docker
    parser, so a commented-out package is never inventoried.
    """
    joined: list[tuple[int, str, list[int]]] = []
    buf: list[str] = []
    line_map: list[int] = []
    start = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not buf:
            if not stripped or stripped.startswith("#"):
                continue
            start = lineno
        elif stripped.startswith("#"):
            continue
        body = raw.rstrip()
        continued = body.endswith("\\")
        if continued:
            body = body[:-1]
        buf.append(body)
        line_map.extend([lineno] * len(body))
        buf.append(" ")
        line_map.append(lineno)
        if not continued:
            joined.append((start, "".join(buf), line_map))
            buf, line_map = [], []
    if buf:
        joined.append((start, "".join(buf), line_map))
    return joined


def _tokens(text: str, line_map: list[int]) -> list[tuple[str, int]]:
    """Split a joined instruction into (token, line) pairs, honouring quotes.

    Quoting matters for correctness, not tidiness: ``services/video-summarization``
    embeds a whole ``uv pip install`` command inside a single-quoted printf
    argument that is never executed at build time. Treating the quoted string
    as one opaque token is what stops it being inventoried as a real install.
    """
    out: list[tuple[str, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start = i
        chars: list[str] = []
        quote: str | None = None
        while i < n:
            char = text[i]
            if quote is not None:
                if char == quote:
                    quote = None
                else:
                    chars.append(char)
                i += 1
                continue
            if char in "\"'":
                quote = char
                i += 1
                continue
            if char.isspace():
                break
            chars.append(char)
            i += 1
        token = "".join(chars)
        if token:
            out.append((token, line_map[start] if start < len(line_map) else 0))
    return out


def _segments(tokens: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    """Split a token stream into shell command segments."""
    out: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    for token, line in tokens:
        if token in _SEPARATORS:
            if current:
                out.append(current)
            current = []
            continue
        # `apt-get install -y libgdal-dev; fi` — the separator is glued to the
        # end of the previous word. Stripping it without ending the segment is
        # how `fi` ended up inventoried as a Debian package.
        terminates = token.endswith(";")
        token = token.rstrip(";")
        if token:
            current.append((token, line))
        if terminates:
            if current:
                out.append(current)
            current = []
    if current:
        out.append(current)
    return out


# Shell words that stand in front of the real command. `RUN if [ … ]; then
# apt-get install …` puts `then` first, and reading `then` as the command loses
# the whole conditional install — which is where the arm64-only packages live.
_SHELL_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "do", "done", "while", "for", "until",
    "exec", "eval", "time", "nohup", "sudo", "command", "env", "set",
}


def _command(segment: list[tuple[str, int]]) -> tuple[str, list[tuple[str, int]]]:
    """Return (command basename, remaining args) for one segment.

    Leading ``VAR=value`` assignments (``DEBIAN_FRONTEND=noninteractive``),
    leading flags (``RUN --mount=type=bind,...``) and shell keywords are
    stepped over, and the command is reduced to its basename so ``$PYBIN/pip``
    is still recognised as pip.
    """
    index = 0
    while index < len(segment):
        token = segment[index][0]
        if (
            token.startswith("-")
            or _ENV_ASSIGN_RE.match(token)
            or token in _SHELL_KEYWORDS
        ):
            index += 1
            continue
        break
    if index >= len(segment):
        return "", []
    name = segment[index][0].rsplit("/", 1)[-1]
    return name, segment[index + 1 :]


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------

# System package managers, mapped to the CSV `language` (ecosystem) they feed.
_OS_INSTALLERS = {
    "apt-get": "deb",
    "apt": "deb",
    "aptitude": "deb",
    "apk": "apk",
    "yum": "rpm",
    "dnf": "rpm",
    "microdnf": "rpm",
    "zypper": "rpm",
}
_OS_INSTALL_VERBS = {"install", "add"}
# Flags whose value is the NEXT token. Without this list `--retries 20` puts a
# package called "20" in front of OSRB and `--index-url https://pypi.org/simple`
# puts one called "https:".
_OS_VALUE_FLAGS = {"-t", "--target-release", "-o", "--option"}

_PIP_COMMANDS = {"pip", "pip3"}
_PIP_VALUE_FLAGS = {
    "-r",
    "--requirement",
    "-c",
    "--constraint",
    "-t",
    "--target",
    "-i",
    "--index-url",
    "--extra-index-url",
    "-f",
    "--find-links",
    "--python-version",
    "--python",
    "--platform",
    "--abi",
    "--implementation",
    "--prefix",
    "--root",
    "--src",
    "--cache-dir",
    "--timeout",
    "--default-timeout",
    "--retries",
    "--proxy",
    "--trusted-host",
    "--upgrade-strategy",
    "--progress-bar",
    "--report",
    "--log",
    "--config-settings",
    "--exclude-newer",
}
_FETCH_COMMANDS = {"wget", "curl"}
_FETCH_VALUE_FLAGS = {"-o", "-O", "--output", "--output-document"}

# Extensions that are documents or key material, not code. Fetching the NVIDIA
# licence PDF or an apt signing key is not a third-party dependency, and rows
# for them would train reviewers to skim past this section.
_NON_CODE_SUFFIXES = (
    ".pdf",
    ".asc",
    ".gpg",
    ".key",
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".json",
    ".crt",
    ".pem",
    ".list",
    ".repo",
)
# Extensionless signing keys: docker publishes its apt key at
# `…/linux/debian/gpg`, which no suffix check catches and which OSRB has no
# interest in reviewing as a component.
_KEY_FILENAMES = {"gpg", "key", "signing", "pubkey", "release.key"}


def _expand(value: str, env: dict[str, str]) -> str:
    """Resolve ``$VAR`` and ``${VAR:-default}`` against collected ARG defaults.

    Half the Dockerfiles here write ``FROM python:${PYTHON_VERSION}-trixie`` or
    ``FROM $DISTROLESS_IMG:$DISTROLESS_TAG``. Left unresolved those are useless
    to a reviewer; resolved, they are ``python:3.13-trixie`` and
    ``nvcr.io/nvidia/distroless/python:3.13-v4.0.6``.
    """
    return resolve_nested(_BARE_VAR_RE.sub(r"${\1}", value), env)


def _matches_stage(ref: str, aliases: set[str]) -> bool:
    """True when a FROM/COPY target names an earlier build stage, not an image.

    ``FROM ocv-${TARGETARCH}`` refers to the ``ocv-amd64`` / ``ocv-arm64``
    stages above it. TARGETARCH is supplied by buildx, so it never resolves
    here; treating the unresolved reference as a wildcard is what keeps a
    stage from being reported to OSRB as a phantom image.
    """
    if ref in aliases:
        return True
    if "${" not in ref:
        return False
    pattern = re.compile(
        "^" + "".join(".*" if part is None else re.escape(part) for part in _split_vars(ref)) + "$"
    )
    return any(pattern.match(alias) for alias in aliases)


def _split_vars(ref: str) -> list[str | None]:
    """Split ``a${B}c`` into ``["a", None, "c"]`` — None marks a substitution."""
    parts: list[str | None] = []
    index = 0
    for match in re.finditer(r"\$\{[^}]*\}", ref):
        parts.append(ref[index : match.start()])
        parts.append(None)
        index = match.end()
    parts.append(ref[index:])
    return parts


def _image_identity(ref: str) -> tuple[str, str]:
    """Split an image reference into (repository, tag-or-digest).

    Keeping the tag out of the package name is what makes a version bump read
    as ``updated`` in the diff instead of one ``removed`` plus one ``added``.
    """
    if "@" in ref:
        # `python:3.12-slim@sha256:…` — the tag belongs with the version, not
        # the name, or every digest-pinned image becomes its own package and no
        # bump ever reads as `updated`.
        head, _, digest = ref.partition("@")
        repo, tag = _image_identity(head)
        return repo, f"{tag}@{digest}" if tag else digest
    repo, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        # No tag at all, or the colon belonged to a registry port.
        return ref, ""
    return repo, tag


# Image names this repo builds and publishes itself. They are not third-party
# and must not reach OSRB, but they must be matched by *shape* rather than by a
# hardcoded list, so a new VSS service does not silently become a "dependency".
_VSS_ARTIFACT_RE = re.compile(
    r"(^|/)(vss|vss-core)/|(^|/)(vss[-_]|via-engine)", re.IGNORECASE
)


def _is_vss_artifact(repo: str) -> bool:
    return bool(_VSS_ARTIFACT_RE.search(repo))


def _os_packages(
    args: list[tuple[str, int]], language: str, path: str
) -> list[dict[str, str]]:
    verb_at = None
    for index, (token, _line) in enumerate(args):
        if token in _OS_INSTALL_VERBS:
            verb_at = index
            break
        if not token.startswith("-"):
            # A different subcommand (`update`, `upgrade`, `clean`).
            return []
    if verb_at is None:
        return []

    rows: list[dict[str, str]] = []
    skip_next = False
    for token, line in args[verb_at + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if token in _OS_VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        name, _, version = token.partition("=")
        # `$(cat requirements_apt.txt)`, `/tmp/foo.deb`, `*.deb`: the package
        # set is not knowable from this line. Reporting the shell fragment as a
        # package name would be a lie; the requirements_apt.txt itself is
        # picked up as its own dependency file.
        if not _PKG_TOKEN_RE.match(name):
            continue
        rows.append(
            _row(
                language=language,
                package=name,
                version=version,
                path=path,
                line=line,
                kind=KIND_CONTAINER,
                notes="OS package installed into the image",
            )
        )
    return rows


def _pip_packages(args: list[tuple[str, int]], path: str) -> list[dict[str, str]]:
    if not args or args[0][0] != "install":
        return []
    rows: list[dict[str, str]] = []
    skip_next = False
    for token, line in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in _PIP_VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        # `. `, `./release[eval]`, `/tmp/x.whl`, `"$wheel_path"`: local paths
        # and shell expansions, not named PyPI packages. A direct reference
        # (`name @ git+https://…`) is exempt — it contains a URL but names a
        # real package, and vss-ctx-rag enters the LVS image exactly that way.
        if " @ " not in token and (
            "/" in token or token.startswith(".") or "$" in token or "*" in token
        ):
            continue
        name, version, repository = _pip_requirement(token)
        if not name:
            continue
        rows.append(
            _row(
                language="python",
                package=name,
                version=version,
                repository_url=repository,
                path=path,
                line=line,
                kind=KIND_CONTAINER,
                notes="pip install in a Dockerfile — declared in no manifest",
            )
        )
    return rows


def _pip_requirement(spec: str) -> tuple[str, str, str]:
    """Return (name, pinned version, repository url) for one pip requirement.

    Only ``==`` pins become a version. ``wheel>=0.46.2`` floats, so claiming
    0.46.2 shipped would misreport what OSRB reviewed.
    """
    if " @ " in spec:
        name, _, url = spec.partition(" @ ")
        name = name.strip()
        url = url.strip()
        version = ""
        base = url.split("#", 1)[0]
        if ".git@" in base:
            version = base.rsplit("@", 1)[-1]
        return name, version, url
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    if not match:
        return "", "", ""
    name = match.group(1)
    rest = spec[len(name) :]
    version = ""
    if rest.startswith("=="):
        version = rest[2:].split(",")[0].strip()
    return name, version, ""


def _url_identity(url: str) -> tuple[str, str]:
    """Name a fetched artifact, preferring an upstream project over a filename.

    ``https://github.com/numpy/numpy/archive/refs/tags/v2.2.6.tar.gz`` is the
    numpy project at v2.2.6; reporting it as ``v2.2.6.tar.gz`` would leave the
    reviewer to work out which project that even is.
    """
    parts = urllib.parse.urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if parts.netloc.endswith("github.com") and len(segments) >= 2:
        project = f"{segments[0]}/{segments[1]}".removesuffix(".git")
        version = ""
        for marker in ("tags", "download"):
            if marker in segments:
                index = segments.index(marker)
                if index + 1 < len(segments):
                    version = segments[index + 1]
                    for suffix in (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip"):
                        version = version.removesuffix(suffix)
                    break
        return project, version
    if segments:
        return segments[-1], ""
    return parts.netloc, ""


def _fetched_sources(
    command: str, args: list[tuple[str, int]], path: str
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    skip_next = False
    for token, line in args:
        is_url = bool(_URL_RE.match(token))
        if skip_next:
            skip_next = False
            if not is_url:
                continue
        if token in _FETCH_VALUE_FLAGS:
            # `-o /tmp/out` consumes its value; `curl -fLO https://…` does not,
            # so a URL is never eaten as a flag value.
            skip_next = True
            continue
        if not is_url:
            continue
        lowered = token.split("?", 1)[0].lower()
        if lowered.endswith(_NON_CODE_SUFFIXES) or lowered.rsplit("/", 1)[-1] in _KEY_FILENAMES:
            continue
        package, version = _url_identity(token)
        rows.append(
            _row(
                language="source",
                package=package,
                version=version,
                repository_url=token,
                path=path,
                line=line,
                kind=KIND_CONTAINER,
                notes=f"fetched into the image with {command}",
            )
        )
    return rows


def _cloned_sources(args: list[tuple[str, int]], path: str) -> list[dict[str, str]]:
    """Rows for `git clone` and `git remote add` of an upstream repository.

    Both forms appear here: rt-vlm clones deepstream_python_apps outright,
    rt-embed does `git init` + `git remote add origin <url>` + `git fetch` to
    pin an exact commit. Matching only `clone` would miss the pinned one, which
    is the more interesting of the two for OSRB.
    """
    version = ""
    urls: list[tuple[str, int]] = []
    index = 0
    while index < len(args):
        token, line = args[index]
        if token in {"-b", "--branch"}:
            if index + 1 < len(args):
                version = args[index + 1][0]
            index += 2
            continue
        if token in {"-C", "--depth"}:
            index += 2
            continue
        if _URL_RE.match(token) or token.startswith("git@"):
            urls.append((token, line))
        index += 1
    rows: list[dict[str, str]] = []
    for url, line in urls:
        package, url_version = _url_identity(url)
        rows.append(
            _row(
                language="source",
                package=package,
                version=version or url_version,
                repository_url=url,
                path=path,
                line=line,
                kind=KIND_CONTAINER,
                notes="upstream source cloned into the image",
            )
        )
    return rows


def parse_dockerfile(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory every dependency a Dockerfile pulls in.

    Five shapes, all of which reach the shipped image and none of which any
    lockfile records:

    * ``FROM`` base images — but only real images. A stage alias referenced by
      a later ``FROM`` (``FROM kafka-base AS kafka-health-check``) is this
      repo's own build graph, and reporting it as a dependency both invents a
      package that does not exist and hides that nothing new was added.
    * ``COPY --from=<image>`` — ``COPY --from=ghcr.io/astral-sh/uv:latest`` is
      a genuine third-party image that never appears in any ``FROM``.
    * OS package installs (apt/apt-get/apk/yum/dnf) — where the GPL codec set
      enters.
    * ``pip`` / ``uv pip`` installs naming packages inline, which is how a
      dependency ends up in the image while being absent from requirements.txt.
    * ``git clone`` / ``wget`` / ``curl`` of source or binaries.
    """
    rows: list[dict[str, str]] = []
    aliases: set[str] = set()
    args_env: dict[str, str] = {}

    for _start, text, line_map in _logical_lines(_decode(data)):
        tokens = _tokens(text, line_map)
        if not tokens:
            continue
        keyword = tokens[0][0].upper()
        rest = tokens[1:]

        if keyword in {"ARG", "ENV"}:
            # Both feed the same substitution table. Versions in this repo are
            # routinely declared once as `ARG PYTHON_VERSION=3.13` / `ENV
            # NODE_VERSION=22` and referenced from FROM, curl and pip lines; a
            # row that reports the literal `${PYTHON_VERSION}` tells a reviewer
            # nothing about what shipped.
            for token, _line in rest:
                name, sep, value = token.partition("=")
                if sep:
                    args_env[name] = _expand(value, args_env)
            continue

        if keyword == "FROM":
            rows.extend(_from_rows(rest, aliases, args_env, path))
            continue

        if keyword in {"COPY", "ADD"}:
            rows.extend(_copy_from_rows(rest, aliases, args_env, path))
            continue

        if keyword != "RUN":
            continue

        for segment in _segments(rest):
            segment = [(_expand(token, args_env), line) for token, line in segment]
            command, command_args = _command(segment)
            if command in _OS_INSTALLERS:
                rows.extend(_os_packages(command_args, _OS_INSTALLERS[command], path))
            elif command in _PIP_COMMANDS:
                rows.extend(_pip_packages(command_args, path))
            elif command in {"uv", "uvx"} and command_args and command_args[0][0] == "pip":
                rows.extend(_pip_packages(command_args[1:], path))
            elif command in {"python", "python3"} and len(command_args) >= 2 and [
                token for token, _line in command_args[:2]
            ] == ["-m", "pip"]:
                rows.extend(_pip_packages(command_args[2:], path))
            elif command == "git":
                rows.extend(_cloned_sources(command_args, path))
            elif command in _FETCH_COMMANDS:
                rows.extend(_fetched_sources(command, command_args, path))

    return _dedupe(rows)


def _from_rows(
    rest: list[tuple[str, int]],
    aliases: set[str],
    args_env: dict[str, str],
    path: str,
) -> list[dict[str, str]]:
    words = [token for token, _line in rest if not token.startswith("--")]
    lines = [line for token, line in rest if not token.startswith("--")]
    if not words:
        return []
    ref = _expand(words[0], args_env)
    line = lines[0]
    if len(words) >= 3 and words[1].upper() == "AS":
        aliases.add(words[2])
    if ref == "scratch" or _matches_stage(ref, aliases):
        return []
    return [_image_row(ref, path, line, KIND_CONTAINER, "base image (FROM)")]


def _copy_from_rows(
    rest: list[tuple[str, int]],
    aliases: set[str],
    args_env: dict[str, str],
    path: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for token, line in rest:
        if not token.startswith("--from="):
            continue
        ref = _expand(token.removeprefix("--from="), args_env)
        # `--from=0` indexes an earlier stage; `--from=builder` names one.
        if ref.isdigit() or _matches_stage(ref, aliases):
            continue
        rows.append(
            _image_row(ref, path, line, KIND_CONTAINER, "external image (COPY --from)")
        )
    return rows


def _image_row(ref: str, path: str, line: int, kind: str, note: str) -> dict[str, str]:
    repo, tag = _image_identity(ref)
    notes = note
    if "$" in ref:
        notes = f"{note}; unresolved build/env substitution — resolve before review"
    return _row(
        language="container",
        package=repo,
        version=tag,
        path=path,
        line=line,
        kind=kind,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Narrow YAML reading
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<dash>-\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*:(?P<rest>.*)$")


def _yaml_rows(text: str) -> list[tuple[int, int, bool, str, str]]:
    """Yield (line, indent, is_list_item, key, value) for plain ``key: value``.

    This is not a YAML parser and must not become one: it exists because CI has
    no PyYAML and installing one would make the compliance gate fail closed.
    It understands indentation, ``- `` list markers, ``#`` comments and quotes,
    which is exactly enough for ``image:``, ``build:``, ``dependencies:``,
    ``repo:``, ``rev:`` and ``uses:`` — and nothing else. Block scalars and
    flow mappings are not interpreted, so a value written as ``{image: x}``
    is invisible here; no file in this repo writes one, and a new one would
    surface as an unparsed source rather than a wrong row.
    """
    out: list[tuple[int, int, bool, str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        match = _KEY_RE.match(raw.strip())
        if not match:
            continue
        value = _strip_comment(match.group("rest").strip())
        out.append(
            (lineno, indent, bool(match.group("dash")), match.group("key"), value)
        )
    return out


def _strip_comment(value: str) -> str:
    """Drop a trailing ``# …`` comment, but not a ``#`` inside quotes."""
    quote: str | None = None
    for index, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].strip()
    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


# ---------------------------------------------------------------------------
# docker compose
# ---------------------------------------------------------------------------


def parse_compose(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory the images a compose file pulls.

    Every ``image:`` under ``services:`` is a container this blueprint ships or
    runs, and this is where grafana (AGPL-3.0), minio (AGPL-3.0), kibana and
    logstash (Elastic-2.0) and cp-kafka (Confluent Community) actually enter.

    Two classes are excluded because they are not third-party:

    * A service with a ``build:`` key. Compose then treats ``image:`` as the
      *tag to give the locally built image*, not something to pull — the infra
      stack's ``image: elasticsearch`` is built from
      ``Dockerfiles/elasticsearch.Dockerfile``. Reporting it as Docker Hub's
      elasticsearch would attribute the wrong provenance and the wrong licence,
      while the real base image is inventoried from that Dockerfile.
    * Images that are clearly VSS artifacts this repo publishes.

    ``${VAR:-default}`` is resolved to its default, since that is what a
    developer running ``docker compose up`` with no overrides actually gets.
    A reference with no default stays literal and is flagged in ``notes``: an
    unresolvable image is still evidence a reviewer must chase, and dropping it
    would be exactly the silent blindness this module exists to remove.
    """
    entries = _yaml_rows(_decode(data))
    services_indent: int | None = None
    service_indent: int | None = None
    child_indent: int | None = None
    service_lines: list[tuple[int, str, str]] = []  # (line, key, value) of one service
    rows: list[dict[str, str]] = []

    def flush() -> None:
        if not service_lines:
            return
        has_build = any(key == "build" for _line, key, _value in service_lines)
        for line, key, value in service_lines:
            if key != "image":
                continue
            if has_build:
                continue
            ref = _expand_compose(_unquote(value))
            repo, tag = _image_identity(ref)
            if not repo or _is_vss_artifact(repo):
                continue
            rows.append(_image_row(ref, path, line, KIND_COMPOSE, "compose service image"))
        service_lines.clear()

    for lineno, indent, is_item, key, value in entries:
        if services_indent is None:
            if key == "services" and not value:
                services_indent = indent
            continue
        if indent <= services_indent:
            flush()
            services_indent = None
            service_indent = None
            child_indent = None
            continue
        if service_indent is None:
            service_indent = indent
        if indent == service_indent and not is_item:
            flush()
            child_indent = None
            continue
        if child_indent is None:
            child_indent = indent
        if indent == child_indent and not is_item:
            service_lines.append((lineno, key, value))
    flush()
    return _dedupe(rows)


def _expand_compose(value: str) -> str:
    # Empty env: compose defaults are what an un-overridden deploy resolves to,
    # and the CI runner's own environment must never leak into the inventory.
    return resolve_nested(value, {})


# ---------------------------------------------------------------------------
# Helm
# ---------------------------------------------------------------------------


def parse_helm_chart(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory the ``dependencies:`` list of a Chart.yaml.

    A Helm dependency is a chart pulled from a repository at install time; the
    haproxy ingress controller used to be one here and pulled
    ``haproxytech.github.io/helm-charts`` on every ``helm dependency build``.

    34 of the 61 Chart.yaml in this repo have no ``dependencies:`` block at
    all. That must be a quiet zero rows, not an exception — a parser that
    raises on the common case takes the whole gate down.

    In-repo subcharts (``repository: file://…``) are reported with a note
    rather than dropped: they are not third-party, but silently filtering a
    ``dependencies`` entry is the habit that produced the blind spot in the
    first place. Downstream can filter on the note; nobody can filter on a row
    that was never emitted.
    """
    entries = _yaml_rows(_decode(data))
    rows: list[dict[str, str]] = []
    in_block = False
    block_indent = 0
    current: dict[str, str] = {}
    current_line = 0

    def flush() -> None:
        nonlocal current
        name = _unquote(current.get("name", ""))
        if name:
            repository = _unquote(current.get("repository", ""))
            local = repository.startswith("file:")
            rows.append(
                _row(
                    language="helm",
                    package=name,
                    version=_unquote(current.get("version", "")),
                    repository_url=repository,
                    path=path,
                    line=current_line,
                    kind=KIND_CHART,
                    notes=(
                        "in-repo subchart (file:// repository) — not third-party"
                        if local
                        else "Helm chart dependency"
                    ),
                )
            )
        current = {}

    for lineno, indent, is_item, key, value in entries:
        if not in_block:
            if key == "dependencies" and indent == 0 and not value:
                in_block = True
                block_indent = indent
            continue
        if indent <= block_indent and not is_item:
            flush()
            in_block = False
            continue
        if is_item:
            flush()
            current_line = lineno
        current[key] = value
    flush()
    return rows


# ---------------------------------------------------------------------------
# CMake
# ---------------------------------------------------------------------------

_CMAKE_CALL_RE = re.compile(
    r"\b(find_package|FetchContent_Declare|ExternalProject_Add|pkg_check_modules)\s*\(",
    re.IGNORECASE,
)
_CMAKE_FIND_FLAGS = {
    "REQUIRED",
    "QUIET",
    "EXACT",
    "CONFIG",
    "MODULE",
    "NO_MODULE",
    "COMPONENTS",
    "OPTIONAL_COMPONENTS",
    "IMPORTED_TARGET",
    "GLOBAL",
    "NO_CMAKE_PATH",
    "NO_DEFAULT_PATH",
}
# CMake's own infrastructure modules. `find_package(Threads)` resolves to the
# platform's pthread support and `find_package(PkgConfig)` to the pkg-config
# tool itself; neither is a component anyone can ship or license, and rows for
# them would appear in every native build with nothing behind them. This list
# stays this short on purpose — it is not a licence table.
_CMAKE_BUILTIN_PACKAGES = {"Threads", "PkgConfig"}


def parse_cmake(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory native dependencies declared in CMake.

    ``find_package`` and ``pkg_check_modules`` link against libraries already
    on the build image (which is how a GPL codec becomes a link-time
    dependency), while ``FetchContent_Declare`` and ``ExternalProject_Add``
    download and build third-party source during the build — the strongest
    form of vendoring and the one no lockfile records.

    Calls are read with brace counting rather than line by line: CMake spreads
    a single ``FetchContent_Declare`` over five or six lines, and a per-line
    reader would see the name and never the GIT_REPOSITORY it came from.
    """
    text = _decode(data)
    text = re.sub(r"#[^\n]*", "", text)  # comments cannot contain a call
    rows: list[dict[str, str]] = []

    for match in _CMAKE_CALL_RE.finditer(text):
        function = match.group(1).lower()
        body, end = _balanced(text, match.end())
        if end < 0:
            continue
        line = text.count("\n", 0, match.start()) + 1
        words = body.replace("\n", " ").split()
        if not words:
            continue
        if function == "pkg_check_modules":
            rows.extend(_pkg_config_rows(words[1:], path, line))
            continue
        name = words[0].strip('"')
        if name in _CMAKE_BUILTIN_PACKAGES:
            continue
        version = ""
        repository = ""
        if function == "find_package":
            if len(words) > 1 and re.match(r"^\d", words[1]):
                version = words[1]
            note = "native dependency required at build time (find_package)"
        else:
            note = f"third-party source built during the build ({function})"
            for index, word in enumerate(words):
                upper = word.upper()
                if upper in {"GIT_REPOSITORY", "URL", "SVN_REPOSITORY"} and index + 1 < len(words):
                    repository = words[index + 1].strip('"')
                elif upper in {"GIT_TAG", "URL_HASH", "SVN_REVISION"} and index + 1 < len(words):
                    version = words[index + 1].strip('"')
        rows.append(
            _row(
                language="cmake",
                package=name,
                version=version,
                repository_url=repository,
                path=path,
                line=line,
                kind=KIND_BUILD,
                notes=note,
            )
        )
    return _dedupe(rows)


def _pkg_config_rows(words: list[str], path: str, line: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for word in words:
        token = word.strip('"')
        if token.upper() in _CMAKE_FIND_FLAGS or token.startswith("$"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9.+_-]*)\s*(?:[<>=]=?\s*(\S+))?$", token)
        if not match:
            continue
        rows.append(
            _row(
                language="cmake",
                package=match.group(1),
                version=match.group(2) or "",
                path=path,
                line=line,
                kind=KIND_BUILD,
                notes="native dependency resolved via pkg-config",
            )
        )
    return rows


def _balanced(text: str, start: int) -> tuple[str, int]:
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], index
        index += 1
    return "", -1


# ---------------------------------------------------------------------------
# pre-commit
# ---------------------------------------------------------------------------


def parse_precommit(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory the hook repositories pinned in .pre-commit-config.yaml.

    These are third-party programs the project pins by revision and every
    contributor executes, so OSRB wants them on the list — but they run on a
    developer's machine and in CI only, never inside a shipped artifact. The
    note says so, so a reviewer does not spend the same scrutiny on a linter
    that they spend on a linked library.

    ``repo: local`` and ``repo: meta`` are pre-commit's own sentinels for
    in-repo and built-in hooks; there is nothing third-party behind them.
    """
    entries = _yaml_rows(_decode(data))
    rows: list[dict[str, str]] = []
    in_block = False
    block_indent = 0
    current: dict[str, str] = {}
    current_line = 0

    def flush() -> None:
        nonlocal current
        repo = _unquote(current.get("repo", ""))
        if repo and repo not in {"local", "meta"}:
            rows.append(
                _row(
                    language="pre-commit",
                    package=_repo_slug(repo),
                    version=_unquote(current.get("rev", "")),
                    repository_url=repo,
                    path=path,
                    line=current_line,
                    kind=KIND_CI,
                    notes="pre-commit hook — runs at dev/CI time, does not ship",
                )
            )
        current = {}

    for lineno, indent, is_item, key, value in entries:
        if not in_block:
            if key == "repos" and not value:
                in_block = True
                block_indent = indent
            continue
        if indent <= block_indent and not is_item:
            flush()
            in_block = False
            continue
        if is_item and key == "repo":
            flush()
            current_line = lineno
        if key in {"repo", "rev"}:
            current[key] = value
    flush()
    return rows


def _repo_slug(url: str) -> str:
    """``https://github.com/trufflesecurity/trufflehog`` -> ``trufflesecurity/trufflehog``."""
    parts = urllib.parse.urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2:
        return f"{segments[-2]}/{segments[-1]}".removesuffix(".git")
    return url


# ---------------------------------------------------------------------------
# GitHub Actions
# ---------------------------------------------------------------------------

_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)\s*(?P<comment>#.*)?$")


def parse_actions_workflow(data: bytes, path: str) -> list[dict[str, str]]:
    """Inventory the third-party Actions a workflow runs.

    Every ``uses: owner/repo@ref`` executes someone else's code with this
    repository checked out, which makes it both a supply-chain surface and a
    third-party component OSRB reviews. Actions here are pinned by commit SHA
    with the human tag in a trailing comment (``@34e11487… # v4.3.1``); the SHA
    is the version that actually runs, so it is what the row records, and the
    tag goes to notes so a reviewer can tell v4 from v6 at a glance.

    ``uses: ./…`` is an action defined in this repository and is not a
    dependency.
    """
    rows: list[dict[str, str]] = []
    for lineno, raw in enumerate(_decode(data).splitlines(), 1):
        match = _USES_RE.match(raw)
        if not match:
            continue
        ref = _unquote(match.group("ref"))
        if ref.startswith("./") or ref.startswith("."):
            continue
        comment = (match.group("comment") or "").lstrip("# ").strip()
        if ref.startswith("docker://"):
            image = ref.removeprefix("docker://")
            rows.append(
                _image_row(image, path, lineno, KIND_CI, "container action image")
            )
            continue
        repo, _, version = ref.partition("@")
        segments = repo.split("/")
        if len(segments) < 2 or not version:
            continue
        notes = "GitHub Actions dependency — runs in CI, does not ship"
        if len(segments) > 2:
            notes = f"{notes}; reusable workflow {'/'.join(segments[2:])}"
        if comment:
            notes = f"{notes}; pinned to {comment}"
        rows.append(
            _row(
                language="github-action",
                package="/".join(segments[:2]),
                version=version,
                repository_url=f"https://github.com/{segments[0]}/{segments[1]}",
                path=path,
                line=lineno,
                kind=KIND_CI,
                notes=notes,
            )
        )
    return _dedupe(rows)


# ---------------------------------------------------------------------------
# Routing + inventory
# ---------------------------------------------------------------------------

Parser = Callable[[bytes, str], list[dict[str, str]]]


def parser_for(path: str) -> tuple[str, Parser] | None:
    """Return (source_kind, parser) for a path this module can inventory.

    ``osrb_scan.is_dependency_file`` decides what *counts* as a dependency
    file; this decides what this module can actually read. Keeping the two
    separate is deliberate — a file the first recognises and the second does
    not is a coverage gap that must surface as UNCOVERED_SOURCE, and merging
    them would make that gap unrepresentable.
    """
    base = _basename(path)
    lowered = base.lower()
    if lowered in {".pre-commit-config.yaml", ".pre-commit-config.yml"}:
        return KIND_CI, parse_precommit
    if path.startswith(".github/workflows/") and lowered.endswith((".yml", ".yaml")):
        return KIND_CI, parse_actions_workflow
    # Chart.lock repeats Chart.yaml's `dependencies:` list in the same shape,
    # with the resolved versions. Routing both keeps this function's coverage
    # identical to the set `osrb_scan.is_parsed` promises osrb_sources reads —
    # a file the scanner calls parsed and this module skips is a silent gap —
    # and the duplicate rows collapse on the shared inventory key.
    if lowered in {"chart.yaml", "chart.lock"}:
        return KIND_CHART, parse_helm_chart
    if lowered.endswith((".yml", ".yaml")) and "compose" in lowered:
        return KIND_COMPOSE, parse_compose
    if base == "CMakeLists.txt" or lowered.endswith(".cmake"):
        return KIND_BUILD, parse_cmake
    if lowered.endswith(".dockerignore"):
        return None
    if lowered == "dockerfile" or lowered.startswith("dockerfile.") or lowered.endswith(
        ".dockerfile"
    ):
        return KIND_CONTAINER, parse_dockerfile
    return None


def _row_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (row["source_kind"], row["language"], row["package"], row["new_version"])


def inventory_at_ref(
    ref: str,
    read: Callable[[str, str], bytes | None],
    paths: list[str],
) -> dict[tuple, dict]:
    """Inventory every parseable dependency-bearing file at one git ref.

    ``read(ref, path)`` returns the blob or None; None means the file does not
    exist at that ref, which is normal when diffing an added file against the
    base and must not be an error.

    A parser that raises produces an UNCOVERED_SOURCE row instead of an
    exception, for two reasons. The gate must not die on one malformed file and
    stop protecting every other PR in the repo; and the row's key is stable
    across refs, so a file that was already unparsable cancels out in the diff
    while a newly added one surfaces and fails the job. Swallowing the error
    would restore exactly the silence this module was written to end.

    Duplicate keys keep the first path in sorted order, so the same base image
    used by 30 Dockerfiles produces one row with one deterministic citation.
    """
    inventory: dict[tuple, dict] = {}
    for path in sorted(paths):
        routed = parser_for(path)
        if routed is None:
            continue
        kind, parser = routed
        data = read(ref, path)
        if data is None:
            continue
        try:
            rows = parser(data, path)
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log(f"WARNING: cannot parse {path}@{ref}: {exc!r}")
            rows = [
                make_row(
                    package=path,
                    change=CHANGE_UNCOVERED,
                    notes=f"{kind} parser failed: {exc!r}",
                    source_kind=kind,
                    source_file=path,
                    module=owning_module(path),
                    risk=license_risk(""),
                )
            ]
        for row in rows:
            inventory.setdefault(_row_key(row), row)
    return inventory
