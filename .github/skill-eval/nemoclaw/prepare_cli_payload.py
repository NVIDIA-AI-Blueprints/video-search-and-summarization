#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build a pinned, offline-installable NemoClaw CLI payload on the coordinator.

The Brev workers intentionally are not trusted with GitHub/npm credentials and
may have no outbound internet access.  Build the exact release on the trusted
GitHub Actions coordinator, cache it by immutable source revision, and hand the
result to ``brev_env`` for checksum-verified transfer.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Mapping, Sequence


SCHEMA_VERSION = 3
DEFAULT_REF = "v0.0.103"
RELEASES: Mapping[str, tuple[str, str]] = {
    # Immutable tag resolution verified against NVIDIA/NemoClaw.  Do not add a
    # ref without pinning its full commit: a version-looking tag alone is not
    # an integrity boundary for a coordinator-built executable payload.
    "v0.0.103": (
        "0.0.103",
        "db31c286129e878c3356eed49f76ab259561e47e",
    ),
}
SOURCE_URL = "https://github.com/NVIDIA/NemoClaw.git"
PAYLOAD_KIND = "nemoclaw-skill-eval-offline-runtime"
NODE_VERSION = "22.22.1"
NODE_ASSET_NAME = f"node-v{NODE_VERSION}-linux-x64.tar.xz"
NODE_ASSET_MEMBER = f"node-v{NODE_VERSION}-linux-x64/bin/node"
NODE_ASSET_SHA256 = "9a6bc82f9b491279147219f6a18add1e18424dce90d41d2a5fcd69d4924ba3aa"
NODE_ASSET_URL = f"https://nodejs.org/dist/v{NODE_VERSION}/{NODE_ASSET_NAME}"
OPENSHELL_VERSION = "0.0.85"
OPENSHELL_RELEASE_TAG = f"v{OPENSHELL_VERSION}"
OPENSHELL_RELEASE_ROOT = (
    f"https://github.com/NVIDIA/OpenShell/releases/download/{OPENSHELL_RELEASE_TAG}"
)
# These are the three Linux x86_64 artifacts consumed by the exact NemoClaw
# source revision above.  The digests are copied from v0.0.103's
# scripts/install-openshell.sh, not discovered dynamically from GitHub.
OPENSHELL_ASSETS: Mapping[str, tuple[str, str]] = {
    "openshell-x86_64-unknown-linux-musl.tar.gz": (
        "openshell",
        "078fa086f506832c3d47d992e6109f26074bdd55916ce268e47c3971423459eb",
    ),
    "openshell-gateway-x86_64-unknown-linux-gnu.tar.gz": (
        "openshell-gateway",
        "718cc9f942f88565cacb13c39717b128d6acc8d336212d42d26243f36ab19ece",
    ),
    "openshell-sandbox-x86_64-unknown-linux-gnu.tar.gz": (
        "openshell-sandbox",
        "94306f057d862cd5c34a0daa7692491733bc5ca528a7b92f9f62f717fb70a9be",
    ),
}
RUNTIME_ASSET_DIRECTORY = ".skill-eval-runtime-assets"
MIN_NODE_VERSION = (22, 19, 0)
MIN_NPM_MAJOR = 10
DEFAULT_TIMEOUT_SEC = 900
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_PINNED_ASSET_BYTES = 256 * 1024 * 1024
MAX_PINNED_BINARY_BYTES = 512 * 1024 * 1024


class PayloadBuildError(RuntimeError):
    """The exact release could not be prepared or verified."""


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        tail = stderr[-2000:].strip()
        detail = f": {tail}" if tail else ""
        raise PayloadBuildError(f"command failed: {args[0]}{detail}") from exc
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _runtime_manifest() -> dict[str, object]:
    return {
        "node": {
            "asset": {
                "member": NODE_ASSET_MEMBER,
                "name": NODE_ASSET_NAME,
                "sha256": NODE_ASSET_SHA256,
            },
            "version": NODE_VERSION,
        },
        "openshell": {
            "assets": [
                {"member": member, "name": name, "sha256": digest}
                for name, (member, digest) in sorted(OPENSHELL_ASSETS.items())
            ],
            "version": OPENSHELL_VERSION,
        },
    }


