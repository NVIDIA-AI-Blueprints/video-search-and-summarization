#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reset an explicitly isolated Compose evaluation deployment; never a shared stack."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

MARKER = ".vss-evaluation-owner.json"


def owned_directory(path, project, initialize=False):
    raw = Path(path).absolute()
    if raw.is_symlink() or raw != raw.resolve():
        raise ValueError(
            "Evaluation data directory must not contain symlink components"
        )
    if not re.fullmatch(r"vss-eval-[a-z0-9][a-z0-9_-]+", project):
        raise ValueError("Project must be a dedicated vss-eval-<name> project")
    marker = raw / MARKER
    if initialize:
        raw.mkdir(parents=True, exist_ok=True)
        if any(raw.iterdir()):
            raise ValueError("Ownership can only be initialized in an empty directory")
        marker.write_text(json.dumps({"project": project, "data_dir": str(raw)}))
    if not marker.is_file() or marker.is_symlink():
        raise ValueError(
            "Missing evaluation ownership marker; initialize a new empty directory"
        )
    if json.loads(marker.read_text()) != {"project": project, "data_dir": str(raw)}:
        raise ValueError(
            "Evaluation ownership marker does not match project and directory"
        )
    return raw


def inside(path, root):
    return Path(path).resolve().is_relative_to(root)


def overlaps(path, root):
    return inside(path, root) or inside(root, Path(path).resolve())


def validate_config(config, root, retention_hours):
    services = config["services"]
    for name in ("elasticsearch", "elasticsearch-init-container", "kafka", "redis"):
        if name not in services:
            raise ValueError(f"Required local state service missing: {name}")
    init = services["elasticsearch-init-container"].get("environment", {})
    if init.get("ELASTICSEARCH_URL") != "http://elasticsearch:9200":
        raise ValueError("Reset requires the local Compose Elasticsearch service")
    if init.get("ELASTICSEARCH_ILM_MIN_AGE") != f"{retention_hours}h":
        raise ValueError("Compose must forward the requested explicit retention")
    for name, volume in config.get("volumes", {}).items():
        if volume.get("external"):
            raise ValueError(
                f"External volume is forbidden in an evaluation reset: {name}"
            )
        options = volume.get("driver_opts", {})
        if volume.get("driver", "local") != "local" or (
            options and (options.get("type") != "none" or options.get("o") != "bind")
        ):
            raise ValueError(
                f"Only local or scoped local-bind volumes are supported: {name}"
            )
        device = options.get("device")
        if device and not any(
            inside(device, root / part) for part in ("data_log", "agent_eval")
        ):
            raise ValueError(
                f"Volume {name} stores state outside evaluation data directory"
            )
    state_targets = (
        "/data",
        "/log",
        "/tmp/elastic",
        "/tmp/kafka-data",
        "/web-api-app/files",
        "/var/run/postgresql",
        "/home/vst/vst_release/vst_data",
        "/home/vst/vst_release/vst_video",
        "/home/vst/vst_release/webroot/temp_files",
    )
    for name, service in services.items():
        for mount in service.get("volumes", []):
            target = mount.get("target", "")
            is_state = any(
                target == prefix or target.startswith(prefix + "/")
                for prefix in state_targets
            )
            if (
                mount["type"] == "bind"
                and not mount.get("read_only", False)
                and is_state
                and not any(
                    inside(mount["source"], root / part)
                    for part in ("data_log", "agent_eval")
                )
            ):
                raise ValueError(
                    f"State mount for {name} is outside evaluation runtime directory: {target}"
                )
            if (
                mount["type"] == "bind"
                and "streamer_videos" in target
                and not mount.get("read_only", False)
                and not any(
                    inside(mount["source"], root / part)
                    for part in ("data_log", "agent_eval")
                )
            ):
                raise ValueError(
                    f"Media storage for {name} is outside evaluation directory"
                )


def validate_resources(config, containers, volumes, project, root):
    expected_names = {s.get("container_name") for s in config["services"].values()}
    expected_volumes = {v["name"] for v in config.get("volumes", {}).values()}
    # A volume can carry our project label yet be mounted by a foreign
    # container after being removed from the current Compose configuration.
    # Its backing path, rather than its name or owner label, defines overlap.
    aliased_volumes = {
        volume["Name"]
        for volume in volumes
        if (volume.get("Options") or {}).get("device")
        and overlaps(volume["Options"]["device"], root)
    }
    for container in containers:
        owner = container.get("Config", {}).get("Labels", {}) or {}
        own = owner.get("com.docker.compose.project") == project
        uses_data = any(
            bool(m.get("Source")) and overlaps(m["Source"], root)
            for m in container.get("Mounts", [])
        )
        uses_volume = any(
            m.get("Name") in expected_volumes | aliased_volumes
            for m in container.get("Mounts", [])
        )
        named = container.get("Name", "").lstrip("/") in expected_names
        if not own and (uses_data or uses_volume or named):
            raise ValueError(
                "Another project/container shares evaluation paths, volumes or container names"
            )
    for volume in volumes:
        device = (volume.get("Options") or {}).get("device")
        labels = volume.get("Labels") or {}
        if (
            device
            and overlaps(device, root)
            and labels.get("com.docker.compose.project") != project
        ):
            raise ValueError(
                f"Foreign volume aliases evaluation storage: {volume['Name']}"
            )
        if volume["Name"] in expected_volumes:
            labels = volume.get("Labels") or {}
            if labels.get("com.docker.compose.project") != project:
                raise ValueError(
                    f"Volume is not owned by this project: {volume['Name']}"
                )


