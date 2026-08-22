#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that a deploy image tag points at the current source subtree.

For every configured VSS service image referenced from ``deploy/docker``
compose + env files, fetch the image's OCI index annotations from the
registry and compare ``com.nvidia.vss.source_tree_sha`` to the current
checkout's tree SHA for the corresponding source folder.

The ``ci-vss-oss`` build pipeline (``ci/tools/create_manifest.py``) stamps that
annotation at build time via ``docker buildx imagetools create --annotation
index:com.nvidia.vss.source_tree_sha=…``. Reading it directly from the manifest
sidesteps the brittle commit-SHA-in-tag lookup that breaks whenever a PR is
squash- or rebase-merged (the build commit gets orphaned even though the source
content survives unchanged on the merge target).

A git-based fallback remains for images that lack the annotation (older builds
predating the manifest-annotation rollout).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


# Optional per-service file (e.g. services/agent/.ignore_source_code_check) that
# lists gitignore-style paths which are NOT built into the image (unit tests,
# READMEs, AGENTS.md, ...). When the folder's git tree SHA differs from the
# deployed image's baked SHA but the only differing files are ignored here, the
# container is still considered a match — the differences never entered the image.
SOURCE_IGNORE_FILENAME = ".ignore_source_code_check"

SOURCE_TREE_SHA_LABEL = "com.nvidia.vss.source_tree_sha"
SOURCE_PATH_LABEL = "com.nvidia.vss.source_path"
IMAGE_NAME_LABEL = "com.nvidia.vss.image_name"

TAG_COMMIT_RE = re.compile(
    r"(?:^|[-_/])(?P<sha>[0-9a-f]{7,40})(?:$|[+._-])", re.IGNORECASE
)
IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<ref>\S+)\s*(?:#.*)?$")
COMPOSE_VAR_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-?])(?P<value>[^}]*))?\}"
)


@dataclass(frozen=True)
class ImageConfig:
    image_name: str
    source_path: Path
    # Compose basenames that identify this image in deploy/docker. Defaults to
    # (image_name,). The alert service is published/promoted as ``vss-alert-ms``
    # but the deploy stack still pins the released basename
    # ``vss-alert-verification``, so both must be recognized when scanning
    # compose refs. Still consumed by release_set.py and compose_image_golden.py.
    deploy_image_names: tuple[str, ...] = ()

    def compose_names(self) -> tuple[str, ...]:
        return self.deploy_image_names or (self.image_name,)


IMAGE_CONFIGS = {
    "vss-agent": ImageConfig(
        image_name="vss-agent", source_path=Path("services/agent")
    ),
    "vss-agent-ui": ImageConfig(
        image_name="vss-agent-ui", source_path=Path("services/ui")
    ),
    "vss-alert-ms": ImageConfig(
        image_name="vss-alert-ms",
        source_path=Path("services/alert"),
        deploy_image_names=("vss-alert-ms", "vss-alert-verification"),
    ),
    "vss-video-analytics-api": ImageConfig(
        image_name="vss-video-analytics-api",
        source_path=Path("services/analytics/video-analytics-api"),
    ),
    "vss-behavior-analytics": ImageConfig(
        image_name="vss-behavior-analytics",
        source_path=Path("services/analytics/behavior-analytics"),
    ),
    "vss-rt-cv": ImageConfig(
        image_name="vss-rt-cv",
        source_path=Path("services/rtvi/rt-cv"),
    ),
    "vss-vios-sensor": ImageConfig(
        image_name="vss-vios-sensor",
        source_path=Path("services/vios"),
    ),
    "vss-vios-streamprocessing": ImageConfig(
        image_name="vss-vios-streamprocessing",
        source_path=Path("services/vios"),
    ),
    "vss-vios-nvstreamer": ImageConfig(
        image_name="vss-vios-nvstreamer",
        source_path=Path("services/vios"),
    ),
    "vss-vios-ingress": ImageConfig(
        image_name="vss-vios-ingress",
        source_path=Path("services/vios"),
    ),
    "sdr-mw-l": ImageConfig(
        image_name="sdr-mw-l", source_path=Path("services/sdrc")
    ),
    "vss-configurator": ImageConfig(
        image_name="vss-configurator",
        source_path=Path("services/configurators/vss-configurator"),
    ),
    "vss-rt-config-adaptor": ImageConfig(
        image_name="vss-rt-config-adaptor",
        source_path=Path("services/configurators/vss-rt-config-adaptor"),
    ),
    "vss-rt-embed": ImageConfig(
        image_name="vss-rt-embed", source_path=Path("services/rtvi/rt-embed")
    ),
}

