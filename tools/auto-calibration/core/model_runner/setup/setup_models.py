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

"""Automated setup: reads models_manifest.yaml, clones repos, downloads weights, installs deps.

Usage:
    python -m core.model_runner.setup.setup_models [--manifest PATH] [--models geocalib moge vggt] [--skip-models vggt] [--skip-deps]
"""
import argparse
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _prefer_ipv4() -> None:
    """Monkey-patch socket.getaddrinfo to prefer IPv4 over IPv6.

    Some networks resolve IPv6 AAAA records for hosts like huggingface.co
    but cannot actually route IPv6 traffic, causing downloads to hang
    indefinitely.  This makes all DNS lookups in this process return IPv4
    results first, matching the behavior of ``wget -4``.
    """
    _orig = socket.getaddrinfo

    def _ipv4_first(*args, **kwargs):
        results = _orig(*args, **kwargs)
        v4 = [r for r in results if r[0] == socket.AF_INET]
        return v4 if v4 else results

    socket.getaddrinfo = _ipv4_first

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "configs" / "config_AutoMagicCalib"
_DEFAULT_MANIFEST = _CONFIG_DIR / "models_manifest.yaml"


def _detect_project_root(manifest_path: Path) -> Path:
    return manifest_path.parent.parent.parent


def load_manifest(manifest_path: Path) -> dict:
    with open(manifest_path) as f:
        raw = yaml.safe_load(f)
    return raw.get("models", {})


def _is_valid_repo(dest: Path) -> bool:
    """Recognize supported source checkouts independently of Git metadata."""
    if not dest.is_dir():
        return False
    # These are the implementation modules imported by the model adapters.
    return any((dest / source).is_file() for source in (
        "geocalib/extractor.py", "vggt/models/vggt.py", "moge/model/v2.py",
    ))


def clone_repo(url: str, dest: Path) -> None:
    if _is_valid_repo(dest):
        logger.info(f"  Repo already exists at {dest}, skipping clone")
        return
    if dest.is_symlink() or (
        dest.exists() and (not dest.is_dir() or any(dest.iterdir()))
    ):
        raise RuntimeError(
            f"Refusing to replace existing checkout or non-empty path: {dest}. "
            "Initialize or repair the model sources explicitly."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"  Cloning {url} -> {dest}")
    subprocess.check_call(["git", "clone", url, str(dest)])


class DownloadError(RuntimeError):
    """Raised when a weight file cannot be downloaded (auth, network, etc.)."""


def download_file(url: str, dest: Path) -> None:
    if dest.exists():
        logger.info(f"  {dest.name} already exists, skipping")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"  Downloading {url}")
    logger.info(f"    -> {dest}")

    try:
        try:
            subprocess.check_call([
                "wget", "-4", "--progress=bar:force:noscroll",
                "--timeout=30", "--tries=3",
                "-O", str(dest), url,
            ])
        except FileNotFoundError:
            try:
                subprocess.check_call([
                    "curl", "-L", "--progress-bar",
                    "--retry", "3", "--retry-delay", "5",
                    "-o", str(dest), url,
                ])
            except FileNotFoundError:
                import urllib.request
                logger.info("  (wget/curl not found, using urllib — no progress bar)")
                urllib.request.urlretrieve(url, str(dest))
    except Exception:
        if dest.exists():
            dest.unlink()
        raise DownloadError(url)


def install_deps(repo_path: Path, model_id: str) -> None:
    pyproject = repo_path / "pyproject.toml"
    setup_py = repo_path / "setup.py"
    requirements = repo_path / "requirements.txt"

    if pyproject.exists() or setup_py.exists():
        logger.info(f"  Installing {model_id} as editable package")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(repo_path)])
    elif requirements.exists():
        logger.info(f"  Installing {model_id} requirements")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(requirements)])
    else:
        logger.info(f"  No installable package or requirements.txt found for {model_id}")


