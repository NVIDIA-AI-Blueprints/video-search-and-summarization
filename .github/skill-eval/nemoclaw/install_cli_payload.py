#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify and transactionally activate an offline NemoClaw runtime payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Callable, NamedTuple, Sequence


SCHEMA_VERSION = 3
SOURCE_URL = "https://github.com/NVIDIA/NemoClaw.git"
PAYLOAD_KIND = "nemoclaw-skill-eval-offline-runtime"
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 500_000
MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_PINNED_ASSET_BYTES = 256 * 1024 * 1024
MAX_PINNED_BINARY_BYTES = 512 * 1024 * 1024
RUNTIME_ASSET_DIRECTORY = ".skill-eval-runtime-assets"

NODE_VERSION = "22.22.1"
NODE_ASSET_NAME = f"node-v{NODE_VERSION}-linux-x64.tar.xz"
NODE_ASSET_MEMBER = f"node-v{NODE_VERSION}-linux-x64/bin/node"
NODE_ASSET_SHA256 = "9a6bc82f9b491279147219f6a18add1e18424dce90d41d2a5fcd69d4924ba3aa"
NODE_BINARY_SHA256 = "243fd8938011479f41b3de101842150fa990f33fbbb3f7aabd330857f2d79e1d"
NODE_RUNTIME_NAME = f"node-v{NODE_VERSION}-linux-x64"

OPENSHELL_VERSION = "0.0.85"
# Asset name -> (archive member, archive SHA-256, extracted binary SHA-256).
OPENSHELL_ASSETS: dict[str, tuple[str, str, str]] = {
    "openshell-x86_64-unknown-linux-musl.tar.gz": (
        "openshell",
        "078fa086f506832c3d47d992e6109f26074bdd55916ce268e47c3971423459eb",
        "222d9d53a142691d7a7de2c692f38e52d24066f9f633d53746c5fef775861bc8",
    ),
    "openshell-gateway-x86_64-unknown-linux-gnu.tar.gz": (
        "openshell-gateway",
        "718cc9f942f88565cacb13c39717b128d6acc8d336212d42d26243f36ab19ece",
        "33bb479d936c3c1b17dd475df05747be9de74564fb67d69a4c33cdd01181d02f",
    ),
    "openshell-sandbox-x86_64-unknown-linux-gnu.tar.gz": (
        "openshell-sandbox",
        "94306f057d862cd5c34a0daa7692491733bc5ca528a7b92f9f62f717fb70a9be",
        "863ef21ab7ef623f5e7a8728c4e5532b46bfbae3ace3b800665a1c6353a1f7d2",
    ),
}
CLI_SCRIPTS = {
    "nemoclaw": "nemoclaw.js",
    "nemohermes": "nemohermes.js",
    "nemo-deepagents": "nemoclaw.js",
}


class PayloadInstallError(RuntimeError):
    """The transferred payload or its worker destination is unsafe."""


class _Replacement(NamedTuple):
    destination: Path
    staged: Path
    allow_directory: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_and_hash(source, destination: Path) -> str:
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o755)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _require_regular_owned_file(path: Path, *, uid: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_archive_unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PayloadInstallError("payload_archive_unsafe")
    if info.st_uid != uid:
        raise PayloadInstallError("payload_archive_owner_mismatch")
    if info.st_size <= 0 or info.st_size > MAX_ARCHIVE_BYTES:
        raise PayloadInstallError("payload_archive_size_invalid")
    return info


def _ensure_owned_directory(path: Path, *, uid: int, mode: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=mode)
        info = path.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_destination_unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PayloadInstallError("payload_destination_unsafe")
    if info.st_uid != uid:
        raise PayloadInstallError("payload_destination_owner_mismatch")
    path.chmod(mode)


def _safe_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    if name.is_absolute() or not name.parts or name.parts[0] != "source":
        raise PayloadInstallError("payload_member_path_unsafe")
    if any(part in ("", ".", "..") for part in name.parts):
        raise PayloadInstallError("payload_member_path_unsafe")
    if member.isdev() or member.isfifo() or member.islnk():
        raise PayloadInstallError("payload_member_type_unsafe")
    if not (member.isdir() or member.isfile() or member.issym()):
        raise PayloadInstallError("payload_member_type_unsafe")
    if member.issym():
        target = PurePosixPath(member.linkname)
        if target.is_absolute():
            raise PayloadInstallError("payload_symlink_unsafe")
        resolved: list[str] = list(name.parent.parts)
        for part in target.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if len(resolved) <= 1:
                    raise PayloadInstallError("payload_symlink_unsafe")
                resolved.pop()
            else:
                resolved.append(part)
        if not resolved or resolved[0] != "source":
            raise PayloadInstallError("payload_symlink_unsafe")


def _extract_verified(archive: Path, destination: Path) -> Path:
    count = 0
    expanded = 0
    try:
        with tarfile.open(archive, "r:gz") as payload:
            members = payload.getmembers()
            for member in members:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise PayloadInstallError("payload_member_count_invalid")
                _safe_member(member)
                if member.isfile():
                    expanded += member.size
                    if expanded > MAX_EXPANDED_BYTES:
                        raise PayloadInstallError("payload_expanded_size_invalid")
            try:
                payload.extractall(destination, members=members, filter="fully_trusted")
            except TypeError:
                payload.extractall(destination, members=members)
    except PayloadInstallError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PayloadInstallError("payload_extract_failed") from exc
    source = destination / "source"
    try:
        info = source.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_source_missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PayloadInstallError("payload_source_unsafe")
    return source


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
                {"member": member, "name": name, "sha256": archive_sha}
                for name, (member, archive_sha, _) in sorted(OPENSHELL_ASSETS.items())
            ],
            "version": OPENSHELL_VERSION,
        },
    }