def _validate_single_binary_archive(
    archive: Path,
    *,
    expected_member: str,
    mode: str,
) -> None:
    try:
        size = archive.stat().st_size
        if size <= 0 or size > MAX_PINNED_ASSET_BYTES:
            raise PayloadBuildError("pinned runtime asset size is invalid")
        with tarfile.open(archive, mode) as payload:
            members = payload.getmembers()
            if len(members) != 1:
                raise PayloadBuildError("pinned runtime asset member count is invalid")
            member = members[0]
            if member.name != expected_member or not member.isfile():
                raise PayloadBuildError("pinned runtime asset member is invalid")
            if member.size <= 0 or member.size > MAX_PINNED_BINARY_BYTES:
                raise PayloadBuildError("pinned runtime binary size is invalid")
    except PayloadBuildError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PayloadBuildError("pinned runtime asset is unreadable") from exc


def _validate_node_archive(archive: Path) -> None:
    """Validate only the exact Node executable that the worker extracts.

    The official distribution contains npm/corepack symlinks.  The worker does
    not extract those paths, so requiring a one-member archive would reject the
    authentic release.  The outer pinned SHA authenticates the complete asset.
    """
    try:
        size = archive.stat().st_size
        if size <= 0 or size > MAX_PINNED_ASSET_BYTES:
            raise PayloadBuildError("pinned Node asset size is invalid")
        with tarfile.open(archive, "r:xz") as payload:
            members = payload.getmembers()
            matches = [member for member in members if member.name == NODE_ASSET_MEMBER]
            if len(matches) != 1 or not matches[0].isfile():
                raise PayloadBuildError("pinned Node executable member is invalid")
            if matches[0].size <= 0 or matches[0].size > MAX_PINNED_BINARY_BYTES:
                raise PayloadBuildError("pinned Node executable size is invalid")
            prefix = PurePosixPath(NODE_ASSET_MEMBER).parts[0]
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or not path.parts
                    or path.parts[0] != prefix
                    or any(part in ("", ".", "..") for part in path.parts)
                ):
                    raise PayloadBuildError("pinned Node asset path is unsafe")
    except PayloadBuildError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PayloadBuildError("pinned Node asset is unreadable") from exc


def _download_pinned_asset(
    *,
    url: str,
    destination: Path,
    expected_sha256: str,
    env: Mapping[str, str],
    timeout: int,
) -> None:
    temporary = destination.with_name(f".{destination.name}.download")
    temporary.unlink(missing_ok=True)
    try:
        _run(
            [
                "curl",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "5",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "--connect-timeout",
                "30",
                "--max-time",
                str(max(60, min(timeout, 300))),
                "--proto",
                "=https",
                "--tlsv1.2",
                "--output",
                str(temporary),
                url,
            ],
            env=env,
            timeout=timeout,
        )
        if _sha256(temporary) != expected_sha256:
            raise PayloadBuildError(f"pinned runtime asset checksum mismatch: {destination.name}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _download_runtime_assets(
    *,
    source: Path,
    env: Mapping[str, str],
    timeout: int,
) -> None:
    assets = source / RUNTIME_ASSET_DIRECTORY
    assets.mkdir(mode=0o700)
    node_archive = assets / NODE_ASSET_NAME
    _download_pinned_asset(
        url=NODE_ASSET_URL,
        destination=node_archive,
        expected_sha256=NODE_ASSET_SHA256,
        env=env,
        timeout=timeout,
    )
    _validate_node_archive(node_archive)
    for name, (member, digest) in sorted(OPENSHELL_ASSETS.items()):
        archive = assets / name
        _download_pinned_asset(
            url=f"{OPENSHELL_RELEASE_ROOT}/{name}",
            destination=archive,
            expected_sha256=digest,
            env=env,
            timeout=timeout,
        )
        _validate_single_binary_archive(
            archive,
            expected_member=member,
            mode="r:gz",
        )


def _validate_runtime(env: Mapping[str, str]) -> None:
    node_version = _version_tuple(_run(["node", "--version"], env=env, timeout=30))
    if node_version is None or node_version < MIN_NODE_VERSION:
        raise PayloadBuildError(
            "coordinator Node.js must be >=22.19.0 to build NemoClaw"
        )
    npm_text = _run(["npm", "--version"], env=env, timeout=30)
    npm_version = _version_tuple(npm_text)
    if npm_version is None or npm_version[0] < MIN_NPM_MAJOR:
        raise PayloadBuildError("coordinator npm major must be >=10")


