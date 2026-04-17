# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Storage helpers for dev-profile directory bootstrapping."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final
from typing import Iterable


DEFAULT_PERMISSION_RELATIVE_ROOTS: Final[tuple[str, ...]] = ("data_log", "agent_eval", "models")
DEFAULT_PERMISSION_MODE: Final[int] = 0o777

NGC_ENV_API_KEY: Final[str] = "NGC_CLI_API_KEY"
NGC_DOWNLOAD_TIMEOUT_S: Final[int] = 1800
NGC_TMP_DIR_PREFIX: Final[str] = "vss-search-models-"


class ArtifactKind(str, Enum):
    FILE = "file"
    DIRECTORY = "dir"


@dataclass(frozen=True)
class ModelArtifact:
    package_ref: str
    downloaded_relative_path: str
    output_name: str
    artifact_kind: ArtifactKind


def resolve_required_absolute_file(
    path_value: str,
    *,
    field_name: str,
    missing_label: str,
    error_type: type[Exception] = RuntimeError,
) -> Path:
    """Resolve and validate a required absolute file path."""

    normalized = path_value.strip()
    if not normalized:
        raise error_type(f"{field_name} must not be empty.")

    resolved_path = Path(normalized).expanduser()
    if not resolved_path.is_absolute():
        raise error_type(f"{field_name} must be an absolute path.")

    resolved_path = resolved_path.resolve()
    if not resolved_path.is_file():
        raise error_type(f"{missing_label} not found: {resolved_path}")
    return resolved_path


def _artifact_is_valid(path: Path, kind: ArtifactKind) -> bool:
    if kind == ArtifactKind.FILE:
        return path.is_file()
    if kind == ArtifactKind.DIRECTORY:
        return path.is_dir()
    return False


def ensure_model_artifacts(
    data_directory: str | Path,
    ngc_cli_api_key: str,
    *,
    artifacts: tuple[ModelArtifact, ...],
) -> None:
    """Ensure required profile model artifacts exist under ``<data_directory>/models``."""

    mdx_data_dir = Path(data_directory).expanduser().resolve()
    models_dir = mdx_data_dir / "models"
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(f"Permission denied creating model directory: {models_dir}") from exc

    missing_artifacts = [
        artifact
        for artifact in artifacts
        if not _artifact_is_valid(models_dir / artifact.output_name, artifact.artifact_kind)
    ]
    if not missing_artifacts:
        print("=== Model Artifacts ===")
        print("Required model artifacts are already present.")
        print()
        return

    if not ngc_cli_api_key:
        missing_names = ", ".join(sorted({artifact.output_name for artifact in missing_artifacts}))
        raise RuntimeError(
            "Profile requires model artifacts but some are missing: "
            f"{missing_names}. Provide NGC_CLI_API_KEY to auto-download them."
        )

    print("=== Model Artifacts ===")
    print("Downloading missing model artifacts from NGC...")
    print()

    ngc_env = os.environ.copy()
    ngc_env[NGC_ENV_API_KEY] = ngc_cli_api_key

    with tempfile.TemporaryDirectory(prefix=NGC_TMP_DIR_PREFIX) as tmp_dir:
        tmp_path = Path(tmp_dir)
        for package_ref in sorted({artifact.package_ref for artifact in missing_artifacts}):
            try:
                result = subprocess.run(
                    ["ngc", "registry", "model", "download-version", package_ref],
                    capture_output=True,
                    text=True,
                    timeout=NGC_DOWNLOAD_TIMEOUT_S,
                    cwd=str(tmp_path),
                    env=ngc_env,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"Failed to download required model package: {package_ref}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Failed to download required model package: {package_ref} "
                    f"(timed out after {NGC_DOWNLOAD_TIMEOUT_S}s)"
                ) from exc
            if result.returncode != 0:
                details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                raise RuntimeError(f"Failed to download required model package: {package_ref}\n{details}")

        for artifact in missing_artifacts:
            source_path = tmp_path / artifact.downloaded_relative_path
            if not _artifact_is_valid(source_path, artifact.artifact_kind):
                raise RuntimeError(f"Downloaded model artifact is missing or invalid: {source_path}")

            destination_path = models_dir / artifact.output_name
            # output_name may include nested directories (e.g. rtdetr-its/model.onnx),
            # so ensure parent directories exist before moving downloaded artifacts.
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if destination_path.exists():
                try:
                    if destination_path.is_dir():
                        shutil.rmtree(destination_path)
                    else:
                        destination_path.unlink()
                except PermissionError as exc:
                    raise RuntimeError(
                        f"Permission denied while replacing model artifact: {destination_path}"
                    ) from exc

            try:
                shutil.move(str(source_path), str(destination_path))
            except PermissionError as exc:
                raise RuntimeError(f"Permission denied writing model artifact: {destination_path}") from exc

    print(f"Model artifacts are ready at: {models_dir}")
    print()


def ensure_required_directories(
    data_directory: str | Path,
    *,
    relative_paths: Iterable[str],
) -> list[Path]:
    """Create required directories under ``data_directory`` and return paths."""

    root = Path(data_directory).expanduser().resolve()
    created_paths: list[Path] = []
    for relative_path in relative_paths:
        full_path = root / relative_path
        try:
            full_path.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise RuntimeError(
                f"Permission denied creating required directory: {full_path}. "
                f"Check ownership/permissions under data root: {root}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"Failed creating required directory: {full_path}. {exc}") from exc

        if not full_path.is_dir():
            raise RuntimeError(f"Required path is not a directory after creation attempt: {full_path}")

        # Even when the directory already exists, fail early if the current user
        # cannot traverse/write it; compose bind mounts will fail later otherwise.
        if not os.access(full_path, os.X_OK | os.W_OK):
            raise RuntimeError(
                f"Required directory is not writable by current user: {full_path}. "
                f"Check ownership/permissions under data root: {root}"
            )
        created_paths.append(full_path)
    return created_paths


def ensure_permissions(
    data_directory: str | Path,
    *,
    relative_roots: Iterable[str] = DEFAULT_PERMISSION_RELATIVE_ROOTS,
    mode: int = DEFAULT_PERMISSION_MODE,
    best_effort: bool = True,
) -> list[Path]:
    """Recursively apply permissions under selected roots."""

    def chmod_if_allowed(path: Path) -> bool:
        try:
            os.chmod(path, mode)
            return True
        except PermissionError:
            if not best_effort:
                raise
            return False

    root = Path(data_directory).expanduser().resolve()
    touched_paths: list[Path] = []
    for relative_root in relative_roots:
        full_root = root / relative_root
        if not full_root.exists():
            continue
        if chmod_if_allowed(full_root):
            touched_paths.append(full_root)
        for dirpath, dirnames, filenames in os.walk(full_root):
            dir_path = Path(dirpath)
            if chmod_if_allowed(dir_path):
                touched_paths.append(dir_path)
            for dirname in dirnames:
                child = dir_path / dirname
                if chmod_if_allowed(child):
                    touched_paths.append(child)
            for filename in filenames:
                child = dir_path / filename
                if chmod_if_allowed(child):
                    touched_paths.append(child)
    return touched_paths


def ensure_data_directories(
    data_directory: str | Path,
    *,
    required_subdirectories: tuple[str, ...],
) -> list[Path]:
    """Create base-profile directories and enforce writable permissions."""

    created_paths = ensure_required_directories(
        data_directory,
        relative_paths=required_subdirectories,
    )
    ensure_permissions(data_directory, best_effort=True)
    return created_paths