DEPLOY_DIR = Path("deploy/docker")


def run_git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_stdout(repo: Path, *args: str) -> str:
    return run_git(repo, *args).stdout.strip()


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines from .env content. Shared with the gate helper
    so both resolve the same way regardless of which revision the text is read
    from (working tree vs ``git show``)."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = strip_quotes(value)
    return values


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env_text(path.read_text())


def image_name(ref: str) -> str:
    ref_without_digest = ref.split("@", 1)[0]
    last_component = ref_without_digest.rsplit("/", 1)[-1]
    return last_component.split(":", 1)[0]


def image_tag(ref: str) -> str | None:
    ref_without_digest = ref.split("@", 1)[0]
    slash_index = ref_without_digest.rfind("/")
    colon_index = ref_without_digest.rfind(":")
    if colon_index <= slash_index:
        return None
    return ref_without_digest[colon_index + 1 :]


def commit_prefix_from_tag(tag: str | None) -> str | None:
    if not tag:
        return None
    matches = list(TAG_COMMIT_RE.finditer(tag))
    if not matches:
        return None
    return matches[-1].group("sha").lower()


def image_refs_in_text(text: str, expected_image_name: str | Iterable[str]) -> list[str]:
    """Extract ``image:`` refs matching ``expected_image_name`` from compose
    content. ``expected_image_name`` may be a single basename or an iterable of
    accepted basenames (for images whose deploy basename differs from the
    published name). Shared with the gate helper (see ``parse_env_text``).

    The registry+name portion may itself be parameterized via the
    ``deploy/docker/containers.env`` single source of truth, e.g.
    ``image: ${VSS_AGENT_IMAGE:-nvcr.io/nvstaging/vss-core/vss-agent}:${VSS_AGENT_VERSION}``.
    We therefore expand inline ``${VAR:-default}`` fallbacks (with an empty env,
    so only the literal defaults are applied) purely to compute the image name
    for matching. The RAW ref is still returned so the caller resolves the tag
    against the real ``.env`` values exactly as before."""
    accepted = (
        {expected_image_name}
        if isinstance(expected_image_name, str)
        else set(expected_image_name)
    )
    refs: list[str] = []
    for line in text.splitlines():
        match = IMAGE_LINE_RE.match(line)
        if not match:
            continue
        ref = strip_quotes(match.group("ref"))
        name_probe, _ = resolve_compose_vars(ref, {})
        if image_name(name_probe) in accepted and ref not in refs:
            refs.append(ref)
    return refs


def find_image_refs(compose_file: Path, expected_image_name: str | Iterable[str]) -> list[str]:
    return image_refs_in_text(compose_file.read_text(), expected_image_name)


def resolve_compose_vars(text: str, env: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    """Resolve Compose substitutions, including nested default expressions."""
    missing: set[str] = set()

    def resolve(value: str) -> str:
        result: list[str] = []
        index = 0
        while index < len(value):
            match = re.match(
                r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?])?",
                value[index:],
            )
            if not match:
                result.append(value[index])
                index += 1
                continue

            start = index
            name = match.group("name")
            op = match.group("op")
            cursor = index + match.end()
            if op is None:
                if cursor < len(value) and value[cursor] == "}":
                    env_value = env.get(name)
                    if env_value is None:
                        missing.add(name)
                        result.append(value[start : cursor + 1])
                    else:
                        result.append(env_value)
                    index = cursor + 1
                    continue
                result.append(value[index])
                index += 1
                continue

            depth = 1
            end = cursor
            while end < len(value) and depth:
                if value.startswith("${", end):
                    depth += 1
                    end += 2
                elif value[end] == "}":
                    depth -= 1
                    end += 1
                else:
                    end += 1
            if depth:
                result.append(value[index])
                index += 1
                continue

            fallback = value[cursor : end - 1]
            env_value = env.get(name)
            use_fallback = env_value is None or (op.startswith(":") and env_value == "")
            if op.endswith("?") and use_fallback:
                missing.add(name)
                result.append(value[start:end])
            elif use_fallback:
                result.append(resolve(fallback))
            else:
                result.append(env_value)
            index = end
        return "".join(result)

    return resolve(text), tuple(sorted(missing))


