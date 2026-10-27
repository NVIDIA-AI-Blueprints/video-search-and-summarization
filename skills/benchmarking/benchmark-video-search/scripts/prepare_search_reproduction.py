# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare, check, audit and destroy one explicitly owned search deployment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
LOCK = json.loads((HERE / "reproduction-lock.json").read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def command(argv, **kwargs):
    return subprocess.check_output([str(x) for x in argv], text=True, **kwargs).strip()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def require_capacity(path, cold_gib):
    usage = shutil.disk_usage(path)
    # Keep stock Elasticsearch's low watermark available after cold builds/data.
    required = cold_gib * 1024**3 + int(usage.total * 0.15)
    if usage.free < required:
        raise ValueError(
            f"Insufficient capacity on {path}: need {required / 1024**3:.1f} GiB free including 15% filesystem headroom"
        )


def source_identity():
    paths = [
        *HERE.glob("*.py"),
        *HERE.glob("flows/*.py"),
        HERE / "reproduction-lock.json",
        REPO / "libs/vss/uv.lock",
        REPO / "services/agent/uv.lock",
        REPO / "libs/vss/core/pyproject.toml",
        REPO / "libs/vss/cli/pyproject.toml",
    ]
    for root in (
        "libs/vss/core/src",
        "libs/vss/cli/src",
        "services/agent/packages/vss_agents/src",
        "services/rtvi/rt-embed/src",
        "deploy/docker",
    ):
        paths.extend(
            p
            for p in (REPO / root).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and (
                p.name == "Dockerfile"
                or p.suffix
                in {
                    ".py",
                    ".json",
                    ".yml",
                    ".yaml",
                    ".env",
                    ".sh",
                    ".template",
                    ".patch",
                    ".h",
                    ".cpp",
                }
            )
        )
    hashes = {str(p.relative_to(REPO)): digest(p) for p in sorted(set(paths))}
    return {
        "commit": command(["git", "rev-parse", "HEAD"], cwd=REPO),
        "files": hashes,
        "sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
    }


def verify_files(root, expected):
    root = Path(root).resolve(strict=True)
    for name, expected_hash in expected.items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or digest(path) != expected_hash:
            raise ValueError(f"Missing, unexpected or modified model input: {name}")
    return root


def ensure_separate(runtime, protected):
    runtime = Path(runtime).resolve()
    for path in protected:
        path = Path(path).resolve()
        if runtime.is_relative_to(path) or path.is_relative_to(runtime):
            raise ValueError(f"Disposable runtime overlaps a protected path: {path}")
    return runtime


def copy_inputs(root, target, names):
    for name in names:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
        destination.chmod(0o644)


def load_workspace(workspace):
    workspace = Path(workspace).resolve(strict=True)
    manifest = json.loads((workspace / "deployment.json").read_text())
    if (
        manifest["workspace"] != str(workspace)
        or manifest["compose"] != str(workspace / "resolved.yml")
        or manifest["runtime"] != str(workspace / "runtime")
    ):
        raise ValueError("Workspace ownership record does not match its location")
    if manifest["docker_host"] != os.environ.get("DOCKER_HOST", ""):
        raise ValueError("DOCKER_HOST differs from the deployment ownership record")
    ensure_separate(workspace / "runtime", manifest["protected"])
    if digest(workspace / "resolved.yml") != manifest["compose_sha256"]:
        raise ValueError("Generated Compose file changed after preparation")
    return workspace, manifest


def compose(workspace, *args):
    return ["docker", "compose", "-f", str(workspace / "resolved.yml"), *args]


def docker_inventory(project):
    ids = command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ]
    ).split()
    containers = json.loads(command(["docker", "inspect", *ids])) if ids else []
    return {
        "containers": [
            {
                "id": c["Id"],
                "name": c["Name"],
                "image": c["Image"],
                "service": c["Config"]["Labels"]["com.docker.compose.service"],
                "state": c["State"]["Status"],
                "exit_code": c["State"]["ExitCode"],
                "started": c["State"]["StartedAt"],
                "mounts": c["Mounts"],
            }
            for c in containers
        ],
        "volumes": sorted(
            set(
                command(
                    [
                        "docker",
                        "volume",
                        "ls",
                        "-q",
                        "--filter",
                        f"label=com.docker.compose.project={project}",
                    ]
                ).split()
            )
            | {
                m["Name"]
                for c in containers
                for m in c["Mounts"]
                if m["Type"] == "volume"
            }
        ),
        "networks": command(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ]
        ).split(),
    }


