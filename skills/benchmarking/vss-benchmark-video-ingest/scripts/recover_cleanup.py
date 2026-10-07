#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recover interrupted runs using persisted upload intents/IDs and public VIOS inventory.

Dry-run by default. Never infer ownership from a filename prefix or delete ES data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any
from urllib.parse import urlsplit

from upload import UPLOAD_NAME, delete_asset, pending_sensor_id, video_inventory
from vss_cli import VssCli

SCHEMA = "vss-ingest-upload-ledger-v2"


def vios_url(deployment: dict[str, Any]) -> str:
    services = deployment.get("services", {})
    vst = services.get("vst", {}) if isinstance(services, dict) else {}
    url = vst.get("url", "") if isinstance(vst, dict) else ""
    if not isinstance(url, str):
        raise ValueError("Missing VIOS URL in CLI configuration")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Recovery requires a credential-free public VIOS URL")
    return url.rstrip("/")


def append_identity(path: Path, run_id: str, identity: dict[str, Any]) -> None:
    """Durably pin an intent or returned/listed ID before any later mutation."""
    line = json.dumps({"run_id": run_id, **identity}, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


class UploadLedger:
    """Fsync intents before upload, then returned identities; thread-safe."""

    def __init__(self, path: Path, run_id: str, deployment: dict[str, Any]):
        self.path = path
        self.run_id = run_id
        self.lock = threading.Lock()
        header = {"schema": SCHEMA, "run_id": run_id, "vios_url": vios_url(deployment)}
        # Exclusive creation preserves the previous run's recovery evidence.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(header, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record(self, **identity: Any) -> None:
        with self.lock:
            append_identity(self.path, self.run_id, identity)


def load_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0], dict) or rows[0].get("schema") != SCHEMA:
        raise ValueError("Invalid upload ledger header")
    header = rows[0]
    if not isinstance(header.get("run_id"), str) or not header["run_id"]:
        raise ValueError("Missing ledger run_id")
    identities: dict[str, dict[str, Any]] = {}
    seen_sensors: set[str] = set()
    for row in rows[1:]:
        if not isinstance(row, dict) or row.get("run_id") != header["run_id"]:
            raise ValueError("Upload identity does not belong to this ledger run")
        sensor = row.get("sensor_id")
        filename = row.get("upload_filename")
        if (not isinstance(sensor, str) or sensor != sensor.strip()
                or not isinstance(filename, str) or not UPLOAD_NAME.fullmatch(filename)
                or row.get("camera_name") != filename.rsplit(".", 1)[0]):
            raise ValueError("Invalid upload identity")
        previous = identities.get(filename)
        if previous is not None and (previous["sensor_id"] or not sensor):
            raise ValueError("Duplicate or conflicting upload identity; inspect the ledger before recovery")
        if sensor:
            if sensor in seen_sensors:
                raise ValueError("Duplicate sensor ID in upload ledger; inspect it before recovery")
            seen_sensors.add(sensor)
        identities[filename] = row
    return header, list(identities.values())


def recover(cli: VssCli, path: Path, *, apply: bool = False) -> dict[str, Any]:
    """Validate all targets, pin newly resolved IDs, then delete verified IDs once."""
    header, identities = load_ledger(path)
    current_url = vios_url(cli.deployment(check_health=False))
    if header.get("vios_url") != current_url:
        raise ValueError("CLI VIOS URL differs from the recorded run; select its original CLI configuration")
    sensors = video_inventory(cli)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for sensor in sensors:
        by_id.setdefault(sensor["sensor_id"], []).append(sensor)
    targets = []
    resolutions = []
    target_ids: set[str] = set()
    for identity in identities:
        sensor_id = identity["sensor_id"]
        if not sensor_id:
            sensor_id = pending_sensor_id(identity["upload_filename"], sensors)
            if not sensor_id:
                targets.append({"sensor_id": "", "name": identity["camera_name"], "status": "unresolved"})
                continue
            resolutions.append({**identity, "sensor_id": sensor_id})
        matches = by_id.get(sensor_id, [])
        if matches and (len(matches) != 1 or matches[0].get("name") != identity["camera_name"]
                        or matches[0].get("type") != "video"):
            raise ValueError(f"Refusing cleanup: current identity differs for sensor {sensor_id}")
        if sensor_id in target_ids:
            raise ValueError("Different upload identities resolve to the same sensor ID; refusing cleanup")
        target_ids.add(sensor_id)
        targets.append({"sensor_id": sensor_id, "name": identity["camera_name"],
                        "status": "would_delete" if matches else "already_absent"})
    if apply:
        # Preserve every resolved handle before any delete. A later recovery must
        # never select a replacement sensor merely because it reused the name.
        for identity in resolutions:
            append_identity(path, header["run_id"], identity)
        for target in targets:
            if target["status"] == "would_delete":
                ok, _ = delete_asset(cli, target["sensor_id"])
                target["status"] = "deleted" if ok else "delete_failed"
    return {"run_id": header["run_id"], "mode": "apply" if apply else "dry_run", "assets": targets,
            "downstream_cleanup_verified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--vss-repo", type=Path,
                        default=Path(os.environ.get("VSS_REPO_ROOT", str(Path.home() / "video-search-and-summarization"))))
    parser.add_argument("--cli-executable", default=None)
    parser.add_argument("--cli-config-home", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Delete verified assets; default only previews.")
    args = parser.parse_args(argv)
    try:
        cli = VssCli(args.vss_repo, args.cli_config_home, executable=args.cli_executable)
        result = recover(cli, args.ledger, apply=args.apply)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if any(asset["status"] in {"delete_failed", "unresolved"} for asset in result["assets"]) else 0


if __name__ == "__main__":
    sys.exit(main())
