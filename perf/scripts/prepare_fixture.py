#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Acquire the pinned warehouse sample when a worker has no supplied fixture."""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import yaml


def main():
    video = Path(os.environ["VSS_PERF_VIDEO"])
    if video.is_file():
        return
    config = yaml.safe_load(Path(os.environ["PERF_CONFIG"]).read_text())["global"][
        "dataset"
    ]
    if not os.environ.get("NGC_CLI_API_KEY"):
        raise SystemExit(
            "The pinned benchmark dataset needs NGC_API_KEY, or provide VSS_PERF_VIDEO/VSS_PERF_STREAM_URL"
        )
    data = Path(os.environ["VSS_DATA_DIR"])
    downloads = data / "fixture-download"
    downloads.mkdir(parents=True, exist_ok=True)
    from ensure_ngc import ensure_ngc

    ngc = ensure_ngc(data)
    download = subprocess.run(
        [
            ngc,
            "registry",
            "resource",
            "download-version",
            config["resource"],
            "--dest",
            str(downloads),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if download.returncode:
        raise RuntimeError(
            f"NGC benchmark dataset download failed (exit {download.returncode}); check resource access and NGC credentials"
        )
    expected = config["video_path"]
    candidates = [
        p for p in downloads.rglob(video.name) if p.as_posix().endswith(expected)
    ]
    video.parent.mkdir(parents=True, exist_ok=True)
    if len(candidates) == 1:
        shutil.copyfile(candidates[0], video)
    else:
        matches = []
        for archive in downloads.rglob("*.tar.gz"):
            with tarfile.open(archive) as source:
                for member in source.getmembers():
                    if member.isfile() and member.name.endswith(expected):
                        matches.append((archive, member.name))
        if len(matches) != 1:
            raise ValueError(
                "Pinned NGC resource must contain exactly one expected benchmark video"
            )
        with (
            tarfile.open(matches[0][0]) as source,
            source.extractfile(matches[0][1]) as stream,
            video.open("wb") as destination,
        ):
            shutil.copyfileobj(stream, destination)
    (video.parent / "source.json").write_text(json.dumps(config, indent=2) + "\n")


if __name__ == "__main__":
    main()