def prepare(args):
    workspace = args.workspace.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(
            "Use a new empty workspace; existing deployments are never overwritten"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o700)
    require_capacity(workspace, args.minimum_free_gib)
    if not os.environ.get("NGC_CLI_API_KEY"):
        raise ValueError("Export NGC_CLI_API_KEY before preparing the profile")
    docker_root = Path(
        command(["docker", "info", "--format", "{{.DockerRootDir}}"])
    ).resolve()
    require_capacity(docker_root, args.minimum_free_gib)
    url = urlsplit(args.origin)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.path not in {"", "/"}
        or url.username
        or url.query
        or url.fragment
    ):
        raise ValueError("--origin must be a bare deployment origin")
    if url.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "Use an origin reachable from both host and containers, such as the host's interface IP"
        )
    cosmos = verify_files(args.cosmos_release, LOCK["cosmos_files"])
    cv = verify_files(args.cv_models, LOCK["cv_files"]) if args.cv_models else None
    protected = [str(REPO), str(cosmos)]
    if cv:
        protected.append(str(cv))
    if args.llm_downloads:
        protected.append(str(args.llm_downloads.resolve(strict=True)))
    runtime = ensure_separate(workspace / "runtime", protected)
    project = (
        args.project
        or "vss-repro-" + hashlib.sha256(str(workspace).encode()).hexdigest()[:10]
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]+", project):
        raise ValueError("Invalid project name")
    if any(docker_inventory(project).values()):
        raise ValueError(
            "Project already owns Docker resources; choose a fresh workspace/project"
        )
    source = source_identity()
    copy_inputs(cosmos, runtime / "cosmos", LOCK["cosmos_files"])
    if cv:
        copy_inputs(cv, runtime / "models", LOCK["cv_files"])
    if args.llm_downloads:
        # Checkpoint downloads only: never seed vLLM/Torch/TensorRT compile caches.
        verify_files(args.llm_downloads, LOCK["llm_download_files"])
        hub = args.llm_downloads.resolve() / "hub"
        if not hub.is_dir():
            raise ValueError(
                "--llm-downloads must contain the NGC checkpoint download 'hub' directory"
            )
        shutil.copytree(
            hub,
            runtime / "llm-cache/ngc/hub",
            symlinks=True,
            ignore=shutil.ignore_patterns("tmp", "*.lock"),
        )
    profile = REPO / "deploy/docker/developer-profiles/dev-profile-search"
    notification = json.loads(
        (profile / "vios/configs/notification_config.json").read_text()
    )
    for item in notification["webhooks"]["items"]:
        if item["id"] in {
            "rtvi-cv-camera-streaming",
            "rtvi-embed-camera-streaming",
            "rtvi-vlm-tagging-camera-streaming",
        }:
            item["enabled"] = False
    save(workspace / "notification.json", notification)
    model = LOCK["cosmos_model"]
    origin = args.origin.rstrip("/")
    values = {
        "COMPOSE_PROJECT_NAME": project,
        "VSS_APPS_DIR": str(REPO / "deploy/docker"),
        "VSS_DATA_DIR": str(runtime),
        "HARDWARE_PROFILE": args.hardware_profile,
        "HOST_IP": url.hostname,
        "EXTERNAL_IP": url.hostname,
        "VSS_PUBLIC_HOST": url.hostname,
        "VSS_PUBLIC_PORT": str(url.port or (443 if url.scheme == "https" else 80)),
        "VSS_PUBLIC_HTTP_PROTOCOL": url.scheme,
        "VSS_PUBLIC_WS_PROTOCOL": "wss" if url.scheme == "https" else "ws",
        "HAPROXY_HOST_PORT": str(url.port or 7777),
        "VST_EXTERNAL_URL": origin,
        "VST_BASE_URL": origin,
        "VSS_AGENT_EXTERNAL_URL": origin,
        "VSS_AGENT_REPORTS_BASE_URL": origin + "/static/",
        "RTVI_EMBED_MODEL": model,
        "MODEL_PATH": "/models/cosmos-v1.2/" + model,
        "MODEL_IMPLEMENTATION_PATH": "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1-release",
        "MODEL_REPOSITORY_SCRIPT_PATH": "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1-release/create_triton_model_repo.py",
        "VST_NOTIFICATION_CONFIG_PATH": str(workspace / "notification.json"),
        "INSTALL_PROPRIETARY_CODECS": "true",
        "VLM_BATCH_SIZE": "64",
        "RT_CV_DEVICE_ID": args.cv_gpu,
        "RT_EMBED_DEVICE_ID": args.embed_gpu,
        "LLM_DEVICE_ID": args.embed_gpu,
        "SHARED_LLM_VLM_DEVICE_ID": args.embed_gpu,
        "VLM_DEVICE_ID": args.cv_gpu,
        "SHARED_VLM_DEVICE_ID": args.cv_gpu,
        "RT_VLM_DEVICE_ID": args.cv_gpu,
        "RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.4",
        "NGC_API_KEY": os.environ["NGC_CLI_API_KEY"],
    }
    env = os.environ | values
    argv = ["docker", "compose"]
    for path in (
        REPO / "deploy/docker/containers.env",
        profile / ".env",
        profile / "overrides.env",
    ):
        argv += ["--env-file", str(path)]
    with (workspace / "resolve.log").open("w") as log:
        raw = command(
            [
                *argv,
                "-f",
                REPO / "deploy/docker/compose.yml",
                "config",
                "--no-consistency",
            ],
            env=env,
            stderr=log,
        )
    graph = yaml.safe_load(raw)
    services = graph["services"]
    # Make the profile-filtered graph self-contained using the existing normalizer.
    save(workspace / "resolved.yml", graph)
    subprocess.run(
        [
            sys.executable,
            REPO / "skills/vss-build-vision-ai/scripts/normalize_resolved_yml.py",
            workspace / "resolved.yml",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    graph = yaml.safe_load((workspace / "resolved.yml").read_text())
    graph["name"] = project
    services = graph["services"]
    for name, service in services.items():
        service["platform"] = LOCK["platform"]
        old_name = service.pop("container_name", None)
        if old_name:
            networks = service.setdefault("networks", {"default": {}})
            if isinstance(networks, list):
                networks = {n: {} for n in networks}
                service["networks"] = networks
            entry = networks.get("default") or {}
            entry["aliases"] = sorted(set(entry.get("aliases", []) + [old_name]))
            networks["default"] = entry
        if name in {"rtvi-embed", "vss-agent"}:
            base = LOCK["source_bases"][name]
            copies = (
                "COPY services/rtvi/rt-embed/src/ /opt/nvidia/rtvi/rtvi/\n"
                if name == "rtvi-embed"
                else "COPY services/agent/packages/vss_agents/src/vss_agents/ /vss-agent/.venv/lib/python3.13/site-packages/vss_agents/\n"
                "COPY libs/vss/core/src/vss_core/ /vss-agent/.venv/lib/python3.13/site-packages/vss_core/\n"
                "COPY libs/vss/cli/src/vss_cli/ /vss-agent/.venv/lib/python3.13/site-packages/vss_cli/\n"
            )
            dockerfile = (
                f"FROM {base}\nUSER 0\n{copies}LABEL io.vss.source={source['sha256']}\nUSER "
                + ("1001:1001\n" if name == "rtvi-embed" else "1000:1000\n")
            )
            service["build"] = {"context": str(REPO), "dockerfile_inline": dockerfile}
            service["image"] = f"{project}-{name}:source"
            service["pull_policy"] = "build"
        elif "build" in service:
            build = service["build"]
            path = Path(build["dockerfile"])
            if not path.is_absolute():
                path = Path(build["context"]) / path
            text = path.read_text()
            for tag, pinned in LOCK["build_bases"].items():
                text = re.sub(
                    r"(?m)^(FROM )" + re.escape(tag) + r"(?=\s|$)",
                    lambda m, pinned=pinned: m[1] + pinned,
                    text,
                )
            build.pop("dockerfile", None)
            build["dockerfile_inline"] = text.replace("$", "$$")
            service["image"] = f"{project}-{name}:source"
            service["pull_policy"] = "build"
        else:
            if name not in LOCK["images"]:
                raise ValueError(f"No pinned registry image for service {name}")
            service["image"] = LOCK["images"][name]
        if name == "rtvi-embed":
            service.setdefault("volumes", []).append(
                {
                    "type": "bind",
                    "source": str(runtime / "cosmos"),
                    "target": "/models/cosmos-v1.2",
                    "read_only": True,
                }
            )
        if name == "perception-2d-fusion" and cv:
            # Inputs were verified above; do not download over them.
            service["environment"]["DS_MODEL_DOWNLOAD"] = "never"
        if name == "nemotron-3.5-lightning-30b-a3b-shared-gpu":
            service["environment"]["NIM_MODEL_PROFILE"] = LOCK["llm_profile"]
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                path = Path(mount["source"]).resolve()
                if not (path.is_relative_to(REPO) or path.is_relative_to(workspace)):
                    raise ValueError(f"Undeclared bind input: {path}")
                if not path.exists():
                    path.mkdir(parents=True)
            if "nemotron" in name and mount["target"] == "/opt/nim/.cache":
                mount.update(type="bind", source=str(runtime / "llm-cache"))
                mount.pop("volume", None)
        service.setdefault("labels", {})["io.vss.reproduction"] = str(workspace)
    (runtime / "llm-cache").mkdir(parents=True, exist_ok=True)
    for path in [runtime, *runtime.rglob("*")]:
        if path.is_dir():
            path.chmod(0o777)
    for name, volume in graph.get("volumes", {}).items():
        volume = volume or {}
        volume.pop("external", None)
        volume["name"] = project + "_" + name
        options = volume.get("driver_opts", {})
        if options.get("o") == "bind":
            path = Path(options["device"]).resolve()
            if not path.is_relative_to(runtime):
                raise ValueError("Persistent bind volume must live inside this runtime")
            path.mkdir(parents=True, exist_ok=True)
        graph["volumes"][name] = volume
    for path in [runtime, *runtime.rglob("*")]:
        if path.is_dir():
            path.chmod(0o777)
    for name, network in graph.get("networks", {}).items():
        network = network or {}
        network.pop("external", None)
        network["name"] = project + "_" + name
        graph["networks"][name] = network
    save(workspace / "resolved.yml", graph)
    subprocess.run(compose(workspace, "config", "--quiet"), check=True)
    manifest = {
        "schema_version": 1,
        "workspace": str(workspace),
        "runtime": str(runtime),
        "compose": str(workspace / "resolved.yml"),
        "compose_sha256": digest(workspace / "resolved.yml"),
        "project": project,
        "origin": origin,
        "llm_url": origin + "/llm",
        "llm_model": LOCK["llm_model"],
        "protected": protected,
        "docker_host": os.environ.get("DOCKER_HOST", ""),
        "source": source,
        "inputs": {"cosmos": LOCK["cosmos_files"], "cv": LOCK["cv_files"]},
        "protocol": LOCK["protocol"],
    }
    save(workspace / "deployment.json", manifest)
    print(
        f"Prepared {workspace / 'resolved.yml'}; next: docker compose -f {workspace / 'resolved.yml'} up -d --build"
    )


def check(args):
    workspace, manifest = load_workspace(args.workspace)
    deadline = time.monotonic() + args.deadline
    required = {
        "agent": "/api/v1/version",
        "embedding": "/rtvi-embed/v1/models",
        "CV": "/rtvi-cv/api/v1/stream/get-stream-info",
        "LLM": "/llm/v1/models",
    }
    # Readiness endpoints are declared by the generated search ingress, not guessed from ports.
    pending = dict(required)
    while time.monotonic() < deadline:
        inventory = docker_inventory(manifest["project"])
        expected = len(json.loads((workspace / "resolved.yml").read_text())["services"])
        if len(inventory["containers"]) == expected and all(
            c["state"] == "running" or (c["state"] == "exited" and c["exit_code"] == 0)
            for c in inventory["containers"]
        ):
            pending = {}
            for name, path in required.items():
                try:
                    r = requests.get(manifest["origin"] + path, timeout=10)
                    r.raise_for_status()
                    if name == "LLM" and LOCK["llm_model"] not in {
                        x["id"] for x in r.json()["data"]
                    }:
                        raise ValueError("Routing model identity differs")
                    if name == "embedding" and LOCK["cosmos_model"] not in {
                        x["id"] for x in r.json()["data"]
                    }:
                        raise ValueError("Embedding model identity differs")
                except (requests.RequestException, ValueError, KeyError) as error:
                    pending[name] = str(error)
            if not pending:
                verify_files(workspace / "runtime/models", LOCK["cv_files"])
                verify_files(
                    workspace / "runtime/llm-cache/ngc", LOCK["llm_download_files"]
                )
                for container in inventory["containers"]:
                    if container["service"] in {"rtvi-embed", "vss-agent"}:
                        label = command(
                            [
                                "docker",
                                "image",
                                "inspect",
                                container["image"],
                                "--format",
                                '{{index .Config.Labels "io.vss.source"}}',
                            ]
                        )
                        if label != manifest["source"]["sha256"]:
                            raise RuntimeError(
                                "Deployed application source differs from the prepared branch"
                            )
                    # Profiles prepared before image IDs were recorded have none to check.
                    pas = manifest.get("pas_eval_profile") or {}
                    if (
                        pas.get("image_id")
                        and container["service"] == "perception-2d-fusion"
                        and command(
                            [
                                "docker",
                                "inspect",
                                "--format",
                                "{{.Image}}",
                                container["id"],
                            ]
                        )
                        != pas["image_id"]
                    ):
                        raise RuntimeError(
                            "Running RT-CV image differs from the prepared PAS profile image"
                        )
                    if container["service"] == "rtvi-embed":
                        subprocess.run(
                            [
                                "docker",
                                "cp",
                                f"{container['id']}:/tmp/triton_model_repo/{LOCK['cosmos_model'].lower()}/build.json",
                                str(workspace / "cosmos-engine.json"),
                            ],
                            check=True,
                        )
                save(
                    workspace / "cv-engines.json",
                    {
                        str(path.relative_to(workspace / "runtime/models")): digest(
                            path
                        )
                        for path in (workspace / "runtime/models").rglob("*.engine")
                    },
                )
                save(
                    workspace / "ready.json",
                    {"services": list(required), "inventory": inventory},
                )
                print("Search deployment ready")
                return
        time.sleep(5)
    save(
        workspace / "readiness-failed.json",
        {"pending": pending, "inventory": docker_inventory(manifest["project"])},
    )
    raise RuntimeError(
        "Readiness deadline exceeded; see readiness-failed.json and Compose logs"
    )


def teardown(args):
    workspace, manifest = load_workspace(args.workspace)
    inventory = docker_inventory(manifest["project"])
    members = {c["id"] for c in inventory["containers"]}
    for volume in inventory["volumes"]:
        consumers = set(
            command(
                ["docker", "ps", "-aq", "--no-trunc", "--filter", f"volume={volume}"]
            ).split()
        )
        if consumers - members:
            raise ValueError(
                f"Refusing to delete volume shared outside this project: {volume}"
            )
    for c in inventory["containers"]:
        for mount in c["mounts"]:
            if mount["Type"] == "volume":
                info = json.loads(
                    command(["docker", "volume", "inspect", mount["Name"]])
                )[0]
                owner = (info.get("Labels") or {}).get("com.docker.compose.project")
                if owner not in {None, manifest["project"]}:
                    raise ValueError(
                        "Refusing to delete a volume owned by another project"
                    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "project": manifest["project"],
                    "remove": inventory,
                    "runtime": manifest["runtime"],
                },
                indent=2,
            )
        )
        return
    try:
        from ingest_clip_gallery import audit_indexes

        inventory["index_inventory"] = asyncio.run(audit_indexes(manifest))
    except Exception as error:  # noqa: BLE001 - preserve audit failure while removing owned storage
        # A broken backend must not prevent destruction of its owned storage.
        inventory["index_audit_error"] = f"{type(error).__name__}: {error}"
    save(workspace / "before-teardown.json", inventory)
    with (workspace / "compose.log").open("w") as log:
        subprocess.run(
            compose(workspace, "logs", "--no-color"),
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    subprocess.run(
        compose(workspace, "down", "--volumes", "--remove-orphans"), check=True
    )
    for volume in inventory["volumes"]:
        if (
            subprocess.run(
                ["docker", "volume", "inspect", volume],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            subprocess.run(["docker", "volume", "rm", volume], check=True)
    runtime = ensure_separate(manifest["runtime"], manifest["protected"])
    if runtime.exists():
        # Some containers create root-owned files. Restrict privilege to this exact recorded directory.
        if os.geteuid() == 0:
            shutil.rmtree(runtime)
        else:
            subprocess.run(["sudo", "-n", "rm", "-rf", "--", str(runtime)], check=True)
    remaining = docker_inventory(manifest["project"])
    volume_survivors = [
        v
        for v in inventory["volumes"]
        if subprocess.run(
            ["docker", "volume", "inspect", v],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    ]
    passed = (
        not any(remaining.values()) and not volume_survivors and not runtime.exists()
    )
    save(
        workspace / "teardown.json",
        {
            "passed": passed,
            "removed": inventory,
            "remaining": remaining,
            "surviving_volumes": volume_survivors,
            "runtime_removed": not runtime.exists(),
        },
    )
    if not passed:
        raise RuntimeError("Teardown verification failed")
    print(
        "Verified teardown: profile containers, networks, volumes and bind runtime removed"
    )


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--cosmos-release", type=Path, required=True)
    p.add_argument(
        "--cv-models",
        type=Path,
        help="Optional pre-downloaded CV inputs; otherwise use the profile downloader",
    )
    p.add_argument("--llm-downloads", type=Path)
    p.add_argument("--origin", required=True)
    p.add_argument(
        "--hardware-profile", choices=["RTXPRO6000BW", "H100"], default="RTXPRO6000BW"
    )
    p.add_argument("--cv-gpu", default="0")
    p.add_argument("--embed-gpu", default="1")
    p.add_argument("--project")
    p.add_argument("--minimum-free-gib", type=int, default=200)
    p = sub.add_parser("check")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--deadline", type=float, default=3600)
    p = sub.add_parser("teardown")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    {"prepare": prepare, "check": check, "teardown": teardown}[args.action](args)


if __name__ == "__main__":
    main()
