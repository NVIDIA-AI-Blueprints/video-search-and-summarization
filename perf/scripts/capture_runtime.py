#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Capture actual image and fixture identities without exporting environment secrets."""

import hashlib
import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path


def output(command):
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def main():
    data = Path(os.environ["VSS_DATA_DIR"])
    docker = ["docker"]
    if os.environ.get("DOCKER_USE_SUDO") == "true":
        docker = ["sudo", "-n", "docker"]
    ids = output(
        docker
        + [
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=" + os.environ["COMPOSE_PROJECT_NAME"],
        ]
    ).split()
    if not ids:
        raise ValueError("No containers found for the benchmark Compose project")
    containers = json.loads(output(docker + ["inspect", *ids]))
    image_ids = sorted({row["Image"] for row in containers})
    images = json.loads(output(docker + ["image", "inspect", *image_ids]))
    digest_by_id = {row["Id"]: row.get("RepoDigests") or [] for row in images}
    deployed = [
        {
            "container_id": row["Id"],
            "container_name": row["Name"].lstrip("/"),
            "image_ref": row["Config"]["Image"],
            "image_id": row["Image"],
            "repo_digests": digest_by_id[row["Image"]],
        }
        for row in containers
    ]
    video_path = os.environ.get("VSS_PERF_VIDEO")
    if video_path:
        video = Path(video_path)
        with video.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        probe = json.loads(
            output(
                docker
                + [
                    "run",
                    "--rm",
                    "--entrypoint",
                    "ffprobe",
                    "-v",
                    f"{video}:/fixture/video.mp4:ro",
                    "jrottenberg/ffmpeg:7.1-ubuntu",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    "-show_format",
                    "/fixture/video.mp4",
                ]
            )
        )
        streams = [
            stream for stream in probe["streams"] if stream["codec_type"] == "video"
        ]
        if len(streams) != 1:
            raise ValueError("Benchmark fixture must have exactly one video stream")
        stream = streams[0]
        fixture = {
            "kind": "video_file",
            "sha256": digest,
            "width": stream["width"],
            "height": stream["height"],
            "fps": float(Fraction(stream["avg_frame_rate"])),
            "duration_seconds": float(probe["format"]["duration"]),
            "codec": stream["codec_name"],
        }
    else:
        fixture = {
            "kind": "external_stream",
            "repeatability": "input file hash unavailable",
        }
    downloader = (
        Path(os.environ["VSS_REPO_ROOT"])
        / "deploy/docker/services/rtvi/rtvi-cv/download-models.sh"
    )
    result = {
        "fixture": fixture,
        "deployed_images": deployed,
        "compatibility_helpers": [
            {
                "name": "rt-cv-model-downloader",
                "sha256": hashlib.sha256(downloader.read_bytes()).hexdigest(),
            }
        ],
        "model_assets": [],
    }
    for model in sorted((data / "models").glob("*.onnx")):
        with model.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        result["model_assets"].append(
            {"file_name": model.name, "kind": "onnx", "sha256": digest}
        )
    promoted = data / "promoted-artifact.json"
    if promoted.exists() and (
        os.environ.get("ARTIFACT_REF") or os.environ.get("IMAGE_TAG")
    ):
        artifact = json.loads(promoted.read_text())
        result["promoted_artifact"] = {
            key: artifact[key]
            for key in (
                "artifact_ref",
                "resource_version",
                "verified",
                "compose_tree_sha256",
                "container_pins",
            )
            if key in artifact
        }
    (data / "metadata.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
