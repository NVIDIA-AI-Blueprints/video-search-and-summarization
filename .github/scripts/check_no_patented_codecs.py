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
"""Fail if a container image / filesystem ships patent-encumbered media codecs.

Patent pools (AVC/H.264 via Via-LA, HEVC/H.265 via Access Advance, ...) levy
royalties on *any* distributed software implementation of those codecs. FFmpeg's
native H.264/HEVC decoders live in ``libavcodec`` and are enabled by default, so
shipping the FFmpeg shared libraries (as ``opencv-python-headless`` and similar
wheels bundle them) creates per-copy liability. VSS containers therefore must NOT
bundle these libraries; operators opt in to install them at runtime on their own
machines (see ``services/agent/docker/install_proprietary_codecs.py``).

This script enforces that guarantee. It scans either an exported container image
or a filesystem path for the forbidden FFmpeg / codec shared libraries and exits
non-zero if any are found. It is the single source of truth shared by the
Dockerfile build-time guard and the CI job.

Usage:
    # Scan a built image (requires docker; uses `docker export`, no run needed):
    check_no_patented_codecs.py --image vss-agent:ci

    # Scan a filesystem path (used as the Dockerfile build-time guard):
    check_no_patented_codecs.py --path /vss-agent --path /usr
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tempfile

# FFmpeg shared libraries (the H.264/HEVC/MPEG codecs live in libavcodec; the rest
# of the av* / sw* / postproc family travels with it) plus standalone patent-bearing
# codec libraries. Matched case-insensitively against the *basename* of every file.
# Each entry is a library *stem*; matched as `^<stem>.*\.so` so both the plain
# soname form (libswscale.so.9) and the wheel-mangled form
# (libswscale-09114b18.so.9.1.100) are caught. Stems are enumerated explicitly
# rather than a broad `libav` prefix so unrelated libraries such as libavahi
# (Avahi/mDNS) are not falsely flagged.
FORBIDDEN_LIB_STEMS = [
    "libavcodec",
    "libavformat",
    "libavutil",
    "libavfilter",
    "libavdevice",
    "libavresample",
    "libavif",  # AV1 image codec bundled alongside FFmpeg by opencv wheels
    "libswscale",
    "libswresample",
    "libpostproc",
    "libx264",
    "libx265",
    "libde265",
    "libopenh264",
    "libkvazaar",
]

_FORBIDDEN_RE = re.compile(
    "|".join(rf"^{re.escape(stem)}.*\.so" for stem in FORBIDDEN_LIB_STEMS),
    re.IGNORECASE,
)

# Skip pseudo-filesystems when scanning a live path (e.g. the Docker build guard).
_SKIP_DIRS = {"/proc", "/sys", "/dev", "/run"}


def is_forbidden(basename: str) -> bool:
    """Return True if a file basename matches a forbidden codec library."""
    return _FORBIDDEN_RE.match(basename) is not None


def scan_paths(paths: list[str]) -> list[str]:
    """Walk filesystem paths and return forbidden library files found."""
    hits: list[str] = []
    for root_path in paths:
        for root, dirs, files in os.walk(root_path):
            if root in _SKIP_DIRS:
                dirs[:] = []
                continue
            for name in files:
                if is_forbidden(name):
                    hits.append(os.path.join(root, name))
    return hits


def scan_image(image: str) -> list[str]:
    """Export a container image's filesystem and return forbidden libraries found."""
    hits: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        cid = subprocess.check_output(["docker", "create", image], text=True).strip()
        try:
            tar_path = os.path.join(tmp, "image.tar")
            with open(tar_path, "wb") as fh:
                subprocess.check_call(["docker", "export", cid], stdout=fh)
        finally:
            subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)
        with tarfile.open(tar_path) as tar:
            for member in tar:
                if member.isfile() and is_forbidden(os.path.basename(member.name)):
                    hits.append(member.name)
    return hits


def resolve_deploy_refs(image_name: str) -> list[str]:
    """Resolve the deployed image ref(s) for ``image_name`` from deploy/docker.

    Reuses ``check_container_tag_source`` (the same resolver the container-source
    check uses) so we scan the *exact* image the blueprint pins — no local build.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import check_container_tag_source as cts  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path.cwd()
    if image_name not in cts.IMAGE_CONFIGS:
        raise SystemExit(f"unknown deploy image '{image_name}' (choices: {sorted(cts.IMAGE_CONFIGS)})")
    cfg = cts.IMAGE_CONFIGS[image_name]
    images, _unresolved = cts.collect_resolved_images(
        root, cfg, cts.discover_compose_files(root), cts.discover_env_files(root)
    )
    return [img.resolved_ref for img in images]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if patent-encumbered media codecs are present.")
    parser.add_argument("--image", help="Container image ref to scan (via `docker export`).")
    parser.add_argument(
        "--from-deploy",
        metavar="IMAGE_NAME",
        help="Resolve the deployed image ref(s) for this image (e.g. vss-agent) from "
        "deploy/docker and scan the already-published image(s) — no local build.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Filesystem path to scan (repeatable). Default: /usr /vss-agent /opt",
    )
    args = parser.parse_args(argv)

    if args.from_deploy:
        refs = resolve_deploy_refs(args.from_deploy)
        if not refs:
            print(f"FAIL: no deployable {args.from_deploy} image reference found under deploy/docker.", file=sys.stderr)
            return 1
        rc = 0
        for ref in refs:
            print(f"Scanning deployed image: {ref}")
            if scan_and_report(scan_image(ref), f"image {ref}"):
                rc = 1
        return rc

    if args.image:
        hits = scan_image(args.image)
        target = f"image {args.image}"
    else:
        paths = args.path or ["/usr", "/vss-agent", "/opt"]
        paths = [p for p in paths if os.path.exists(p)]
        hits = scan_paths(paths)
        target = ", ".join(paths) or "(no existing paths)"

    return 1 if scan_and_report(hits, target) else 0


def scan_and_report(hits: list[str], target: str) -> bool:
    """Print the result for one scanned target. Returns True if forbidden libs found."""
    if hits:
        print(f"FAIL: found {len(hits)} patent-encumbered codec librar(y/ies) in {target}:", file=sys.stderr)
        for path in sorted(hits):
            print(f"  - {path}", file=sys.stderr)
        print(
            "\nThese must not ship in the container. They are installed at runtime only when the "
            "operator opts in via INSTALL_PROPRIETARY_CODECS=true.",
            file=sys.stderr,
        )
        return True
    print(f"OK: no patent-encumbered codec libraries found in {target}.")
    return False


if __name__ == "__main__":
    sys.exit(main())