def _load_manifest(source: Path, *, ref: str, version: str, revision: str) -> None:
    manifest_path = source / ".skill-eval-payload.json"
    try:
        info = manifest_path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise PayloadInstallError("payload_manifest_unsafe")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except PayloadInstallError:
        raise
    except (OSError, ValueError) as exc:
        raise PayloadInstallError("payload_manifest_invalid") from exc
    expected = {
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
    expected.update(_runtime_manifest())
    if manifest != expected:
        raise PayloadInstallError("payload_manifest_mismatch")


def _runtime_asset(source: Path, name: str, expected_sha256: str) -> Path:
    root = source / RUNTIME_ASSET_DIRECTORY
    try:
        root_info = root.lstat()
        asset = root / name
        asset_info = asset.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_runtime_asset_missing") from exc
    if not stat.S_ISDIR(root_info.st_mode) or not stat.S_ISREG(asset_info.st_mode):
        raise PayloadInstallError("payload_runtime_asset_unsafe")
    if asset_info.st_size <= 0 or asset_info.st_size > MAX_PINNED_ASSET_BYTES:
        raise PayloadInstallError("payload_runtime_asset_size_invalid")
    if _sha256(asset) != expected_sha256:
        raise PayloadInstallError("payload_runtime_asset_checksum_mismatch")
    return asset


def _extract_exact_member(
    archive: Path,
    *,
    mode: str,
    expected_member: str,
    destination: Path,
    require_only_member: bool,
) -> str:
    try:
        with tarfile.open(archive, mode) as payload:
            members = payload.getmembers()
            matches = [member for member in members if member.name == expected_member]
            if len(matches) != 1 or not matches[0].isfile():
                raise PayloadInstallError("payload_runtime_asset_member_invalid")
            if require_only_member and len(members) != 1:
                raise PayloadInstallError("payload_runtime_asset_member_invalid")
            member = matches[0]
            if member.size <= 0 or member.size > MAX_PINNED_BINARY_BYTES:
                raise PayloadInstallError("payload_runtime_binary_size_invalid")
            handle = payload.extractfile(member)
            if handle is None:
                raise PayloadInstallError("payload_runtime_asset_member_invalid")
            return _copy_and_hash(handle, destination)
    except PayloadInstallError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PayloadInstallError("payload_runtime_asset_extract_failed") from exc


def _run_version(binary: Path, expected: str, *, label: str) -> None:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PayloadInstallError(f"payload_{label}_unrunnable") from exc
    combined = f"{result.stdout}\n{result.stderr}"
    if re.search(rf"(?<![0-9.]){re.escape(expected)}(?![0-9.])", combined) is None:
        raise PayloadInstallError(f"payload_{label}_version_mismatch")


def _validate_node(node: Path) -> None:
    try:
        info = node.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_node_unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise PayloadInstallError("payload_node_unsafe")
    if _sha256(node) != NODE_BINARY_SHA256:
        raise PayloadInstallError("payload_node_checksum_mismatch")
    try:
        result = subprocess.run(
            [str(node), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PayloadInstallError("payload_node_unavailable") from exc
    if result.stdout.strip() != f"v{NODE_VERSION}":
        raise PayloadInstallError("payload_node_version_mismatch")


def _validate_source(source: Path, *, node: Path, version: str, revision: str) -> None:
    required = (
        source / "package.json",
        source / "bin" / "nemoclaw.js",
        source / "bin" / "nemohermes.js",
        source / "dist" / "build-identity.json",
        source / "dist" / "lib" / "cli" / "logger.js",
        source / "dist" / "lib" / "onboard" / "preflight.js",
        source / "dist" / "lib" / "tunnel" / "gateway-port-release.js",
        source / "nemoclaw" / "dist" / "index.js",
    )
    for path in required:
        try:
            info = path.lstat()
        except OSError as exc:
            raise PayloadInstallError("payload_source_incomplete") from exc
        if not stat.S_ISREG(info.st_mode):
            raise PayloadInstallError("payload_source_incomplete")
    try:
        package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PayloadInstallError("payload_package_invalid") from exc
    if package.get("name") != "nemoclaw":
        raise PayloadInstallError("payload_package_invalid")
    try:
        identity = json.loads(
            (source / "dist" / "build-identity.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise PayloadInstallError("payload_build_identity_invalid") from exc
    if identity != {"nemoclawVersion": version, "sourceRevision": revision}:
        raise PayloadInstallError("payload_build_identity_mismatch")
    try:
        result = subprocess.run(
            [str(node), str(source / "bin" / "nemoclaw.js"), "--version"],
            cwd=source,
            env={
                **os.environ,
                "PATH": (
                    f"{node.parent}:/usr/local/sbin:/usr/local/bin:"
                    "/usr/sbin:/usr/bin:/sbin:/bin"
                ),
            },
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PayloadInstallError("payload_cli_unrunnable") from exc
    if result.stdout.strip() != f"nemoclaw v{version}":
        raise PayloadInstallError("payload_cli_version_mismatch")


def _remove_tree_or_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _stage_node(source: Path, runtime_root: Path, token: str) -> tuple[Path, Path]:
    archive = _runtime_asset(source, NODE_ASSET_NAME, NODE_ASSET_SHA256)
    stage = runtime_root / f".{NODE_RUNTIME_NAME}.stage-{token}"
    _remove_tree_or_link(stage)
    try:
        node = stage / "bin" / "node"
        node.parent.mkdir(parents=True, mode=0o755)
        binary_sha = _extract_exact_member(
            archive,
            mode="r:xz",
            expected_member=NODE_ASSET_MEMBER,
            destination=node,
            require_only_member=False,
        )
        if binary_sha != NODE_BINARY_SHA256:
            raise PayloadInstallError("payload_node_checksum_mismatch")
        _validate_node(node)
        return stage, node
    except BaseException:
        _remove_tree_or_link(stage)
        raise


def _stage_openshell(source: Path, bin_dir: Path, token: str) -> dict[str, Path]:
    staged: dict[str, Path] = {}
    try:
        for asset_name, (member, archive_sha, binary_sha) in sorted(OPENSHELL_ASSETS.items()):
            archive = _runtime_asset(source, asset_name, archive_sha)
            destination = bin_dir / f".{member}.stage-{token}"
            destination.unlink(missing_ok=True)
            staged[member] = destination
            actual_binary_sha = _extract_exact_member(
                archive,
                mode="r:gz",
                expected_member=member,
                destination=destination,
                require_only_member=True,
            )
            if actual_binary_sha != binary_sha:
                raise PayloadInstallError("payload_openshell_binary_checksum_mismatch")
        _run_version(staged["openshell"], OPENSHELL_VERSION, label="openshell")
        _run_version(
            staged["openshell-gateway"],
            OPENSHELL_VERSION,
            label="openshell_gateway",
        )
        # Some worker host glibc versions cannot execute the sandbox binary.
        # Its exact release archive and extracted binary hashes are pinned, and
        # NemoClaw v0.0.103 uses the same digest fallback in its feature gate.
        return staged
    except BaseException:
        for path in staged.values():
            path.unlink(missing_ok=True)
        raise


def _launcher_content(*, node: Path, script: Path, bin_dir: Path) -> str:
    safe_path = (
        f"{node.parent}:{bin_dir}:/usr/local/sbin:/usr/local/bin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    )
    openshell = bin_dir / "openshell"
    gateway = bin_dir / "openshell-gateway"
    sandbox = bin_dir / "openshell-sandbox"
    return (
        "#!/bin/sh\n"
        f"export PATH={shlex.quote(safe_path)}\n"
        f"export NEMOCLAW_OPENSHELL_BIN={shlex.quote(str(openshell))}\n"
        f"export NEMOCLAW_OPENSHELL_GATEWAY_BIN={shlex.quote(str(gateway))}\n"
        f"export NEMOCLAW_OPENSHELL_SANDBOX_BIN={shlex.quote(str(sandbox))}\n"
        f"export OPENSHELL_DOCKER_SUPERVISOR_BIN={shlex.quote(str(sandbox))}\n"
        f"exec {shlex.quote(str(node))} {shlex.quote(str(script))} \"$@\"\n"
    )


def _stage_launchers(
    *,
    bin_dir: Path,
    node: Path,
    source: Path,
    token: str,
) -> tuple[dict[str, Path], dict[str, str]]:
    staged: dict[str, Path] = {}
    content: dict[str, str] = {}
    try:
        for name, target in CLI_SCRIPTS.items():
            destination = bin_dir / f".{name}.stage-{token}"
            destination.unlink(missing_ok=True)
            staged[name] = destination
            body = _launcher_content(
                node=node,
                script=source / "bin" / target,
                bin_dir=bin_dir,
            )
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            destination.chmod(0o755)
            content[name] = body
        return staged, content
    except BaseException:
        for path in staged.values():
            path.unlink(missing_ok=True)
        raise


def _validate_installed_runtime(
    *,
    source: Path,
    node: Path,
    bin_dir: Path,
    version: str,
    revision: str,
    launcher_content: dict[str, str],
) -> None:
    _validate_node(node)
    _validate_source(source, node=node, version=version, revision=revision)
    for name, expected in launcher_content.items():
        path = bin_dir / name
        try:
            info = path.lstat()
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PayloadInstallError("payload_launcher_unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o755 or body != expected:
            raise PayloadInstallError("payload_launcher_mismatch")
    try:
        result = subprocess.run(
            [str(bin_dir / "nemoclaw"), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PayloadInstallError("payload_launcher_unrunnable") from exc
    if result.stdout.strip() != f"nemoclaw v{version}":
        raise PayloadInstallError("payload_launcher_version_mismatch")
    for _, (member, _, binary_sha) in OPENSHELL_ASSETS.items():
        path = bin_dir / member
        try:
            info = path.lstat()
        except OSError as exc:
            raise PayloadInstallError("payload_openshell_unavailable") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o777 != 0o755
            or _sha256(path) != binary_sha
        ):
            raise PayloadInstallError("payload_openshell_mismatch")
    _run_version(bin_dir / "openshell", OPENSHELL_VERSION, label="openshell")
    _run_version(
        bin_dir / "openshell-gateway",
        OPENSHELL_VERSION,
        label="openshell_gateway",
    )


def _transactional_replace(
    replacements: Sequence[_Replacement],
    *,
    token: str,
    verify: Callable[[], None],
) -> None:
    if len({item.destination for item in replacements}) != len(replacements):
        raise PayloadInstallError("payload_transaction_duplicate_destination")
    prepared: list[tuple[_Replacement, Path, bool]] = []
    for item in replacements:
        try:
            staged_info = item.staged.lstat()
            parent_info = item.destination.parent.lstat()
        except OSError as exc:
            raise PayloadInstallError("payload_transaction_stage_unavailable") from exc
        if staged_info.st_dev != parent_info.st_dev:
            raise PayloadInstallError("payload_transaction_cross_device")
        if item.allow_directory:
            if not stat.S_ISDIR(staged_info.st_mode):
                raise PayloadInstallError("payload_transaction_stage_unsafe")
        elif not stat.S_ISREG(staged_info.st_mode):
            raise PayloadInstallError("payload_transaction_stage_unsafe")
        try:
            destination_info = item.destination.lstat()
            had_destination = True
            if not item.allow_directory and stat.S_ISDIR(destination_info.st_mode):
                raise PayloadInstallError("payload_transaction_destination_unsafe")
        except FileNotFoundError:
            had_destination = False
        backup = item.destination.parent / f".{item.destination.name}.backup-{token}"
        if os.path.lexists(backup):
            raise PayloadInstallError("payload_transaction_backup_collision")
        prepared.append((item, backup, had_destination))

    applied: list[tuple[_Replacement, Path, bool, bool]] = []
    try:
        for item, backup, had_destination in prepared:
            if had_destination:
                os.replace(item.destination, backup)
            applied.append((item, backup, had_destination, False))
            os.replace(item.staged, item.destination)
            applied[-1] = (item, backup, had_destination, True)
        verify()
    except BaseException as original:
        rollback_errors: list[BaseException] = []
        for item, backup, had_destination, installed in reversed(applied):
            try:
                if installed and os.path.lexists(item.destination):
                    _remove_tree_or_link(item.destination)
                if had_destination and os.path.lexists(backup):
                    os.replace(backup, item.destination)
            except BaseException as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            raise PayloadInstallError("payload_transaction_rollback_failed") from original
        raise
    for _, backup, _, _ in applied:
        _remove_tree_or_link(backup)


def install_payload(
    *,
    archive: Path,
    sha256: str,
    ref: str,
    version: str,
    revision: str,
    home: Path,
) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PayloadInstallError("payload_checksum_invalid")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", ref):
        raise PayloadInstallError("payload_ref_invalid")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PayloadInstallError("payload_version_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PayloadInstallError("payload_revision_invalid")
    if ref != f"v{version}":
        raise PayloadInstallError("payload_ref_version_mismatch")

    uid = os.getuid()
    if not home.is_absolute():
        raise PayloadInstallError("payload_home_unsafe")
    try:
        home_info = home.lstat()
    except OSError as exc:
        raise PayloadInstallError("payload_home_unavailable") from exc
    if not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != uid:
        raise PayloadInstallError("payload_home_unsafe")
    _require_regular_owned_file(archive, uid=uid)
    if _sha256(archive) != sha256:
        raise PayloadInstallError("payload_checksum_mismatch")

    state = home / ".nemoclaw"
    _ensure_owned_directory(state, uid=uid, mode=0o700)
    runtime_root = state / "runtime"
    _ensure_owned_directory(runtime_root, uid=uid, mode=0o700)
    local = home / ".local"
    _ensure_owned_directory(local, uid=uid, mode=0o755)
    bin_dir = local / "bin"
    _ensure_owned_directory(bin_dir, uid=uid, mode=0o755)

    source_destination = state / "source"
    node_destination_root = runtime_root / NODE_RUNTIME_NAME
    node_destination = node_destination_root / "bin" / "node"
    token = f"{os.getpid()}-{secrets.token_hex(8)}"
    cleanup: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".payload-stage-", dir=state) as tmp:
        candidate = _extract_verified(archive, Path(tmp))
        try:
            _load_manifest(candidate, ref=ref, version=version, revision=revision)
            node_stage_root, node_stage = _stage_node(candidate, runtime_root, token)
            cleanup.append(node_stage_root)
            _validate_source(
                candidate,
                node=node_stage,
                version=version,
                revision=revision,
            )
            openshell_staged = _stage_openshell(candidate, bin_dir, token)
            cleanup.extend(openshell_staged.values())
            launcher_staged, launcher_content = _stage_launchers(
                bin_dir=bin_dir,
                node=node_destination,
                source=source_destination,
                token=token,
            )
            cleanup.extend(launcher_staged.values())
            replacements = [
                _Replacement(source_destination, candidate, True),
                _Replacement(node_destination_root, node_stage_root, True),
                *(
                    _Replacement(bin_dir / name, staged, False)
                    for name, staged in launcher_staged.items()
                ),
                *(
                    _Replacement(bin_dir / name, staged, False)
                    for name, staged in openshell_staged.items()
                ),
            ]
            _transactional_replace(
                replacements,
                token=token,
                verify=lambda: _validate_installed_runtime(
                    source=source_destination,
                    node=node_destination,
                    bin_dir=bin_dir,
                    version=version,
                    revision=revision,
                    launcher_content=launcher_content,
                ),
            )
        finally:
            for path in cleanup:
                _remove_tree_or_link(path)
    return source_destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        destination = install_payload(
            archive=args.archive,
            sha256=args.sha256,
            ref=args.ref,
            version=args.version,
            revision=args.revision,
            home=args.home,
        )
    except PayloadInstallError as exc:
        print(f"NemoClaw runtime payload activation refused: {exc}", file=os.sys.stderr)
        return 1
    print(f"Activated offline NemoClaw {args.version} runtime at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
