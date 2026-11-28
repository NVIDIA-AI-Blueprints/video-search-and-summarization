# SPDX-License-Identifier: Apache-2.0
"""Apply the opt-in native PAS evaluation profile to an owned search workspace.

Build the accompanying RT-CV Dockerfile first. This updates generated Compose
and copied runtime config only; product defaults and model inputs stay intact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from prepare_search_reproduction import (
    REPO,
    digest,
    load_workspace,
    save,
    source_identity,
)


def prepare(workspace: Path, image: str) -> None:
    workspace, manifest = load_workspace(workspace)
    graph = yaml.safe_load((workspace / "resolved.yml").read_text())
    if graph["name"] != manifest["project"]:
        raise ValueError("Compose project differs from ownership record")
    # A local tag is mutable; record the exact image the profile was prepared with.
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source = (
        REPO
        / "deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/configs"
    )
    target = workspace / "runtime/pas-eval-configs"
    if target.exists():
        raise FileExistsError(target)
    shutil.copytree(source, target)
    patch = (
        REPO / "deploy/docker/services/rtvi/rtvi-cv/pas-eval/eval-config-i1-i4.patch"
    )
    with patch.open() as stream:
        subprocess.run(
            ["patch", "--fuzz=0", "-p0"], cwd=target, stdin=stream, check=True
        )
    service = graph["services"]["perception-2d-fusion"]
    mounts = [
        m
        for m in service["volumes"]
        if m.get("target", "").endswith("/mounted-configs")
    ]
    if len(mounts) != 1:
        raise ValueError("Expected one RT-CV configuration mount")
    mounts[0]["source"] = str(target)
    service["image"] = image
    # Keep the base preparation's model policy: it disables downloads only when
    # verified --cv-models inputs were copied. Forcing "never" here would leave
    # a workspace prepared without them unable to fetch its detector/encoder.
    # Preserve the original ownership receipt and secret-bearing resolved graph.
    backup = workspace / "before-pas-profile"
    backup.mkdir(mode=0o700)
    for name in ("deployment.json", "resolved.yml"):
        shutil.copy2(workspace / name, backup / name)
        (backup / name).chmod(0o600)
    source_receipt = source_identity()
    for name in ("rtvi-embed", "vss-agent"):
        build = graph["services"][name]["build"]
        build["context"] = str(REPO)
        build["dockerfile_inline"], count = re.subn(
            r"LABEL io\.vss\.source=[0-9a-f]{64}",
            "LABEL io.vss.source=" + source_receipt["sha256"],
            build["dockerfile_inline"],
        )
        if count != 1:
            raise ValueError("Expected a source-labelled application build")
    resolved = workspace / "resolved.yml"
    resolved.write_text(json.dumps(graph, indent=2) + "\n")
    resolved.chmod(0o600)
    manifest["compose_sha256"] = digest(resolved)
    manifest["source"] = source_receipt
    manifest["pas_eval_profile"] = {
        "image": image,
        "image_id": image_id,
        "config_dir": str(target),
        "frame_pool_size": 14,
        "routing_profile": "person-state-photo",
        "configs": {p.name: digest(p) for p in target.iterdir() if p.is_file()},
    }
    save(workspace / "deployment.json", manifest)
    print(
        json.dumps(
            {"project": manifest["project"], "image": image, "config_dir": str(target)}
        )
    )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    prepare(args.workspace, args.image)