def download_hf_snapshot(pretrained_name: str, local_dir: Path) -> None:
    """Download full HF model snapshot (config + weights) into a local directory."""
    try:
        from huggingface_hub import snapshot_download
        local_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Downloading HF snapshot: {pretrained_name} -> {local_dir}")
        snapshot_download(repo_id=pretrained_name, local_dir=str(local_dir))
        logger.info(f"  HF snapshot complete: {local_dir}")
    except Exception as e:
        logger.warning(f"  Could not download HF snapshot for {pretrained_name}: {e}")


def _is_fully_set_up(entry: dict, project_root: Path) -> bool:
    """Return True if repo exists and all weight files are present."""
    repo_cfg = entry.get("repo", {})
    clone_to = repo_cfg.get("clone_to")
    if clone_to and not _is_valid_repo(project_root / clone_to):
        return False
    weights_cfg = entry.get("weights", {})
    local_dir = weights_cfg.get("local_dir", "")
    for wf in weights_cfg.get("files", []):
        if not (project_root / local_dir / wf["name"]).exists():
            return False
    pretrained_name = entry.get("pretrained_name")
    if pretrained_name:
        hf_dir = project_root / weights_cfg.get("local_dir", "")
        if not (hf_dir / "model.pt").exists():
            return False
    return True


def setup_model(model_id: str, entry: dict, project_root: Path, skip_deps: bool) -> None:
    if _is_fully_set_up(entry, project_root):
        logger.info(f"[{model_id}] Already fully set up (repo + weights present), skipping")
        return

    logger.info(f"\n{'='*50}")
    logger.info(f"Setting up: {model_id}")
    logger.info(f"{'='*50}")

    repo_cfg = entry.get("repo", {})
    repo_url = repo_cfg.get("url")
    clone_to = repo_cfg.get("clone_to")
    if repo_url and clone_to:
        clone_repo(repo_url, project_root / clone_to)

    weights_cfg = entry.get("weights", {})
    local_dir = weights_cfg.get("local_dir", "")
    for wf in weights_cfg.get("files", []):
        name = wf["name"]
        url = wf.get("url", "")
        if url:
            try:
                download_file(url, project_root / local_dir / name)
            except DownloadError:
                dest = project_root / local_dir / name
                logger.warning(
                    f"\n{'!'*60}\n"
                    f"  [{model_id}] Could not download weight file.\n"
                    f"  The URL may require authentication (e.g. HuggingFace login).\n"
                    f"\n"
                    f"  Download manually from:\n"
                    f"    {url}\n"
                    f"\n"
                    f"  Then place the file at:\n"
                    f"    {dest}\n"
                    f"\n"
                    f"  For Docker, mount the file and use:\n"
                    f"    --{model_id}-model-path <path>\n"
                    f"{'!'*60}"
                )
                continue

    pretrained_name = entry.get("pretrained_name")
    if pretrained_name:
        hf_local_dir = project_root / weights_cfg.get("local_dir", f"models/{model_id}")
        download_hf_snapshot(pretrained_name, hf_local_dir)

    if not skip_deps and clone_to:
        install_deps(project_root / clone_to, model_id)


def main(argv=None):
    p = argparse.ArgumentParser(description="FL Model Runner -- Setup models from manifest")
    p.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    p.add_argument("--project-root", type=Path, default=None)
    p.add_argument("--models", nargs="*", default=None,
                   help="Specific model IDs to set up (default: all enabled)")
    p.add_argument("--skip-models", nargs="*", default=None,
                   help="Model IDs to exclude from setup")
    p.add_argument("--skip-deps", action="store_true",
                   help="Skip pip install step")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s - %(message)s",
    )

    _prefer_ipv4()

    project_root = args.project_root or _detect_project_root(args.manifest)
    manifest = load_manifest(args.manifest)

    targets = args.models or [mid for mid, cfg in manifest.items() if cfg.get("enabled", False)]
    if args.skip_models:
        targets = [m for m in targets if m not in args.skip_models]

    logger.info(f"Project root: {project_root}")
    logger.info(f"Models to set up: {targets}")

    for model_id in targets:
        if model_id not in manifest:
            logger.error(f"Model '{model_id}' not found in manifest, skipping")
            continue
        setup_model(model_id, manifest[model_id], project_root, args.skip_deps)

    logger.info(f"\nSetup complete for: {targets}")


if __name__ == "__main__":
    main()
