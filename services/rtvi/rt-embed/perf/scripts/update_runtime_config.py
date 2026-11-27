# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Apply deployment-discovered values to an RTVI runtime configuration."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from urllib.parse import urlsplit

import yaml


RTSP_PLACEHOLDER = "RTSP_STREAM_URL"


def _mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a YAML mapping")
    return value


def _validated_url(value: str, name: str, schemes: set[str]) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in schemes or not parsed.hostname:
        expected = "/".join(sorted(schemes))
        raise ValueError(f"{name} must be a valid {expected} URL: {value!r}")
    return value


def update_runtime_config(
    config: dict,
    *,
    rtsp_url: str,
    rtvi_backend: str,
    dcgm_exporter_url: str,
    node_exporter_url: str,
    initial_stream_count: int | None = None,
) -> tuple[dict, int, int]:
    rtsp_url = _validated_url(rtsp_url, "rtsp_url", {"rtsp"})
    rtvi_backend = _validated_url(rtvi_backend, "rtvi_backend", {"http", "https"})
    dcgm_exporter_url = _validated_url(
        dcgm_exporter_url, "dcgm_exporter_url", {"http", "https"}
    )
    node_exporter_url = _validated_url(
        node_exporter_url, "node_exporter_url", {"http", "https"}
    )
    if initial_stream_count is not None and initial_stream_count <= 0:
        raise ValueError("initial_stream_count must be positive")

    global_config = _mapping(config.get("global"), "global")
    gpu_monitoring = _mapping(global_config.get("gpu_monitoring"), "global.gpu_monitoring")
    prometheus = _mapping(
        gpu_monitoring.get("prometheus"), "global.gpu_monitoring.prometheus"
    )
    scenarios = _mapping(config.get("test_scenarios"), "test_scenarios")

    global_config["rtvi_backend"] = rtvi_backend
    prometheus["dcgm_exporter_url"] = dcgm_exporter_url
    prometheus["node_exporter_url"] = node_exporter_url

    rtsp_updates = 0
    stream_count_updates = 0
    for scenario_name, scenario_value in scenarios.items():
        scenario = _mapping(scenario_value, f"test_scenarios.{scenario_name}")
        videos = scenario.get("videos", [])
        if videos is None:
            continue
        if not isinstance(videos, list):
            raise ValueError(f"test_scenarios.{scenario_name}.videos must be a YAML list")
        for index, video_value in enumerate(videos):
            video = _mapping(video_value, f"test_scenarios.{scenario_name}.videos[{index}]")
            if "rtsp_url" in video:
                video["rtsp_url"] = rtsp_url
                rtsp_updates += 1
            if initial_stream_count is not None and "initial_stream_count" in video:
                video["initial_stream_count"] = initial_stream_count
                stream_count_updates += 1

    if rtsp_updates == 0:
        raise ValueError("runtime config does not contain any rtsp_url fields")
    if initial_stream_count is not None and stream_count_updates == 0:
        raise ValueError("runtime config does not contain any initial_stream_count fields")

    remaining = [
        f"{scenario_name}[{index}]"
        for scenario_name, scenario in scenarios.items()
        for index, video in enumerate(scenario.get("videos") or [])
        if isinstance(video, dict) and video.get("rtsp_url") == RTSP_PLACEHOLDER
    ]
    if remaining:
        raise ValueError(f"RTSP placeholder remains in: {', '.join(remaining)}")
    return config, rtsp_updates, stream_count_updates


def _atomic_write_yaml(path: Path, config: dict) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            yaml.safe_dump(config, output, sort_keys=False)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        verified = yaml.safe_load(temporary.read_text(encoding="utf-8"))
        if verified != config:
            raise ValueError("written runtime config failed semantic verification")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--rtsp-url", required=True)
    parser.add_argument("--rtvi-backend", required=True)
    parser.add_argument("--dcgm-exporter-url", required=True)
    parser.add_argument("--node-exporter-url", required=True)
    parser.add_argument("--initial-stream-count", type=int)
    args = parser.parse_args()

    path = Path(args.config)
    try:
        config = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "document")
        config, rtsp_updates, stream_count_updates = update_runtime_config(
            config,
            rtsp_url=args.rtsp_url,
            rtvi_backend=args.rtvi_backend,
            dcgm_exporter_url=args.dcgm_exporter_url,
            node_exporter_url=args.node_exporter_url,
            initial_stream_count=args.initial_stream_count,
        )
        _atomic_write_yaml(path, config)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Failed to update RTVI runtime config: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "config": str(path),
                "rtsp_updates": rtsp_updates,
                "initial_stream_count_updates": stream_count_updates,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
