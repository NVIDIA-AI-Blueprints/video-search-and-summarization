#!/usr/bin/env python3
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
"""
Verify that the FFmpeg source tarball exists and is a valid gzip file.

This catches two failure modes:
1. Tarball missing entirely (file not present)
2. LFS pointer file instead of actual tarball (git-lfs fetch failed)

Usage:
    python3 docker/verify_ffmpeg_tarball.py [--path DIR]

Exit codes:
    0 - Tarball found and valid
    1 - Tarball missing or invalid
"""

import argparse
import gzip
from pathlib import Path
import sys

DEFAULT_PATH = "3rdparty/ffmpeg"
DOCKER_PATH = "/vss-agent/third_party/ffmpeg"


def find_tarball(search_dir: Path) -> Path | None:
    """Find FFmpeg tarball in the given directory."""
    candidates = list(search_dir.glob("FFmpeg-*.tar.gz"))
    return candidates[0] if candidates else None


def is_valid_gzip(filepath: Path) -> bool:
    """Check if file is a valid gzip file (not an LFS pointer)."""
    try:
        with gzip.open(filepath, "rb") as f:
            # Read first few bytes to verify it's valid gzip
            f.read(1024)
        return True
    except (gzip.BadGzipFile, OSError):
        return False


def get_file_info(filepath: Path) -> str:
    """Get human-readable file info."""
    if not filepath.exists():
        return "does not exist"
    size = filepath.stat().st_size
    if size < 1024:
        return f"{size} bytes (likely LFS pointer)"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify FFmpeg source tarball.")
    parser.add_argument(
        "--path",
        default=None,
        help=f"Directory containing FFmpeg tarball (default: {DEFAULT_PATH} or {DOCKER_PATH})",
    )
    args = parser.parse_args()

    # Auto-detect path: use Docker path if it exists, otherwise default
    if args.path:
        search_dir = Path(args.path)
    elif Path(DOCKER_PATH).exists():
        search_dir = Path(DOCKER_PATH)
    else:
        search_dir = Path(DEFAULT_PATH)

    print(f"[ffmpeg-tarball] Checking directory: {search_dir}")

    if not search_dir.exists():
        print(f"[ffmpeg-tarball] ERROR: Directory does not exist: {search_dir}")
        return 1

    tarball = find_tarball(search_dir)
    if not tarball:
        print(f"[ffmpeg-tarball] ERROR: No FFmpeg-*.tar.gz found in {search_dir}")
        print(f"[ffmpeg-tarball] Contents: {list(search_dir.iterdir())}")
        return 1

    file_info = get_file_info(tarball)
    print(f"[ffmpeg-tarball] Found: {tarball.name} ({file_info})")

    if not is_valid_gzip(tarball):
        print(f"[ffmpeg-tarball] ERROR: {tarball.name} is not a valid gzip file")
        print("[ffmpeg-tarball] This usually means git-lfs fetch failed and the file is an LFS pointer.")
        print("[ffmpeg-tarball] Run: git lfs pull --include='3rdparty/ffmpeg/*'")
        # Show first few bytes to help debug
        try:
            content = tarball.read_bytes()[:200].decode("utf-8", errors="replace")
            print(f"[ffmpeg-tarball] File content preview: {content[:100]}...")
        except Exception:
            pass
        return 1

    print(f"[ffmpeg-tarball] OK: Valid gzip tarball ({file_info})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
