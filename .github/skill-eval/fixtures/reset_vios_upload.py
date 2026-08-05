#!/usr/bin/env python3
"""Remove one uploaded VIOS file sensor while keeping VSS deployed."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request


def request(base_url: str, path: str, method: str = "GET") -> object:
    target = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    with urllib.request.urlopen(
        urllib.request.Request(target, method=method), timeout=30,
    ) as response:
        body = response.read()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", "replace")


def matching_sensors(base_url: str, name: str) -> list[dict]:
    sensors = request(base_url, "sensor/list")
    if not isinstance(sensors, list):
        raise RuntimeError("VIOS sensor list was not an array")
    names = {name, f"{name}.mp4"}
    return [
        sensor for sensor in sensors
        if isinstance(sensor, dict) and sensor.get("name") in names
    ]


def matching_storage_streams(base_url: str, name: str) -> list[dict]:
    groups = request(base_url, "storage/streams")
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise RuntimeError("VIOS storage streams was not an array")
    names = {name, f"{name}.mp4"}
    return [
        stream
        for group in groups if isinstance(group, dict)
        for streams in group.values() if isinstance(streams, list)
        for stream in streams if isinstance(stream, dict)
        if stream.get("name") in names
    ]


def restart_services(base_url: str) -> None:
    subprocess.run(
        [
            "docker", "restart",
            "vss-vios-streamprocessing", "vss-vios-sensor",
            "vss-rtvi-vlm", "vss-agent",
        ],
        check=True, capture_output=True, text=True, timeout=180,
    )

    endpoints = (
        f"{base_url.rstrip('/')}/sensor/version",
        "http://localhost:8018/v1/models",
        "http://localhost:8000/docs",
    )
    for _attempt in range(600):
        try:
            for endpoint in endpoints:
                with urllib.request.urlopen(endpoint, timeout=10) as response:
                    if response.status != 200:
                        raise OSError(f"{endpoint} returned {response.status}")
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("VSS services did not recover after restart")


def remove_without_timeline(base_url: str, sensor_id: str) -> None:
    listing = request(base_url, f"storage/file/{sensor_id}/list")
    entries = listing.get(sensor_id) if isinstance(listing, dict) else None
    file_ids = [
        entry.get("metadata", {}).get("id")
        for entry in entries or []
        if isinstance(entry, dict) and isinstance(entry.get("metadata"), dict)
    ]
    file_ids = [value for value in file_ids if value]
    if not file_ids:
        raise RuntimeError(f"sensor {sensor_id} has no deletable storage file")

    for file_id in file_ids:
        query = urllib.parse.urlencode({"id": file_id})
        request(base_url, f"storage/file?{query}", method="DELETE")

    sensors = request(base_url, "sensor/list")
    if isinstance(sensors, list) and any(
            item.get("sensorId") == sensor_id
            for item in sensors if isinstance(item, dict)):
        request(base_url, f"sensor/{sensor_id}", method="DELETE")


def remove_sensor(base_url: str, sensor: dict) -> None:
    sensor_id = sensor.get("sensorId")
    streams = request(base_url, f"sensor/{sensor_id}/streams")
    if not isinstance(streams, list) or not streams:
        raise RuntimeError(f"sensor {sensor_id} has no streams")
    stream = next((item for item in streams if item.get("isMain")), streams[0])
    stream_id, stream_url = stream.get("streamId"), stream.get("url", "")
    if not stream_id:
        raise RuntimeError(f"sensor {sensor_id} has no stream ID")
    if str(stream_url).startswith("rtsp://"):
        raise RuntimeError(f"refusing to delete RTSP sensor {sensor_id}")

    timelines = request(base_url, f"storage/{stream_id}/timelines")
    ranges = timelines.get(stream_id) if isinstance(timelines, dict) else None
    if not isinstance(ranges, list) or not ranges:
        remove_without_timeline(base_url, sensor_id)
        return
    start, end = ranges[0].get("startTime"), ranges[0].get("endTime")
    if not start or not end:
        raise RuntimeError(f"stream {stream_id} has an invalid timeline")

    query = urllib.parse.urlencode({"startTime": start, "endTime": end})
    request(base_url, f"storage/file/{stream_id}?{query}", method="DELETE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",
                        default="http://localhost:30888/vst/api/v1")
    parser.add_argument("--name", default="warehouse_safety_0001")
    args = parser.parse_args()

    request(args.base_url, "sensor/version")
    for sensor in matching_sensors(args.base_url, args.name):
        remove_sensor(args.base_url, sensor)
    restart_services(args.base_url)

    # File-sensor deletion is asynchronous and can outlive the service restart.
    for _attempt in range(120):
        try:
            clean = not matching_sensors(args.base_url, args.name) \
                    and not matching_storage_streams(args.base_url, args.name)
            if clean:
                print(f"reset complete: {args.name} is absent")
                return 0
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f"sensor still present after reset: {args.name}")


if __name__ == "__main__":
    raise SystemExit(main())
