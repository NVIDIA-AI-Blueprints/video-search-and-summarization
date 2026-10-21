# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare explicit media/embedding assertions before any destructive ingest work.

This host-side runner need not import vss_core: the installed vss command owns
index inspection and independently validates the complete, resolved contract.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any


def prepare_contract(
    filename: str, files: list[Path]
) -> tuple[dict[str, Any], dict[str, Any]]:
    template = json.loads(Path(filename).read_text())
    if not isinstance(template, dict) or set(template) - {
        "schema_version",
        "embedding",
        "document_limit",
    }:
        raise ValueError(
            "Ingestion contract accepts only schema_version, embedding and document_limit; frame counts are decoded locally"
        )
    if (
        type(template.get("schema_version")) is not int
        or template["schema_version"] != 1
    ):
        raise ValueError("Ingestion contract schema_version must be 1")
    embedding = template.get("embedding")
    if not isinstance(embedding, dict) or set(embedding) - {
        "model",
        "dimensions",
        "unit_normalized",
        "norm_tolerance",
    }:
        raise ValueError("Invalid embedding contract fields")
    if not isinstance(embedding.get("model"), str) or not embedding["model"].strip():
        raise ValueError("An explicit embedding model is required")
    if (
        type(embedding.get("dimensions")) is not int
        or not 1 <= embedding["dimensions"] <= 65536
    ):
        raise ValueError("Embedding dimensions must be an integer in [1, 65536]")
    if type(embedding.get("unit_normalized")) is not bool:
        raise ValueError("unit_normalized must be explicit boolean")
    tolerance = embedding.get("norm_tolerance", 0.001)
    try:
        valid_tolerance = (
            type(tolerance) in (int, float)
            and math.isfinite(tolerance)
            and 0 <= tolerance < 1
        )
    except OverflowError:
        valid_tolerance = False
    if not valid_tolerance:
        raise ValueError("norm_tolerance must be finite and in [0, 1)")
    limit = template.get("document_limit")
    if type(limit) is not int or not 1 <= limit <= 1000000:
        raise ValueError("document_limit must be an integer in [1, 1000000]")
    if not files or len({file.stem for file in files}) != len(files):
        raise ValueError("Content validation requires distinct gallery filenames")
    # Check the tool up front; do not upload an expensive gallery before finding
    # that this host cannot establish decoded frame bounds.
    version = subprocess.run(
        ["ffprobe", "-version"], capture_output=True, text=True, check=True, timeout=10
    )
    decoded: dict[str, int] = {}
    for file in files:
        process = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "json",
                str(file.resolve()),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        streams = json.loads(process.stdout).get("streams")
        if (
            not isinstance(streams, list)
            or len(streams) != 1
            or not isinstance(streams[0], dict)
        ):
            raise ValueError(f"Cannot resolve one decoded video stream for {file}")
        count = streams[0].get("nb_read_frames")
        if (
            not isinstance(count, str)
            or not count.isascii()
            or not count.isdecimal()
            or len(count) > 12
            or int(count) <= 0
        ):
            raise ValueError(f"Missing positive decoded frame count for {file}")
        decoded[file.stem] = int(count)
    contract = {
        **template,
        "embedding": {**embedding, "norm_tolerance": tolerance},
        "frame_counts": decoded,
    }
    provenance = {
        "frame_count_method": "ffprobe -count_frames / nb_read_frames",
        "ffprobe_version": version.stdout.splitlines()[0]
        if version.stdout
        else "unknown",
    }
    return contract, provenance
