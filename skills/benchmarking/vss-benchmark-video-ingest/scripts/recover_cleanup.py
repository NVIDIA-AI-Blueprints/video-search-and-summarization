#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recover interrupted runs using persisted upload IDs and public VIOS inventory.

Dry-run by default. Never infer ownership from a filename prefix or delete ES data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any
from urllib.parse import urlsplit

from upload import delete_asset
from vss_cli import VssCli

SCHEMA = "vss-ingest-upload-ledger-v1"
UPLOAD_NAME = re.compile(r"[0-9a-f]{32}-[0-9]{5,}\.[a-z0-9]+\Z")


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


class UploadLedger:
    """Append and fsync returned identities before ES polling; thread-safe."""

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
        line = json.dumps({"run_id": self.run_id, **identity}, sort_keys=True) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())


def load_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0], dict) or rows[0].get("schema") != SCHEMA:
        raise ValueError("Invalid upload ledger header")
    header = rows[0]
    if not isinstance(header.get("run_id"), str) or not header["run_id"]:
        raise ValueError("Missing ledger run_id")
    seen = set()
    for row in rows[1:]:
        if not isinstance(row, dict) or row.get("run_id") != header["run_id"]:
            raise ValueError("Upload identity does not belong to this ledger run")
        sensor = row.get("sensor_id")
        filename = row.get("upload_filename")
        if (not isinstance(sensor, str) or not sensor or sensor != sensor.strip()
                or not isinstance(filename, str) or not UPLOAD_NAME.fullmatch(filename)
                or row.get("camera_name") != filename.rsplit(".", 1)[0]):
            raise ValueError("Invalid returned upload identity")
        if sensor in seen:
            raise ValueError("Duplicate sensor ID in upload ledger; inspect it before recovery")
        seen.add(sensor)
    return header, rows[1:]


def recover(cli: VssCli, path: Path, *, apply: bool = False) -> dict[str, Any]:
    """Validate all targets before any deletion, then delete matching IDs once."""
    header, identities = load_ledger(path)
    current_url = vios_url(cli.deployment(check_health=False))
    if header.get("vios_url") != current_url:
        raise ValueError("CLI VIOS URL differs from the recorded run; select its original CLI configuration")
    result = cli.call("vios", "list", "--type", "video")
    sensors = result.body.get("sensors")
    if result.exit_code or not isinstance(sensors, list):
        raise ValueError(f"Cannot verify VIOS inventory: CLI exit {result.exit_code}")
    by_id: dict[str, list[dict[str, Any]]] = {}
    for sensor in sensors:
        if not isinstance(sensor, dict) or not isinstance(sensor.get("sensor_id"), str):
            raise ValueError("Invalid VIOS video inventory")
        by_id.setdefault(sensor["sensor_id"], []).append(sensor)
    targets = []
    for identity in identities:
        matches = by_id.get(identity["sensor_id"], [])
        if matches and (len(matches) != 1 or matches[0].get("name") != identity["camera_name"]
                        or matches[0].get("type") != "video"):
            raise ValueError(f"Refusing cleanup: current identity differs for sensor {identity['sensor_id']}")
        targets.append({"sensor_id": identity["sensor_id"], "name": identity["camera_name"],
                        "status": "would_delete" if matches else "already_absent"})
    if apply:
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
    parser.add_argument("--uv-executable", default="uv")
    parser.add_argument("--apply", action="store_true", help="Delete verified assets; default only previews.")
    args = parser.parse_args(argv)
    try:
        cli = VssCli(args.vss_repo, args.cli_config_home, args.uv_executable, args.cli_executable)
        result = recover(cli, args.ledger, apply=args.apply)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if any(asset["status"] == "delete_failed" for asset in result["assets"]) else 0


if __name__ == "__main__":
    sys.exit(main())
