# SPDX-License-Identifier: Apache-2.0
"""Delete an owned evaluation gallery and reset its Elasticsearch/Kafka storage.

Destructive: the explicit workspace and ingestion manifest establish ownership.
Deletes file sources using the configured CLI, stops the Compose project, clears
only its dedicated ES and Kafka bind volumes, then recreates the project.
Models, source dataset, PostgreSQL, and model caches are preserved.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from flows import cleanup
from ingest_clip_gallery import configured_origin
from prepare_search_reproduction import command, compose, load_workspace, save


def owned_storage(runtime: Path, relative: str) -> Path:
    """Resolve a disposable volume directory that cannot lead outside ``runtime``.

    Resolved-path equality alone is not containment: a symbolic link anywhere
    on the path would make both sides resolve to the same foreign directory,
    which the root cleanup container would then empty.
    """
    current = runtime
    for part in (None, *Path(relative).parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise ValueError(
                f"Refusing to clear storage through a symbolic link: {current}"
            )
    root = runtime.resolve(strict=True)
    resolved = (runtime / relative).resolve(strict=True)
    if resolved == root or not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError(
            f"Storage is not a directory inside the owned runtime: {resolved}"
        )
    return resolved


def reset(workspace: Path, ingestion: Path, output: Path) -> None:
    workspace, manifest = load_workspace(workspace)
    cfg = configured_origin(manifest)
    previous = json.loads(ingestion.read_text())
    if not previous.get("complete"):
        raise ValueError("Expected a completed owned ingestion manifest")
    output.mkdir(parents=True, exist_ok=False)
    cli = Path(sys.executable).with_name("vss")
    rows = json.loads(command([cli, "vios", "list", "--type", "video", "--raw"]))[
        "sensors"
    ]
    if {r["sensor_id"] for r in rows} != {r["sensor_id"] for r in previous["clips"]}:
        raise ValueError("Registered sources differ from the owned gallery")
    save(output / "registered-sources.json", rows)
    graph = yaml.safe_load((workspace / "resolved.yml").read_text())
    if graph["name"] != manifest["project"]:
        raise ValueError("Unexpected Compose project")
    volumes = []
    for key, relative in [
        ("elastic-data", "data_log/elastic/data"),
        ("kafka-data", "data_log/kafka"),
    ]:
        spec = graph["volumes"][key]
        expected = owned_storage(workspace / "runtime", relative)
        if Path(spec["driver_opts"]["device"]).resolve() != expected:
            raise ValueError(
                "Storage is not the dedicated disposable runtime directory"
            )
        actual = json.loads(command(["docker", "volume", "inspect", spec["name"]]))[0]
        if (actual.get("Labels") or {}).get("com.docker.compose.project") != manifest[
            "project"
        ]:
            raise ValueError("Volume belongs to a different project")
        if Path(actual["Options"]["device"]).resolve() != expected.resolve():
            raise ValueError("Actual volume differs from Compose")
        volumes.append(spec["name"])

    def delete_videos(*_args):
        removed = []
        for row in rows:
            command(
                [
                    cli,
                    "vios",
                    "delete",
                    "--type",
                    "video",
                    "--sensor",
                    row["name"],
                    "--raw",
                ]
            )
            removed.append(row)
            save(output / "deleted-sources.json", removed)
            print(f"Deleted {len(removed)}/{len(rows)}: {row['name']}", flush=True)
        return {
            "found": len(rows),
            "deleted": len(removed),
            "failed": [],
            "names": [r["name"] for r in rows],
        }

    cleanup.clear_all_videos = delete_videos
    save(
        output / "cleanup-complete.json",
        cleanup.wipe_index(manifest["origin"], cfg.base_url, cfg.base_url),
    )
    subprocess.run(compose(workspace, "down", "--timeout", "60"), check=True)
    # Use the already-present ES image for filesystem maintenance; no admin API.
    image = graph["services"]["elasticsearch"]["image"]
    for volume in volumes:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "--entrypoint",
                "bash",
                "--mount",
                f"type=volume,source={volume},target=/reset",
                image,
                "-c",
                "find /reset -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +",
            ],
            check=True,
        )
    save(
        output / "storage-reset.json",
        {
            "project": manifest["project"],
            "cleared_volumes": volumes,
            "all_indices_removed": True,
        },
    )
    subprocess.run(compose(workspace, "up", "-d", "--no-build"), check=True)
    print(
        "Owned index and broker storage reset; wait for readiness before ingestion.",
        flush=True,
    )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ingestion-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    reset(args.workspace, args.ingestion_manifest, args.output_dir)
