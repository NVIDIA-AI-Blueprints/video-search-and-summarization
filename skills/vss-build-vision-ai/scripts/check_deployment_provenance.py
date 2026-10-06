#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record actual Compose images and distinguish Alert API failures from routing failures.

Standard library only. Never serialize Compose environments, container environments,
full image metadata, API response bodies, or credentials into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_LABELS = (
    "org.opencontainers.image.revision",
    "org.opencontainers.image.version",
    "com.nvidia.vss.source_tree_sha",
)
CONFIG_PATH = "/api/v1/verification/config"


def command(*args: str) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        # stderr can include interpolated Compose values; keep it out of artifacts.
        raise RuntimeError(f"{args[0]} command failed (exit {result.returncode})")
    return result.stdout


class NoRedirect(HTTPRedirectHandler):
    """Do not mistake a login/catch-all redirect for the requested API."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_config(origin: str) -> dict:
    result = {"http_status": None, "config_api": False}
    try:
        request = Request(
            origin.rstrip("/") + CONFIG_PATH, headers={"Accept": "application/json"}
        )
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            result["http_status"] = response.status
            body = json.loads(response.read(1024 * 1024))
            result["config_api"] = (
                response.status == 200
                and isinstance(body, dict)
                and body.get("status") == "success"
                and isinstance(body.get("configs"), list)
                and isinstance(body.get("count"), int)
            )
    except HTTPError as error:
        result["http_status"] = error.code
    except (URLError, TimeoutError, OSError, ValueError):
        pass
    return result


def collect(repo: Path, resolved: Path, expected_sha: str | None) -> dict:
    head = command("git", "-C", str(repo), "rev-parse", "HEAD").strip()
    report = {
        "schema_version": 1,
        "checkout_sha": head,
        "expected_checkout_sha": expected_sha,
        "resolved_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "containers": [],
        "errors": [],
    }
    if expected_sha and head != expected_sha:
        report["errors"].append("worker checkout does not match expected eval SHA")
    compose = ("docker", "compose", "-f", str(resolved))
    services = set(command(*compose, "config", "--services").splitlines())
    ids = command(*compose, "ps", "--all", "--quiet").splitlines()
    if not ids:
        report["errors"].append("no containers found for resolved Compose project")
        return report
    seen = set()
    # Inspect the immutable image ID used by the container, not its mutable tag.
    for container in json.loads(command("docker", "inspect", *ids)):
        service = (container["Config"].get("Labels") or {}).get(
            "com.docker.compose.service"
        )
        seen.add(service)
        image_id = container["Image"]
        image = json.loads(command("docker", "image", "inspect", image_id))[0]
        labels = image["Config"].get("Labels") or {}
        report["containers"].append(
            {
                "service": service,
                "container_name": container["Name"].lstrip("/"),
                "image_reference": container["Config"]["Image"],
                "image_id": image_id,
                "repo_digests": image.get("RepoDigests") or [],
                "source_labels": {
                    key: labels[key] for key in SOURCE_LABELS if key in labels
                },
            }
        )
    for service in sorted(services - seen):
        report["errors"].append(f"no container found for resolved service {service}")
    return report


def check_alert_api(report: dict, direct: str, ingress: str | None) -> None:
    probes = {"direct": probe_config(direct)}
    if ingress:
        probes["ingress"] = probe_config(ingress)
    report["alert_config_api"] = probes
    if not probes["direct"]["config_api"]:
        report["errors"].append(
            "Alert configuration API unavailable directly; inspect image provenance and service logs"
        )
    elif ingress and not probes["ingress"]["config_api"]:
        report["errors"].append(
            "Alert configuration API works directly but fails through ingress; inspect routing"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--resolved", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expected-checkout-sha", default=os.environ.get("PR_HEAD_SHA") or None
    )
    parser.add_argument(
        "--alert-direct-origin",
        help="Documented Alert MS origin, only if alert-bridge is selected",
    )
    parser.add_argument(
        "--alert-ingress-origin",
        help="Documented ingress origin including /alert-bridge",
    )
    args = parser.parse_args()
    if args.alert_ingress_origin and not args.alert_direct_origin:
        parser.error("--alert-ingress-origin requires --alert-direct-origin")
    report = {"schema_version": 1, "errors": []}
    try:
        report = collect(args.repo_root, args.resolved, args.expected_checkout_sha)
        has_alert = any(c["service"] == "alert-bridge" for c in report["containers"])
        if has_alert and not args.alert_direct_origin:
            report["errors"].append(
                "alert-bridge requires --alert-direct-origin for the API gate"
            )
        elif has_alert:
            check_alert_api(report, args.alert_direct_origin, args.alert_ingress_origin)
    except (
        RuntimeError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        report["errors"].append(
            f"provenance collection failed ({type(error).__name__})"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as output:
        os.chmod(args.output, 0o600)
        json.dump(report, output, indent=2)
        output.write("\n")
    for error in report["errors"]:
        print(f"FAIL: {error}")
    print(f"Deployment provenance: {args.output}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