def validate_empty_count(result):
    shards = result.get("_shards") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or type(result.get("count")) is not int
        or result["count"] != 0
        or not isinstance(shards, dict)
        or any(
            type(shards.get(key)) is not int or shards[key] < 0
            for key in ("total", "successful", "failed")
        )
        or shards["failed"] != 0
        or shards["successful"] != shards["total"]
    ):
        raise ValueError(f"Evaluation documents remain or count failed: {result}")
    return result


def execute_reset(compose, root, project, config, run):
    """Every command fails closed; a failed verification never starts ingestion services."""
    run([*compose, "build", "elasticsearch-init-container"])
    run([*compose, "down", "--volumes", "--remove-orphans", "--timeout", "60"])
    remaining = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ]
    )
    if remaining.strip():
        raise ValueError(
            "Project containers remain after shutdown; refusing to remove state"
        )
    for name in ("data_log", "agent_eval"):
        child = root / name
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        elif child.exists() or child.is_symlink():
            child.unlink()
    # Recreate bind source directories before Docker mounts them. Config files are
    # expected outside this directory; only empty runtime directories belong here.
    for service in config["services"].values():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind" and inside(mount["source"], root):
                Path(mount["source"]).mkdir(parents=True, exist_ok=True)
    for volume in config.get("volumes", {}).values():
        device = volume.get("driver_opts", {}).get("device")
        if device:
            Path(device).mkdir(parents=True, exist_ok=True)
    for runtime in (root / "data_log", root / "agent_eval"):
        if runtime.exists():
            for directory in [runtime, *(p for p in runtime.rglob("*") if p.is_dir())]:
                directory.chmod(0o777)
    run([*compose, "up", "-d", "--wait", "--wait-timeout", "600", "elasticsearch"])
    # Existing administrative init mechanism installs the revision's mappings,
    # pipelines and new retention policies into the fresh Elasticsearch volume.
    run([*compose, "run", "--rm", "--no-deps", "-T", "elasticsearch-init-container"])
    # Expose the upload-anchor families before `vss configure` probes available
    # indices. Otherwise a fresh deployment can cache attribute/raw lookup as
    # absent even after ingestion later creates those indices.
    for family in ("mdx-embed-filtered", "mdx-behavior", "mdx-raw"):
        index = f"{family}-2025-01-01"
        created = json.loads(
            run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "elasticsearch-init-container",
                    "bash",
                    "-ec",
                    'curl --fail --silent --show-error --request PUT --header "Content-Type: application/json" --data "{}" "${ELASTICSEARCH_URL}/'
                    + index
                    + '"',
                ]
            )
        )
        if (
            not isinstance(created, dict)
            or created.get("acknowledged") is not True
            or created.get("shards_acknowledged") is not True
            or created.get("index") != index
        ):
            raise ValueError(
                f"Evaluation anchor index was not created successfully: {index}"
            )
    count_command = [
        *compose,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "elasticsearch-init-container",
        "bash",
        "-ec",
        'curl --fail --silent --show-error "${ELASTICSEARCH_URL}/mdx-*/_count?ignore_unavailable=true&allow_no_indices=true"',
    ]
    response = run(count_command)
    validate_empty_count(json.loads(response))
    try:
        run([*compose, "up", "-d", "--wait", "--wait-timeout", "900"])
        return validate_empty_count(json.loads(run(count_command)))
    except Exception:
        run([*compose, "stop", "--timeout", "60"])
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--retention-hours", type=int, default=336)
    parser.add_argument(
        "--initialize",
        action="store_true",
        help="Only mark a new empty directory; never resets anything",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the destructive reset of this isolated evaluation project",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Required for execution; store outside evaluation data directory",
    )
    args = parser.parse_args(argv)
    if args.retention_hours < 72:
        parser.error(
            "Evaluation retention must be at least 72 hours; allow enough time for the full campaign"
        )
    root = owned_directory(args.data_dir, args.project, args.initialize)
    if args.initialize:
        print(f"Initialized isolated evaluation ownership: {root}")
        return 0
    if not args.compose_file:
        parser.error("At least one --compose-file is required")
    env = {
        **os.environ,
        "VSS_DATA_DIR": str(root),
        "ELASTICSEARCH_ILM_MIN_AGE": f"{args.retention_hours}h",
    }
    compose = ["docker", "compose", "--project-name", args.project]
    for option, values in (
        ("-f", args.compose_file),
        ("--env-file", args.env_file),
        ("--profile", args.profile),
    ):
        for value in values:
            compose.extend([option, value])

    def run(command):
        return subprocess.run(
            command,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=1200,
        ).stdout

    config = json.loads(run([*compose, "config", "--format", "json"]))
    validate_config(config, root, args.retention_hours)
    ids = run(["docker", "ps", "-aq"]).split()
    containers = json.loads(run(["docker", "inspect", *ids])) if ids else []
    ids = run(["docker", "volume", "ls", "-q"]).split()
    volumes = json.loads(run(["docker", "volume", "inspect", *ids])) if ids else []
    validate_resources(config, containers, volumes, args.project, root)
    receipt = {
        "project": args.project,
        "data_dir": str(root),
        "retention_hours": args.retention_hours,
        "config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "planned",
    }
    print(json.dumps(receipt, indent=2))
    if not args.execute:
        print(
            "Validated plan only. --execute stops this project and destroys its data_log, agent_eval and named-volume runtime state."
        )
        return 0
    if not args.report or inside(args.report, root) or args.report.is_symlink():
        parser.error(
            "--execute requires --report outside the evaluation data directory"
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        receipt["status"] = "resetting"
        args.report.write_text(json.dumps(receipt, indent=2))
        receipt["zero_document_check"] = execute_reset(
            compose, root, args.project, config, run
        )
        receipt["status"] = "complete"
    except Exception as exc:
        receipt.update(status="failed", error=str(exc))
        raise
    finally:
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        args.report.write_text(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