def _validate_build_identity(path: Path, *, version: str, revision: str) -> None:
    try:
        identity = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PayloadBuildError("built NemoClaw identity is unreadable") from exc
    if identity != {"nemoclawVersion": version, "sourceRevision": revision}:
        raise PayloadBuildError("built NemoClaw identity does not match the source pin")


def _manifest(*, ref: str, version: str, revision: str) -> dict[str, object]:
    manifest = {
        "build": {
            "kind": "nemoclaw-source",
            "repository": SOURCE_URL,
            "revision": revision,
        },
        "payload_kind": PAYLOAD_KIND,
        "ref": ref,
        "revision": revision,
        "schema_version": SCHEMA_VERSION,
        "version": version,
    }
    manifest.update(_runtime_manifest())
    return manifest


def _validate_cached_archive(
    archive: Path,
    digest_file: Path,
    *,
    ref: str,
    version: str,
    revision: str,
) -> str | None:
    uid = os.getuid()
    try:
        archive_info = archive.lstat()
        digest_info = digest_file.lstat()
        if (
            not stat.S_ISREG(archive_info.st_mode)
            or not stat.S_ISREG(digest_info.st_mode)
            or archive_info.st_uid != uid
            or digest_info.st_uid != uid
            or archive_info.st_mode & 0o022
            or digest_info.st_mode & 0o022
            or archive_info.st_size <= 0
            or archive_info.st_size > MAX_ARCHIVE_BYTES
            or digest_info.st_size <= 0
            or digest_info.st_size > 128
        ):
            return None
        expected = digest_file.read_text(encoding="ascii").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return None
        if _sha256(archive) != expected:
            return None
        manifest_name = "source/.skill-eval-payload.json"
        required_files = {
            "source/package.json",
            "source/bin/nemoclaw.js",
            "source/dist/build-identity.json",
            "source/dist/lib/cli/logger.js",
            "source/nemoclaw/dist/index.js",
        }
        pinned_assets = {
            f"source/{RUNTIME_ASSET_DIRECTORY}/{NODE_ASSET_NAME}": NODE_ASSET_SHA256,
            **{
                f"source/{RUNTIME_ASSET_DIRECTORY}/{name}": digest
                for name, (_, digest) in OPENSHELL_ASSETS.items()
            },
        }
        with tarfile.open(archive, "r:gz") as payload:
            members = payload.getmembers()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                if member.name in by_name:
                    return None
                by_name[member.name] = member
                if member.name == "source/.git" or member.name.startswith("source/.git/"):
                    return None
            manifest_member = by_name.get(manifest_name)
            if manifest_member is None or not manifest_member.isfile():
                return None
            manifest_handle = payload.extractfile(manifest_member)
            if manifest_handle is None or manifest_member.size > 64 * 1024:
                return None
            manifest = json.loads(manifest_handle.read().decode("utf-8"))
            if manifest != _manifest(ref=ref, version=version, revision=revision):
                return None
            if any(
                name not in by_name or not by_name[name].isfile()
                for name in required_files
            ):
                return None
            package_handle = payload.extractfile(by_name["source/package.json"])
            if package_handle is None:
                return None
            package = json.loads(package_handle.read().decode("utf-8"))
            if package.get("name") != "nemoclaw":
                return None
            identity_handle = payload.extractfile(
                by_name["source/dist/build-identity.json"]
            )
            if identity_handle is None:
                return None
            identity = json.loads(identity_handle.read().decode("utf-8"))
            if identity != {"nemoclawVersion": version, "sourceRevision": revision}:
                return None
            for name, digest in pinned_assets.items():
                member = by_name.get(name)
                if member is None or not member.isfile():
                    return None
                handle = payload.extractfile(member)
                if handle is None or _sha256_stream(handle) != digest:
                    return None
    except (OSError, ValueError, UnicodeError, tarfile.TarError):
        return None
    return expected


