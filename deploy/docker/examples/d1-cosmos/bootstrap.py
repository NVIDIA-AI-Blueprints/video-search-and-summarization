# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register the demo inputs and create missing stream and rule records."""

import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

import httpx

QUESTION = "Is there a queue of stopped or slow-moving vehicles?"
INPUTS = (
    ("d1-flowing", "D1_FLOWING_RTSP_URL"),
    ("d1-queued", "D1_QUEUED_RTSP_URL"),
)


def read_sources(environ):
    sources = {}
    for key, variable in INPUTS:
        url = environ.get(variable, "")
        try:
            parts = urlsplit(url)
            valid = (
                parts.scheme in {"rtsp", "rtsps"}
                and parts.hostname
                and parts.username is None
                and len(url) <= 2048
                and not any(char.isspace() for char in url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{variable} must be a reachable RTSP URL without embedded credentials.")
        sources[key] = url
    if len(set(sources.values())) != len(sources):
        raise ValueError("Provide different RTSP streams for flowing and queued traffic.")
    return sources


def vss(*args):
    result = subprocess.run(
        [os.environ.get("VSS_CLI", "vss"), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # The CLI error may contain the full camera URL. Log only the operation and exit code.
        raise RuntimeError(f"vss {' '.join(args[:2])} failed with exit code {result.returncode}.")
    return json.loads(result.stdout) if result.stdout.strip() else None


def configure(client, ingest_url, analytics_url, vss_origin, sources, cli=vss):
    streams_url = f"{ingest_url.rstrip('/')}/v1/streams"
    rules_url = f"{analytics_url.rstrip('/')}/v1/rules"
    streams = {row["id"]: row for row in client.get(streams_url).raise_for_status().json()}
    rules = {row["id"]: row for row in client.get(rules_url).raise_for_status().json()}

    for key, url in sources.items():
        stream, rule = streams.get(key), rules.get(key)
        if stream and (
            stream["config"]["rtsp_url"] != url or stream["config"]["vios_sensor"] != key
        ):
            raise ValueError(f"{key} already has a different input. Keep its settings or use fresh profile data.")
        if rule and (
            not stream
            or rule["config"]["stream_id"] != key
            or rule["config"]["config_revision"] != stream["revision"]
        ):
            raise ValueError(f"{key} has a rule that does not match its stream revision. Review the configuration.")

    cli("configure", "--base-url", vss_origin)
    sensors = cli("vios", "list")["sensors"]
    missing = set()
    for key, url in sources.items():
        named = [sensor for sensor in sensors if sensor["name"] == key]
        if not named:
            missing.add(key)
            continue
        main = [sensor for sensor in named if sensor.get("is_main")] or named
        if len({sensor.get("sensor_id") for sensor in named}) != 1 or len(main) != 1 or main[0].get("source") != url:
            raise ValueError(f"VIOS already has a different or ambiguous sensor named {key}.")

    for key, url in sources.items():
        if key in missing:
            cli("vios", "add", "--type", "stream", url, "--name", key)

    fresh = not any(key in streams for key in sources)
    for key, url in sources.items():
        stream = streams.get(key)
        if not stream:
            stream = client.put(
                f"{streams_url}/{key}",
                json={
                    "rtsp_url": url,
                    "vios_sensor": key,
                    "question": {"type": "noul", "instructions": QUESTION},
                    "interval_seconds": 2,
                    "input_mode": "video",
                    "video_frames": 8,
                    "video_fps": 1,
                    "enabled": fresh and key == "d1-flowing",
                },
            ).raise_for_status().json()
        if key not in rules:
            client.put(
                f"{rules_url}/{key}",
                json={
                    "stream_id": key,
                    "config_revision": stream["revision"],
                    "field": "noul",
                    "operator": "gte",
                    "value": 0.85,
                    "consecutive_samples": 3,
                    "max_gap_seconds": 10,
                    "cooldown_seconds": 60,
                    "pre_seconds": 15,
                    "post_seconds": 5,
                    "review_prompt": (
                        "Describe the visible vehicle queue and movement. "
                        "State uncertainty and avoid assigning a cause beyond the clip."
                    ),
                    "enabled": stream["config"]["enabled"],
                },
            ).raise_for_status()
        print(f"{key}: VIOS input and D1 rule configured")


def main():
    try:
        sources = read_sources(os.environ)
        with httpx.Client(timeout=15) as client:
            configure(
                client,
                os.environ["D1_INGEST_URL"],
                os.environ["D1_ANALYTICS_URL"],
                os.environ["D1_VSS_ORIGIN"],
                sources,
            )
    except httpx.HTTPStatusError as exc:
        print(f"D1 setup: request failed with HTTP {exc.response.status_code}.", file=sys.stderr)
        return 1
    except httpx.RequestError:
        print("D1 setup: could not reach a required service.", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, KeyError) as exc:
        print(f"D1 setup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