def resolve_commit(repo: Path, prefix: str) -> str | None:
    result = run_git(repo, "rev-parse", "--verify", f"{prefix}^{{commit}}", check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def tree_sha(repo: Path, commit: str, source_path: Path) -> str | None:
    result = run_git(
        repo, "rev-parse", f"{commit}:{source_path.as_posix()}", check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def load_source_ignore_patterns(repo: Path, source_path: Path) -> list[str]:
    """Read gitignore-style patterns from ``<source_path>/.ignore_source_code_check``.

    Blank lines and ``#`` comments are skipped. Returns an empty list when the
    file is absent (so the source comparison behaves exactly as before)."""
    ignore_file = repo / source_path / SOURCE_IGNORE_FILENAME
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for raw_line in ignore_file.read_text().splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def path_is_ignored(rel: str, patterns: list[str]) -> bool:
    """Match ``rel`` (POSIX path relative to the service folder) against
    gitignore-style ``patterns``:

    * ``dir/`` or a bare ``dir`` name → matches everything under that directory
    * ``a/b/**`` or ``a/b/*`` → matches everything under ``a/b``
    * a pattern with ``/`` and globs → ``fnmatch`` on the full relative path
    * a bare name/glob (no ``/``) → ``fnmatch`` on the basename (any depth) or path
    """
    base = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        pat = pattern.rstrip()
        if pat.endswith("/"):
            # Directory: ``tests/`` ⇒ tests/ and everything under it.
            directory = pat.rstrip("/")
            if rel == directory or rel.startswith(directory + "/"):
                return True
        elif pat.endswith("/**") or pat.endswith("/*"):
            directory = pat.rsplit("/", 1)[0].rstrip("/")
            if rel == directory or rel.startswith(directory + "/"):
                return True
        elif pat.startswith("**/"):
            # Any depth: ``**/*.md`` ⇒ every .md file at any depth.
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat[3:]):
                return True
        elif "/" in pat:
            # Path pattern anchored at the service root.
            if fnmatch.fnmatch(rel, pat):
                return True
        else:
            # Bare name/glob, anchored at the service root (NOT basename-anywhere,
            # so a bare ``README.md`` never ignores a shipped src/**/README.md):
            #   - exact file, or a directory prefix (``tests`` ⇒ tests/...)
            #   - a glob (``*.md``) matched against the root-relative path
            if rel == pat or rel.startswith(pat + "/"):
                return True
            if ("*" in pat or "?" in pat) and fnmatch.fnmatch(rel, pat):
                return True
    return False


def changed_source_paths(
    repo: Path, source_path: Path, from_commit: str, to_ref: str
) -> list[str] | None:
    """Paths under ``source_path`` that differ between ``from_commit`` and
    ``to_ref``, relative to the service folder. Returns ``None`` if git can't
    diff (e.g. the commit is unknown)."""
    src = source_path.as_posix()
    result = run_git(
        repo, "diff", "--name-only", from_commit, to_ref, "--", src, check=False
    )
    if result.returncode != 0:
        return None
    prefix = src + "/"
    rels: list[str] = []
    for line in result.stdout.splitlines():
        path = line.strip()
        if not path:
            continue
        rels.append(path[len(prefix) :] if path.startswith(prefix) else path)
    return rels


def diff_is_ignored_only(
    repo: Path, source_path: Path, from_commit: str, to_ref: str, patterns: list[str]
) -> tuple[bool, list[str]]:
    """Return ``(True, changed)`` when every file that differs in ``source_path``
    between ``from_commit`` and ``to_ref`` is ignored by ``patterns``. Returns
    ``(False, [])`` when git can't diff or nothing changed."""
    changed = changed_source_paths(repo, source_path, from_commit, to_ref)
    if not changed:
        return False, []
    not_ignored = [c for c in changed if not path_is_ignored(c, patterns)]
    return (not not_ignored), changed


def rescue_ignored_only(
    repo: Path,
    config: "ImageConfig",
    tag: str | None,
    build_tree_sha: str,
    current_commit: str,
) -> bool:
    """When the folder tree SHA differs from the deployed image, pass anyway iff
    the ONLY differing files are ignored (unit tests, docs, ...).

    Locates the image's build commit from the tag's commit-SHA suffix and
    confirms its ``source_path`` tree matches ``build_tree_sha`` (so we know we
    found the exact source the image was built from) before diffing to HEAD.
    """
    patterns = load_source_ignore_patterns(repo, config.source_path)
    if not patterns:
        return False
    prefix = commit_prefix_from_tag(tag)
    build_commit = resolve_commit(repo, prefix) if prefix else None
    if not build_commit:
        return False
    if tree_sha(repo, build_commit, config.source_path) != build_tree_sha:
        # The tag-suffix commit isn't the one this image was built from; don't
        # trust a diff against it.
        return False
    ignored_only, changed = diff_is_ignored_only(
        repo, config.source_path, build_commit, current_commit, patterns
    )
    if not ignored_only:
        return False
    src = config.source_path.as_posix()
    print(f"  build commit:  {build_commit[:12]} ({src}/ tree matches the image)")
    print(
        f"  {len(changed)} file(s) differ vs the built image, all ignored by {SOURCE_IGNORE_FILENAME}:"
    )
    for path in sorted(changed):
        print(f"    - {path}")
    print(
        f"  [PASS] only non-build (ignored) files differ from the deployed {config.image_name} image."
    )
    return True


@dataclass(frozen=True)
class ImageManifestLabels:
    source_tree_sha: str
    source_path: str | None
    image_name: str | None


_INDEX_ACCEPT = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
_CONFIG_ACCEPT = (
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
    "application/json",
)


def _parse_image_ref(image_ref: str) -> tuple[str, str, str] | None:
    """Split ``registry/name:tag``, ``registry/name@digest``, or
    ``registry/name:tag@digest``.

    Returns ``(registry, name, reference)`` or ``None`` if the ref doesn't have
    a recognizable host prefix and tag/digest. The reference is either a
    ``sha256:...`` digest or a tag string (digest wins when both are present,
    since it's the immutable, authoritative pointer).
    """
    # Digest form (optionally with a leading ``:tag`` we must strip from the
    # repo name, e.g. ``registry/name:tag@sha256:...``).
    if "@" in image_ref:
        repo_part, _, digest = image_ref.partition("@")
        if "/" not in repo_part or not digest.startswith("sha256:"):
            return None
        registry, _, name = repo_part.partition("/")
        # A ``:`` in the last path component is a tag, not part of the name.
        last_segment = name.rsplit("/", 1)[-1]
        if ":" in last_segment:
            name = name.rsplit(":", 1)[0]
        return registry, name, digest

    # Tag form
    slash = image_ref.find("/")
    colon = image_ref.rfind(":")
    if slash < 0 or colon < slash:
        return None
    registry = image_ref[:slash]
    name = image_ref[slash + 1 : colon]
    tag = image_ref[colon + 1 :]
    return registry, name, tag


def _fetch_bearer_token(
    registry: str,
    name: str,
    ngc_key: str | None,
    registry_username: str | None = None,
    registry_password: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a registry pull token via the ``WWW-Authenticate`` challenge flow.

    Some registries (Docker Hub, GHCR) accept anonymous tokens for public
    repos. Private GHCR packages require the workflow actor and GitHub token;
    nvcr.io requires ``$oauthtoken`` plus the NGC API key. Returns
    ``(token, None)`` or ``(None, error_message)``.
    """
    import base64
    import urllib.error
    import urllib.request

    challenge_url = f"https://{registry}/v2/"
    try:
        with urllib.request.urlopen(challenge_url, timeout=20):
            return None, None  # registry doesn't require auth at all
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            return None, f"unexpected status {exc.code} from {challenge_url}"
        www_auth = exc.headers.get("WWW-Authenticate", "")
    except urllib.error.URLError as exc:
        return None, f"network error reaching {challenge_url}: {exc}"

    if not www_auth.lower().startswith("bearer "):
        return None, f"registry returned non-Bearer auth challenge: {www_auth!r}"

    # Parse Bearer params: realm="...",service="...",scope="..."
    params: dict[str, str] = {}
    for piece in www_auth[len("bearer ") :].split(","):
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        params[k.strip()] = v.strip().strip('"')

    realm = params.get("realm")
    if not realm:
        return None, f"Bearer challenge missing realm: {www_auth!r}"

    # Force scope to this repo's pull scope so we get a usable token even if
    # the original challenge didn't include it.
    scope = f"repository:{name}:pull"
    token_url = f"{realm}?service={urllib.parse.quote(params.get('service', ''))}&scope={urllib.parse.quote(scope)}"

    req = urllib.request.Request(token_url)
    if ngc_key:
        basic = base64.b64encode(f"$oauthtoken:{ngc_key}".encode()).decode()
        req.add_header("Authorization", f"Basic {basic}")
    elif registry_username and registry_password:
        basic = base64.b64encode(
            f"{registry_username}:{registry_password}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        return None, f"token endpoint returned {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"network error fetching token: {exc}"

    try:
        token_payload = json.loads(body)
    except json.JSONDecodeError:
        return None, "token response was not valid JSON"
    token = token_payload.get("token") or token_payload.get("access_token")
    if not token:
        return None, "token response missing 'token'/'access_token'"
    return token, None


def _registry_get_json(
    registry: str, name: str, reference: str, token: str | None, accept: tuple[str, ...]
) -> tuple[dict | None, str | None]:
    """GET a manifest or blob from the OCI Distribution API and parse JSON.

    ``reference`` is either a tag string or a ``sha256:...`` digest.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    url = f"https://{registry}/v2/{name}/manifests/{urllib.parse.quote(reference, safe=':')}"
    if reference.startswith("sha256:") and "blobs" in accept[0]:
        # Heuristic: image config is served from the blobs endpoint, not manifests.
        # We pivot below for clarity instead.
        url = f"https://{registry}/v2/{name}/blobs/{reference}"
    req = urllib.request.Request(url)
    req.add_header("Accept", ", ".join(accept))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        return None, f"GET {url} returned {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"network error fetching {url}: {exc}"
    try:
        return json.loads(body), None
    except json.JSONDecodeError as exc:
        return None, f"response from {url} is not valid JSON: {exc}"


def _registry_get_blob(
    registry: str, name: str, digest: str, token: str | None, accept: tuple[str, ...]
) -> tuple[dict | None, str | None]:
    """GET an image config blob from the OCI ``blobs`` endpoint and parse JSON."""
    import urllib.error
    import urllib.request

    url = f"https://{registry}/v2/{name}/blobs/{digest}"
    req = urllib.request.Request(url)
    req.add_header("Accept", ", ".join(accept))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        return None, f"GET {url} returned {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"network error fetching {url}: {exc}"
    try:
        return json.loads(body), None
    except json.JSONDecodeError as exc:
        return None, f"response from {url} is not valid JSON: {exc}"


def read_image_manifest_labels(
    image_ref: str,
) -> tuple[ImageManifestLabels | None, str | None, bool]:
    """Read ``com.nvidia.vss.*`` metadata from the image's config blob labels.

    Both the agent and the UI Dockerfiles emit ``LABEL com.nvidia.vss.*`` lines
    (or the multiarch-builder stamps them via ``--label``), so the resulting
    image **config blob** carries the source-tree-SHA label regardless of
    whether the manifest is stored as an OCI image index or a Docker manifest
    list. Reading the label from the config blob is therefore strictly more
    portable than reading manifest-index annotations — both formats survive
    the round-trip through nvcr.io and the artifacts-promotion copy.

    Chain: top-level index/list → first platform manifest → config blob → ``Labels``.

    Returns a 3-tuple ``(labels, reason, can_fallback)``:

    * ``(labels, None, False)`` on success.
    * ``(None, reason, True)`` if the manifest and config blob were fetched
      successfully but neither carries a ``source_tree_sha`` label — the
      legacy case for images built before the annotation rollout. The caller
      may fall back to git-SHA resolution.
    * ``(None, reason, False)`` for any registry-side failure (404, auth,
      network, malformed response, missing platform manifest, etc.). These
      are real bugs — the image is missing, mis-tagged, or unreachable — and
      the caller must fail rather than mask the issue with a git-SHA fallback
      that happens to find the tag's commit-SHA suffix in the local checkout.
    """
    parsed = _parse_image_ref(image_ref)
    if parsed is None:
        return None, f"could not parse image ref: {image_ref}", False
    registry, name, reference = parsed

    ngc_key = os.environ.get("NGC_CLI_API_KEY") or os.environ.get("NGC_API_KEY")
    ghcr_username = os.environ.get("GHCR_USERNAME") if registry == "ghcr.io" else None
    ghcr_token = os.environ.get("GHCR_TOKEN") if registry == "ghcr.io" else None
    token, token_err = _fetch_bearer_token(
        registry,
        name,
        ngc_key,
        registry_username=ghcr_username,
        registry_password=ghcr_token,
    )
    if token_err:
        return None, token_err, False

    # 1. Fetch the top-level index/list.
    index, err = _registry_get_json(registry, name, reference, token, _INDEX_ACCEPT)
    if err:
        return None, f"index fetch failed: {err}", False
    if index is None:
        return None, "registry returned empty index", False

    # 2. Pick a platform-bearing manifest entry (skip attestation manifests).
    manifests = index.get("manifests") or []
    if not manifests:
        # Single-arch image: the response IS the platform manifest.
        platform_manifest = index
    else:
        platform_manifest = None
        for entry in manifests:
            platform = entry.get("platform") or {}
            if platform.get("architecture") in ("unknown", None):
                continue
            digest = entry.get("digest")
            if not digest:
                continue
            platform_manifest_resp, err = _registry_get_json(
                registry, name, digest, token, _MANIFEST_ACCEPT
            )
            if err:
                continue
            platform_manifest = platform_manifest_resp
            break
        if platform_manifest is None:
            return None, "no usable platform manifest in index", False

    # 3. Follow config.digest to the config blob, read its Labels.
    config_ref = (platform_manifest.get("config") or {}).get("digest")
    if not config_ref:
        return None, "platform manifest has no config.digest", False
    config, err = _registry_get_blob(registry, name, config_ref, token, _CONFIG_ACCEPT)
    if err:
        return None, f"config blob fetch failed: {err}", False
    if config is None:
        return None, "registry returned empty config blob", False

    labels = (config.get("config") or {}).get("Labels") or {}
    tree = labels.get(SOURCE_TREE_SHA_LABEL)
    if not tree:
        # Manifest + config were reachable, but the source-tree-SHA label was
        # never stamped on this image. This is the legacy case (pre-annotation
        # rollout) and is the only condition where the git-SHA fallback may
        # legitimately fire.
        return None, f"image config has no {SOURCE_TREE_SHA_LABEL} label", True

    return (
        ImageManifestLabels(
            source_tree_sha=tree,
            source_path=labels.get(SOURCE_PATH_LABEL),
            image_name=labels.get(IMAGE_NAME_LABEL),
        ),
        None,
        False,
    )


def discover_compose_files(repo_root: Path) -> list[Path]:
    # Validate committed deploy state; generated or untracked compose files in
    # the workspace should not affect the container-source gate.
    tracked = run_git(
        repo_root,
        "ls-files",
        "--",
        DEPLOY_DIR.as_posix(),
        check=False,
    )
    if tracked.returncode == 0:
        files = [
            repo_root / line
            for line in tracked.stdout.splitlines()
            if line.endswith((".yml", ".yaml"))
        ]
        if files:
            return sorted(path for path in files if path.is_file())

    deploy = repo_root / DEPLOY_DIR
    files: set[Path] = set()
    for pattern in ("**/*.yml", "**/*.yaml"):
        for path in deploy.glob(pattern):
            if path.is_file():
                files.add(path)
    return sorted(files)


def discover_env_files(repo_root: Path) -> list[Path]:
    deploy = repo_root / DEPLOY_DIR
    files: set[Path] = set()
    for pattern in ("**/.env", "**/*.env"):
        for path in deploy.glob(pattern):
            if path.is_file():
                files.add(path)
    return sorted(files)


@dataclass(frozen=True)
class ResolvedImage:
    resolved_ref: str
    origins: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class UnresolvedImage:
    compose_rel: str
    env_rel: str | None
    raw_ref: str
    missing: tuple[str, ...]


def collect_resolved_images(
    repo_root: Path,
    config: ImageConfig,
    compose_files: list[Path],
    env_files: list[Path],
) -> tuple[list[ResolvedImage], list[UnresolvedImage]]:
    env_caches = {ef: read_env_file(ef) for ef in env_files}
    by_resolved: dict[str, list[tuple[str, str | None]]] = {}
    unresolved: list[UnresolvedImage] = []

    for compose_file in compose_files:
        raw_refs = find_image_refs(compose_file, config.compose_names())
        if not raw_refs:
            continue
        compose_rel = str(compose_file.relative_to(repo_root))
        for raw_ref in raw_refs:
            _, needed = resolve_compose_vars(raw_ref, {})
            if not needed:
                resolved, _ = resolve_compose_vars(raw_ref, dict(os.environ))
                by_resolved.setdefault(resolved, []).append((compose_rel, None))
                continue

            any_applicable = False
            for env_file in env_files:
                env_values = env_caches[env_file]
                if not all(name in env_values for name in needed):
                    continue
                any_applicable = True
                env_rel = str(env_file.relative_to(repo_root))
                resolved, missing = resolve_compose_vars(
                    raw_ref, {**env_values, **os.environ}
                )
                if missing:
                    unresolved.append(
                        UnresolvedImage(compose_rel, env_rel, raw_ref, missing)
                    )
                else:
                    by_resolved.setdefault(resolved, []).append((compose_rel, env_rel))

            if not any_applicable:
                unresolved.append(
                    UnresolvedImage(compose_rel, None, raw_ref, tuple(sorted(needed)))
                )

    images = [
        ResolvedImage(resolved_ref=ref, origins=tuple(origins))
        for ref, origins in sorted(by_resolved.items())
    ]
    return images, unresolved


def check_resolved_image(
    repo_root: Path,
    config: ImageConfig,
    item: ResolvedImage,
    current_commit: str,
    current_tree: str,
    idx: int,
    total: int,
) -> bool:
    src = config.source_path.as_posix()
    print(f"[{idx}/{total}] {item.resolved_ref}")
    print(f"  produced by {len(item.origins)} (compose, env) combination(s):")
    for compose_rel, env_rel in item.origins:
        suffix = f"  ←  {env_rel}" if env_rel else "  (no env vars)"
        print(f"    - {compose_rel}{suffix}")

    tag = image_tag(item.resolved_ref)
    print(f"  tag:           {tag or '<missing>'}")

    # Primary path: read the build-time tree SHA from the image's OCI manifest
    # annotations. This is the authoritative source of truth (set by
    # ci-vss-oss ci/tools/create_manifest.py) and works regardless of whether
    # the original build commit still exists in any git branch — which is the
    # common case after a squash- or rebase-merge orphans the PR-head SHA.
    labels, oci_reason, can_fallback = read_image_manifest_labels(item.resolved_ref)
    if labels:
        if labels.image_name and labels.image_name != config.image_name:
            print(
                f"  [FAIL] manifest {IMAGE_NAME_LABEL}={labels.image_name!r}, "
                f"expected {config.image_name!r}"
            )
            return False
        if labels.source_path and labels.source_path != src:
            print(
                f"  [FAIL] manifest {SOURCE_PATH_LABEL}={labels.source_path!r}, "
                f"expected {src!r}"
            )
            return False
        print(f"  manifest:      {SOURCE_TREE_SHA_LABEL}={labels.source_tree_sha}")
        print(f"  comparing {src}/:")
        print(f"    at HEAD ({current_commit[:12]}):  {current_tree}")
        print(f"    in image manifest:              {labels.source_tree_sha}")
        if labels.source_tree_sha == current_tree:
            print("    → identical")
            print("  [PASS]")
            return True
        print("    → DIFFERENT")
        # The whole-folder tree SHA differs, but the deployed image may still
        # match the built source if the only differences are non-build files
        # (unit tests, docs) listed in <src>/.ignore_source_code_check.
        if rescue_ignored_only(
            repo_root, config, tag, labels.source_tree_sha, current_commit
        ):
            return True
        print(
            f"  [FAIL] {config.image_name} container does NOT match the current {src}/ source."
        )
        print(
            f"         Image's source tree SHA at build time: {labels.source_tree_sha}"
        )
        print(f"         Current {src}/ tree SHA:               {current_tree}")
        _print_fix_hint(config)
        return False

    # Only fall back when the manifest + config were reachable but the
    # source-tree-SHA label is absent (legacy images predating the
    # annotation rollout in ci-vss-oss). For any registry-side failure
    # (404, auth, network, malformed) we must hard-fail — otherwise a
    # mis-promoted tag is silently rescued by the git-SHA fallback as
    # long as the tag's commit-SHA suffix happens to exist in the local
    # checkout, which masks a real bug.
    if not can_fallback:
        print(f"  manifest:      UNREACHABLE ({oci_reason})")
        print(
            f"  [FAIL] could not fetch the image manifest for {item.resolved_ref}. "
            "The image is missing from the registry, the credentials are "
            "wrong, or the network is broken — fix the upstream promotion "
            "rather than falling back to the tag's git-SHA suffix."
        )
        return False

    # Fallback: pre-annotation images (predate the manifest-annotation rollout
    # in ci-vss-oss). Resolve the commit SHA encoded in the tag suffix and
    # compute the tree SHA at that commit locally.
    print(f"  manifest:      <no {SOURCE_TREE_SHA_LABEL} annotation> ({oci_reason})")
    print("  falling back to git-SHA resolution …")

    prefix = commit_prefix_from_tag(tag)
    if not prefix:
        print(
            "  [FAIL] tag does not contain a git commit SHA suffix; cannot verify source."
        )
        return False

    tag_commit = resolve_commit(repo_root, prefix)
    if not tag_commit:
        print(f"  built from:    {prefix}  (NOT found in this checkout)")
        print(
            "  [FAIL] could not resolve this SHA locally and the image has no "
            f"{SOURCE_TREE_SHA_LABEL} annotation. This usually happens after a "
            "squash/rebase-merge orphaned the build commit. Re-promote the "
            "image with a manifest annotation, or pin a tag whose suffix is a "
            "develop-resident SHA."
        )
        return False
    print(f"  built from:    {tag_commit}")

    tag_tree = tree_sha(repo_root, tag_commit, config.source_path)
    if not tag_tree:
        print(f"  [FAIL] could not read {src}/ at commit {tag_commit[:12]}.")
        return False

    print(f"  comparing {src}/:")
    print(f"    at HEAD              ({current_commit[:12]}):  {current_tree}")
    print(f"    at container commit  ({tag_commit[:12]}):  {tag_tree}")
    if tag_tree == current_tree:
        print("    → identical")
        print("  [PASS]")
        return True
    print("    → DIFFERENT")
    # Same ignored-only rescue as the manifest path: the build commit is known
    # here (tag_commit), so pass iff only non-build files changed since.
    patterns = load_source_ignore_patterns(repo_root, config.source_path)
    if patterns:
        ignored_only, changed = diff_is_ignored_only(
            repo_root, config.source_path, tag_commit, current_commit, patterns
        )
        if ignored_only:
            print(
                f"  {len(changed)} file(s) differ vs {tag_commit[:12]}, all ignored by "
                f"{SOURCE_IGNORE_FILENAME}:"
            )
            for path in sorted(changed):
                print(f"    - {path}")
            print(
                f"  [PASS] only non-build (ignored) files differ from the deployed {config.image_name} image."
            )
            return True
    print(
        f"  [FAIL] {config.image_name} container does NOT match the current {src}/ source."
    )
    print(f"         See the diff:  git diff {tag_commit[:12]} HEAD -- {src}")
    _print_fix_hint(config)
    return False


def _print_fix_hint(config: ImageConfig) -> None:
    print()
    print("  How to fix:")
    print("    1. Find the 'Trigger Downstream Pipeline' job on this PR's CI run.")
    print("       It links to a downstream pipeline that builds + promotes new")
    print(f"       {config.image_name} images from the current source.")
    print("    2. In that downstream pipeline, open the 'promote' job and copy the")
    print(f"       newly promoted {config.image_name} image tag from its output.")
    print(f"    3. Update the {config.image_name} tag in the (compose, env)")
    print("       combination(s) listed above so they reference the new tag,")
    print("       commit, and push.")


def verify(repo_root: Path, config: ImageConfig) -> int:
    src = config.source_path.as_posix()
    bar = "=" * 78
    print(bar)
    print(
        f" {config.image_name}  —  check every deployable container tag against {src}/"
    )
    print(bar)
    print()

    current_commit = git_stdout(repo_root, "rev-parse", "HEAD")
    current_branch = git_stdout(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    current_tree = tree_sha(repo_root, "HEAD", config.source_path)
    if not current_tree:
        print(f"ERROR: could not resolve HEAD:{src}", file=sys.stderr)
        return 1

    print("Current source (HEAD)")
    print(f"  branch:  {current_branch}")
    print(f"  commit:  {current_commit}")
    print(f"  folder:  {src}/  (content hash: {current_tree})")
    print()

    compose_files = discover_compose_files(repo_root)
    env_files = discover_env_files(repo_root)
    print(
        f"Scanned {len(compose_files)} compose file(s) and {len(env_files)} env file(s) "
        f"under {DEPLOY_DIR.as_posix()}/."
    )
    print()

    images, unresolved = collect_resolved_images(
        repo_root, config, compose_files, env_files
    )

    if unresolved:
        print(f"WARNING: {len(unresolved)} unresolved image reference(s):")
        for item in unresolved:
            origin = f"{item.compose_rel}" + (
                f"  ←  {item.env_rel}" if item.env_rel else ""
            )
            print(f"  - {origin}")
            print(f"      raw:      {item.raw_ref}")
            print(f"      missing:  {', '.join(item.missing)}")
        print()

    if not images:
        print(
            f"ERROR: no resolvable {config.image_name} image references found.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Found {len(images)} unique {config.image_name} image reference(s) to check:"
    )
    print()

    failures = 0
    for idx, item in enumerate(images, start=1):
        if not check_resolved_image(
            repo_root, config, item, current_commit, current_tree, idx, len(images)
        ):
            failures += 1
        print()

    print(bar)
    if failures or unresolved:
        problems = []
        if failures:
            problems.append(f"{failures} failure(s) out of {len(images)} unique ref(s)")
        if unresolved:
            problems.append(f"{len(unresolved)} unresolved ref(s)")
        print(f"Result: {'; '.join(problems)}.")
        return 1
    print(f"Result: all {len(images)} unique ref(s) match.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-name", choices=sorted(IMAGE_CONFIGS), required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    return verify(args.repo_root.resolve(), IMAGE_CONFIGS[args.image_name])


if __name__ == "__main__":
    raise SystemExit(main())