def _build_archive(
    *,
    ref: str,
    version: str,
    revision: str,
    archive: Path,
    npm_cache: Path,
    timeout: int,
) -> str:
    with tempfile.TemporaryDirectory(
        prefix="nemoclaw-cli-build-",
        dir=archive.parent,
    ) as tmp:
        build_root = Path(tmp)
        source = build_root / "source"
        clean_env = {
            **os.environ,
            "CI": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "NEMOCLAW_INSTALLING": "1",
            "npm_config_audit": "false",
            "npm_config_cache": str(npm_cache),
            "npm_config_fetch_retries": "5",
            "npm_config_fetch_retry_factor": "2",
            "npm_config_fetch_retry_maxtimeout": "120000",
            "npm_config_fetch_retry_mintimeout": "10000",
            "npm_config_fetch_timeout": "300000",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
        }
        _validate_runtime(clean_env)
        _run(
            ["git", "-c", "init.templateDir=", "init", "--quiet", str(source)],
            env=clean_env,
            timeout=60,
        )
        _run(
            ["git", "-C", str(source), "remote", "add", "origin", SOURCE_URL],
            env=clean_env,
            timeout=30,
        )
        _run(
            [
                "git",
                "-C",
                str(source),
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "origin",
                f"+refs/tags/{ref}:refs/skill-eval/nemoclaw",
            ],
            env=clean_env,
            timeout=timeout,
        )
        _run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "advice.detachedHead=false",
                "checkout",
                "--quiet",
                "--detach",
                "refs/skill-eval/nemoclaw",
            ],
            env=clean_env,
            timeout=60,
        )
        actual_revision = _run(
            ["git", "-C", str(source), "rev-parse", "HEAD^{commit}"],
            env=clean_env,
            timeout=30,
        ).lower()
        if actual_revision != revision:
            raise PayloadBuildError(
                f"NemoClaw {ref} resolved to an unexpected source revision"
            )

        (source / ".version").write_text(version, encoding="ascii")
        # These are the same source-build phases used by the official v0.0.103
        # installer.  The install scripts are deliberately disabled here: the
        # coordinator produces code only and must not install OpenShell or
        # mutate its own global npm prefix.
        _run(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=source,
            env=clean_env,
            timeout=timeout,
        )
        _run(
            ["npm", "run", "build:cli"],
            cwd=source,
            env=clean_env,
            timeout=timeout,
        )
        plugin = source / "nemoclaw"
        _run(
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=plugin,
            env=clean_env,
            timeout=timeout,
        )
        _run(
            ["npm", "run", "build"],
            cwd=plugin,
            env=clean_env,
            timeout=timeout,
        )
        # Runtime dependencies travel with the payload; developer-only build
        # tools do not.  This materially reduces every Brev transfer.
        _run(
            [
                "npm",
                "prune",
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            cwd=source,
            env=clean_env,
            timeout=timeout,
        )
        _run(
            [
                "npm",
                "prune",
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            cwd=plugin,
            env=clean_env,
            timeout=timeout,
        )
        _run(
            [
                "git",
                "-C",
                str(source),
                "checkout",
                "--",
                "package-lock.json",
                "nemoclaw/package-lock.json",
            ],
            env=clean_env,
            timeout=30,
        )
        _run(
            ["git", "-C", str(source), "diff", "--quiet", "--ignore-submodules"],
            env=clean_env,
            timeout=30,
        )
        version_output = _run(
            ["node", str(source / "bin" / "nemoclaw.js"), "--version"],
            cwd=source,
            env=clean_env,
            timeout=60,
        )
        if version_output != f"nemoclaw v{version}":
            raise PayloadBuildError(
                "built NemoClaw CLI did not report the pinned release version"
            )
        _validate_build_identity(
            source / "dist" / "build-identity.json",
            version=version,
            revision=revision,
        )
        _download_runtime_assets(source=source, env=clean_env, timeout=timeout)
        for required in (
            source / "dist" / "lib" / "cli" / "logger.js",
            source / "dist" / "lib" / "onboard" / "preflight.js",
            source / "dist" / "lib" / "tunnel" / "gateway-port-release.js",
            plugin / "dist" / "index.js",
            source / "node_modules",
            plugin / "node_modules",
            source / RUNTIME_ASSET_DIRECTORY / NODE_ASSET_NAME,
            *(
                source / RUNTIME_ASSET_DIRECTORY / name
                for name in OPENSHELL_ASSETS
            ),
        ):
            if not required.exists():
                raise PayloadBuildError(
                    f"built NemoClaw payload is incomplete: {required.relative_to(source)}"
                )
        (source / ".skill-eval-payload.json").write_text(
            json.dumps(
                _manifest(ref=ref, version=version, revision=revision),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        # Git metadata is not needed at runtime: the payload manifest carries
        # the immutable source revision, while excluding .git avoids shipping
        # coordinator-local config/hooks and saves transfer time.
        with tarfile.open(archive, "w:gz", compresslevel=6) as output:
            output.add(source, arcname="source", filter=_archive_filter)
    if archive.stat().st_size <= 0 or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PayloadBuildError("NemoClaw CLI payload archive size is invalid")
    return _sha256(archive)


def _archive_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if info.name == "source/.git" or info.name.startswith("source/.git/"):
        return None
    # Normalize coordinator umask differences while retaining the one mode bit
    # that matters to the CLI payload: whether a regular file is executable.
    # The installed source lives below a 0700 state directory, so these modes
    # do not broaden access outside the worker account.
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    elif info.issym():
        info.mode = 0o777
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _ensure_secure_cache_directory(path: Path, *, mode: int = 0o700) -> None:
    absolute = Path(os.path.abspath(path))
    if absolute != absolute.resolve(strict=False):
        raise PayloadBuildError("NemoClaw coordinator cache path contains a symlink")
    absolute.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise PayloadBuildError("NemoClaw coordinator cache path is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PayloadBuildError("NemoClaw coordinator cache path is unsafe")
    absolute.chmod(mode)


def _open_cache_lock(path: Path):
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise PayloadBuildError("NemoClaw coordinator cache lock is unsafe")
        return os.fdopen(descriptor, "a+b")
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def prepare_payload(
    *,
    ref: str,
    output: Path,
    cache_root: Path,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> tuple[Path, str, str, str]:
    release = RELEASES.get(ref)
    if release is None:
        raise PayloadBuildError(f"unsupported NemoClaw payload ref: {ref}")
    version, revision = release
    cache_root = Path(os.path.abspath(cache_root))
    _ensure_secure_cache_directory(cache_root)
    key = f"nemoclaw-cli-schema{SCHEMA_VERSION}-{version}-{revision}"
    cached_archive = cache_root / f"{key}.tar.gz"
    digest_file = cache_root / f"{key}.sha256"
    lock_path = cache_root / f"{key}.lock"
    npm_cache = cache_root / "npm"
    _ensure_secure_cache_directory(npm_cache)

    with _open_cache_lock(lock_path) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        digest = _validate_cached_archive(
            cached_archive,
            digest_file,
            ref=ref,
            version=version,
            revision=revision,
        )
        if digest is None:
            temp_archive = cache_root / f".{key}.{os.getpid()}.tar.gz"
            temp_digest = cache_root / f".{key}.{os.getpid()}.sha256"
            try:
                digest = _build_archive(
                    ref=ref,
                    version=version,
                    revision=revision,
                    archive=temp_archive,
                    npm_cache=npm_cache,
                    timeout=timeout,
                )
                temp_digest.write_text(digest + "\n", encoding="ascii")
                os.replace(temp_archive, cached_archive)
                os.replace(temp_digest, digest_file)
            finally:
                temp_archive.unlink(missing_ok=True)
                temp_digest.unlink(missing_ok=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.resolve() != cached_archive.resolve():
        output_tmp = output.with_name(f".{output.name}.{os.getpid()}")
        try:
            shutil.copyfile(cached_archive, output_tmp)
            os.replace(output_tmp, output)
        finally:
            output_tmp.unlink(missing_ok=True)
    if _sha256(output) != digest:
        raise PayloadBuildError("copied NemoClaw CLI payload checksum mismatch")
    return output, digest, version, revision


def _append_github_env(
    path: Path,
    *,
    archive: Path,
    digest: str,
    version: str,
    revision: str,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"NEMOCLAW_COORDINATOR_CLI_ARCHIVE={archive.resolve()}\n")
        handle.write(f"NEMOCLAW_COORDINATOR_CLI_SHA256={digest}\n")
        handle.write(f"NEMOCLAW_COORDINATOR_CLI_VERSION={version}\n")
        handle.write(f"NEMOCLAW_COORDINATOR_CLI_REVISION={revision}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("/tmp/skill-eval/coordinator-cache/nemoclaw-cli"),
    )
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    args = parser.parse_args(argv)
    try:
        archive, digest, version, revision = prepare_payload(
            ref=args.ref,
            output=args.output,
            cache_root=args.cache_root,
            timeout=args.timeout,
        )
        if args.github_env is not None:
            _append_github_env(
                args.github_env,
                archive=archive,
                digest=digest,
                version=version,
                revision=revision,
            )
    except PayloadBuildError as exc:
        print(f"NemoClaw coordinator payload build failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "archive": str(archive.resolve()),
                "ref": args.ref,
                "revision": revision,
                "sha256": digest,
                "size_bytes": archive.stat().st_size,
                "version": version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
