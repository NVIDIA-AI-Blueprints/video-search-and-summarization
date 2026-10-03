####################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
####################################################################################################

"""Apply the pinned DL Algo Streaming VLM delta to RTVI's selected vLLM.

The payload intentionally excludes the DL Algo REST server. RTVI calls
``StreamingSession.push_frames(decoded_frames)`` in-process; HTTP remains
outside the production frame path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

PATCH_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PATCH_ROOT / "dlalgo_streaming"
MANIFEST_PATH = PAYLOAD_ROOT / "manifest.json"
DEFAULT_VLLM_ROOT = Path("/usr/local/lib/python3.12/dist-packages/vllm")
MARKER_NAME = ".rtvi-dlalgo-streaming.json"
EVS_MERGED_PATHS = (
    "vllm/v1/core/sched/scheduler.py",
    "vllm/v1/request.py",
    "vllm/v1/core/encoder_cache_manager.py",
    "vllm/v1/worker/gpu_model_runner.py",
)
EVS_MERGED_SYMBOLS = {
    "v1/core/sched/scheduler.py": "def _refresh_streaming_structured_output(",
    "v1/request.py": "self.streaming_retention: StreamingRetentionParams | None",
    "v1/core/encoder_cache_manager.py": "def evict_unreferenced(",
    "v1/worker/gpu_model_runner.py": "def _apply_streaming",
}


class PatchCompatibilityError(RuntimeError):
    """The installed vLLM source does not match the pinned patch context."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as source:
        manifest = json.load(source)
    patch_path = PAYLOAD_ROOT / manifest["patch_file"]
    actual = _sha256(patch_path)
    expected = manifest["patch_sha256"]
    if actual != expected:
        raise PatchCompatibilityError(
            f"DL Algo patch checksum mismatch: expected {expected}, got {actual}"
        )
    for overlay in manifest.get("overlay_patches", []):
        overlay_path = PAYLOAD_ROOT / overlay["patch_file"]
        overlay_actual = _sha256(overlay_path)
        overlay_expected = overlay["patch_sha256"]
        if overlay_actual != overlay_expected:
            raise PatchCompatibilityError(
                "DL Algo overlay checksum mismatch for "
                f"{overlay['patch_file']}: expected {overlay_expected}, "
                f"got {overlay_actual}"
            )
    for rel, expected in manifest.get("evs_merged_files", {}).items():
        actual = _sha256(PAYLOAD_ROOT / "evs_merged_files" / "vllm" / rel)
        if actual != expected:
            raise PatchCompatibilityError(
                f"DL Algo EVS merge checksum mismatch for {rel}: expected {expected}, got {actual}"
            )
    if manifest.get("rest_api_included") is not False:
        raise PatchCompatibilityError("native DL Algo payload must exclude its REST API")
    return manifest


def _resolve_vllm_root(value: str | os.PathLike[str] | None = None) -> Path:
    configured = value or os.environ.get("VLLM_ROOT")
    root = Path(configured) if configured else DEFAULT_VLLM_ROOT
    return root.resolve()


def _validate_vllm_root(vllm_root: Path) -> None:
    required = (
        vllm_root / "__init__.py",
        vllm_root / "v1" / "request.py",
        vllm_root / "v1" / "core" / "sched" / "scheduler.py",
        vllm_root / "v1" / "worker" / "gpu_model_runner.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PatchCompatibilityError(
            "selected VLLM_ROOT is incomplete; missing " + ", ".join(missing)
        )
    for rel, symbol in EVS_MERGED_SYMBOLS.items():
        if symbol not in (vllm_root / rel).read_text(encoding="utf-8"):
            raise PatchCompatibilityError(f"native EVS merge is missing from {rel}")


def _run_patch(
    patch_path: Path,
    vllm_root: Path,
    *,
    dry_run: bool,
    recount: bool = True,
) -> None:
    command = ["git", "apply", "--unsafe-paths"]
    command.extend(f"--exclude={path}" for path in EVS_MERGED_PATHS)
    if recount:
        command.append("--recount")
    if dry_run:
        command.append("--check")
    command.append(str(patch_path))
    result = subprocess.run(
        command,
        cwd=vllm_root.parent,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise PatchCompatibilityError(
            "DL Algo source delta is incompatible with the selected vLLM package"
            + (f": {detail}" if detail else "")
        )


def apply_patch(vllm_root: str | os.PathLike[str] | None = None) -> bool:
    """Apply the native delta once; return whether files were modified."""
    manifest = _load_manifest()
    root = _resolve_vllm_root(vllm_root)
    _validate_vllm_root(root)
    marker_path = root / MARKER_NAME

    if marker_path.is_file():
        with marker_path.open(encoding="utf-8") as source:
            installed = json.load(source)
        if installed == manifest:
            print("DL Algo Streaming VLM source delta already applied.")
            return False
        raise PatchCompatibilityError(
            "a different DL Algo Streaming VLM patch is already installed"
        )

    patch_path = PAYLOAD_ROOT / manifest["patch_file"]
    _run_patch(patch_path, root, dry_run=True)
    _run_patch(patch_path, root, dry_run=False)
    for overlay in manifest.get("overlay_patches", []):
        overlay_path = PAYLOAD_ROOT / overlay["patch_file"]
        _run_patch(overlay_path, root, dry_run=True)
        _run_patch(overlay_path, root, dry_run=False)
    marker_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Applied DL Algo Streaming VLM source delta " f"{manifest['source_commit']} to {root}.")
    return True


if __name__ == "__main__":
    apply_patch()
