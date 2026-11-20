#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compose-base lifecycle used by the shared job and local runner."""

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import yaml
from promoted_artifact import acquire

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
STATE = ROOT / ".runtime-state.json"
PROJECT = "vss-perf-compose-base"
CORE = ROOT / "benchmark/core/vss_perf_benchmark.py"
PYTHON = ROOT / ".venv/bin/python"


def command(args, *, capture=False, env=None):
    return subprocess.run(args, check=True, capture_output=capture, text=True, env=env)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def context(action):
    config = yaml.safe_load((ROOT / "configs/config.yaml").read_text())
    matrix = yaml.safe_load((ROOT / "configs/perf-configs.yaml").read_text())[
        "perf_configs"
    ]
    if STATE.exists() and (action != "pre_flight" or "--worker" in sys.argv):
        state = json.loads(STATE.read_text())
    else:
        config_id = os.environ.get("CONFIG_ID", matrix[0]["id"])
        entry = next(item for item in matrix if item["id"] == config_id)
        data = Path(os.environ.get("VSS_DATA_DIR", ROOT / ".runtime" / config_id))
        data.mkdir(parents=True, exist_ok=True)
        state = {
            "config_id": config_id,
            "data": str(data),
            "apps": str(REPO / "deploy/docker"),
            "compose": os.environ.get("COMPOSE_FILE")
            or str(REPO / entry["composePath"]),
            "gpu": entry["rtCvGpus"],
            "hardware": entry["hardwareProfile"],
            "origin": config["global"]["vss_backend"],
            "video": os.environ.get(
                "VSS_PERF_VIDEO", str(data / "fixture/Camera_03.mp4")
            ),
            "vios_config": tempfile.mkdtemp(prefix="vios-config-", dir=data),
            "output": os.environ.get("PERF_OUTPUT_DIR", str(ROOT / "results/local")),
            "scenario": os.environ.get("PERF_SCENARIO", "quick_test"),
            "release": os.environ.get("RELEASE", ""),
            "trigger": os.environ.get("TRIGGERED_BY", "manual"),
        }
        write_json(STATE, state)
    return state, config


def environment(state):
    env = os.environ.copy()
    promoted = "env_files" in state
    if promoted:
        for key in list(env):
            if key.startswith("VSS_CONTAINER_") or (
                key.startswith("VSS_")
                and key.endswith(("_IMAGE", "_TAG", "_REGISTRY", "_TAG_SUFFIX"))
            ):
                del env[key]
    else:
        env.setdefault("VSS_CONTAINER_REGISTRY", "nvcr.io/nvstaging/vss-core")
        env.setdefault("VSS_CONTAINER_TAG", "nightly-20261010")
    env.update(
        {
            "VSS_PERF_ROOT": str(ROOT),
            "VSS_REPO_ROOT": str(REPO),
            "VSS_APPS_DIR": state["apps"],
            "VSS_DATA_DIR": state["data"],
            "VST_CONFIG_PATH": state["vios_config"],
            "VST_LOGS": state["data"] + "/data_log/vst",
            "COMPOSE_PROJECT_NAME": PROJECT,
            "COMPOSE_PROFILES": "elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,broker-health-check,redis,centralizedb,sensor-ms,streamprocessing-ms,vst-ingress,logstash,vss-video-analytics-api,vss-haproxy-ingress",
            "STREAM_TYPE": "kafka",
            "HARDWARE_PROFILE": state["hardware"],
            "RT_CV_DEVICE_ID": state["gpu"],
            "HOST_IP": "127.0.0.1",
            "EXTERNAL_IP": "127.0.0.1",
            "VSS_PUBLIC_HOST": "127.0.0.1",
            "VSS_PUBLIC_PORT": "7777",
            "VSS_PUBLIC_HTTP_PROTOCOL": "http",
            "VSS_PUBLIC_WS_PROTOCOL": "ws",
            "NGC_CLI_API_KEY": env.get("NGC_CLI_API_KEY") or env.get("NGC_API_KEY", ""),
            "PATH": str(PYTHON.parent) + os.pathsep + env["PATH"],
        }
    )
    env["VSS_PERF_VIDEO"] = state["video"]
    return env


def docker():
    return (
        ["sudo", "-n", "docker"]
        if os.environ.get("DOCKER_USE_SUDO") == "true"
        else ["docker"]
    )


def compose(state):
    args = docker() + ["compose", "--project-name", PROJECT]
    env_files = state.get(
        "env_files",
        [state["apps"] + "/containers.env", state["apps"] + "/services/vios/vst.env"],
    )
    for path in env_files:
        args += ["--env-file", path]
    args += ["-f", state["compose"]]
    args += ["-f", str(ROOT / "setup/compose/media.yaml")]
    return args


def fixture(state, config, env):
    if Path(state["video"]).is_file():
        return
    if not shutil.which("ngc"):
        raise RuntimeError("NGC CLI is required to acquire the benchmark dataset")
    spec = config["global"]["dataset"]
    download = Path(state["data"]) / "fixture-download"
    download.mkdir(exist_ok=True)
    command(
        ["ngc", "registry", "resource", "download-version"]
        + [spec["resource"], "--dest", str(download)],
        capture=True,
        env=env,
    )
    video = Path(state["video"])
    video.parent.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in download.rglob(video.name)
        if path.as_posix().endswith(spec["video_path"])
    ]
    if len(files) == 1:
        shutil.copyfile(files[0], video)
        return
    for archive in download.rglob("*.tar.gz"):
        with tarfile.open(archive) as source:
            matches = [
                item
                for item in source
                if item.isfile() and item.name.endswith(spec["video_path"])
            ]
            if len(matches) == 1:
                with (
                    source.extractfile(matches[0]) as stream,
                    video.open("wb") as target,
                ):
                    shutil.copyfileobj(stream, target)
                return
    raise RuntimeError("Pinned resource did not contain the expected benchmark video")


def pre_flight(state, config, env):
    command(docker() + ["info"], capture=True)
    command(
        [
            "nvidia-smi",
            "--id=" + state["gpu"],
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
    )
    if not PYTHON.exists():
        command(["uv", "venv", "--python", "3.13", str(PYTHON.parent.parent)])
    if not (REPO / ".git").exists():
        env["SETUPTOOLS_SCM_PRETEND_VERSION"] = "0.0.0"
    command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(PYTHON),
            "-r",
            str(ROOT / "benchmark/requirements.txt"),
            "-e",
            str(REPO / "libs/vss/core"),
            "-e",
            str(REPO / "libs/vss/cli"),
        ],
        env=env,
    )
    artifact = os.environ.get("ARTIFACT_REF") or os.environ.get("IMAGE_TAG")
    if artifact:
        destination = (
            Path(tempfile.mkdtemp(prefix="promoted-", dir=state["data"])) / "payload"
        )
        state.update(acquire(artifact, destination, Path(state["compose"])))
        write_json(STATE, state)
        env = environment(state)
    fixture(state, config, env)
    data = Path(state["data"])
    for relative in (
        "models",
        "data_log/elastic/data",
        "data_log/elastic/logs",
        "data_log/kafka",
        "data_log/redis/data",
        "data_log/redis/log",
        "data_log/vss_video_analytics_api",
        "data_log/vst/nginx_logs",
        "data_log/vst/vst_data",
        "data_log/vst/vst_video",
        "data_log/vst/temp_files",
        "data_log/vst/clip_storage",
    ):
        path = data / relative
        if not path.exists():
            path.mkdir(parents=True)
            path.chmod(0o777)
    shutil.copytree(
        Path(state["apps"]) / "services/vios/configs",
        state["vios_config"],
        dirs_exist_ok=True,
    )
    configs = data / "rt-cv-configs"
    shutil.copytree(
        REPO / "services/rtvi/rt-cv/reference-configs/warehouse-2d",
        configs,
        dirs_exist_ok=True,
    )
    pgie = configs / "ds-ppl-analytics-pgie-config.yml"
    # Strongly typed ONNX forbids the FP16 builder flag; graph FP16 types remain.
    pgie.write_text(
        pgie.read_text()
        .replace(
            "<PATH_TO_ONNX_MODEL>", "/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx"
        )
        .replace("network-mode: 2", "network-mode: 0")
    )
    command(compose(state) + ["config", "--quiet"], env=env)


def deploy(state, config, env):
    # --wait considers explicitly selected exited init containers unhealthy.
    command(
        compose(state) + ["up", "-d", "--build", "kafka-topic-init-container"], env=env
    )
    command(compose(state) + ["wait", "kafka-topic-init-container"], env=env)
    services = [
        "elasticsearch",
        "kafka",
        "redis",
        "centralizedb",
        "sensor-ms",
        "streamprocessing-ms",
        "vst-ingress",
        "logstash",
        "vss-video-analytics-api",
        "vss-haproxy-ingress",
    ]
    services += ["perf-media", "perf-video"]
    command(
        compose(state)
        + ["up", "-d", "--build", "--wait", "--wait-timeout"]
        + [str(config["global"]["stage_timeouts"]["deploy"] * 60), *services],
        env=env,
    )


def inference_ready():
    while True:
        status = command(
            docker() + ["inspect", "--format", "{{.State.Status}}", "vss-rtvi-cv"],
            capture=True,
        ).stdout.strip()
        if status not in ("running", "restarting"):
            raise RuntimeError("RT-CV stopped before inference became ready")
        logs = command(
            docker() + ["logs", "--tail", "300", "vss-rtvi-cv"], capture=True
        )
        active = False
        for line in (logs.stdout + logs.stderr).splitlines():
            if "**PERF:" in line or re.search(r"PERF\(\d+\):", line):
                active = True
            elif "Active sources :" in line:
                active = False
            if active and any(
                float(value) > 0
                for value in re.findall(r"([0-9]+\.[0-9]+)\s*\([0-9]+\.[0-9]+\)", line)
            ):
                return
        time.sleep(5)


def post_deploy(state, config, env):
    vss = str(PYTHON.parent / "vss")
    command([vss, "configure", "--base-url", state["origin"]], env=env)
    command([vss, "configure", "check"], env=env)
    command(
        [
            vss,
            "vios",
            "add",
            "--type",
            "stream",
            "rtsp://perf-media:8554/compose-base",
            "--name",
            "compose-base-perf",
        ],
        env=env,
    )
    sensors = json.loads(
        command(
            [vss, "vios", "list", "--type", "stream", "--sensor", "compose-base-perf"],
            capture=True,
            env=env,
        ).stdout
    )["sensors"]
    if len(sensors) != 1:
        raise RuntimeError("Benchmark stream listing must contain exactly one sensor")
    sensor = sensors[0]
    if not sensor.get("sensor_id"):
        raise RuntimeError("VIOS did not return a sensor identifier")
    path = Path(state["data"]) / "rt-cv-configs/ds-main-config.txt"
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path)
    parser["source-list"].update(
        {
            "num-source-bins": "1",
            "list": "rtsp://perf-media:8554/compose-base",
            "sensor-id-list": sensor["sensor_id"],
            "sensor-name-list": sensor["name"],
            "http-ip": "0.0.0.0",
        }
    )
    parser["sink1"].update(
        {
            "enable": "1",
            "msg-broker-conn-str": "kafka;29092;mdx-raw",
            "msg-conv-payload-type": "2",
        }
    )
    with path.open("w") as stream:
        parser.write(stream, space_around_delimiters=False)
    command(compose(state) + ["up", "-d", "rtvi-cv"], env=env)
    inference_ready()
    capture(state)
    command([vss, "configure", "--base-url", state["origin"]], env=env)


def capture(state):
    ids = command(
        docker()
        + ["ps", "--all", "--quiet", "--filter"]
        + ["label=com.docker.compose.project=" + PROJECT],
        capture=True,
    ).stdout.split()
    containers = json.loads(command(docker() + ["inspect", *ids], capture=True).stdout)
    deployed = {row["Name"].lstrip("/"): row["Image"] for row in containers}
    media = next(
        row["Id"]
        for row in containers
        if row["Config"]["Labels"].get("com.docker.compose.service") == "perf-video"
    )
    probe = json.loads(
        command(
            docker()
            + [
                "exec",
                media,
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate:format=duration",
                "-of",
                "json",
                "/fixture/video.mp4",
            ],
            capture=True,
        ).stdout
    )
    with Path(state["video"]).open("rb") as source:
        probe["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
    metadata = {"fixture": probe, "deployed_images": deployed}
    if "promoted_artifact" in state:
        metadata["promoted_artifact"] = state["promoted_artifact"]
    write_json(Path(state["data"]) / "metadata.json", metadata)


def teardown(state, config, env):
    command(compose(state) + ["--profile", "*", "down", "--timeout", "30"], env=env)
    label = "label=com.docker.compose.project=" + PROJECT
    for args in (["ps", "--all"], ["network", "ls"]):
        if command(
            docker() + [*args, "--quiet", "--filter", label], capture=True
        ).stdout.strip():
            raise RuntimeError("Benchmark project still owns containers or networks")
    volumes = command(
        docker()
        + ["volume", "ls", "--quiet", "--filter"]
        + [label, "--filter", "label=com.docker.compose.volume=vios_pg_data"],
        capture=True,
    ).stdout.split()
    if volumes:
        command(docker() + ["volume", "rm", *volumes])


def benchmark_args(state):
    args = [str(PYTHON), str(CORE)]
    for flag, key in (
        ("--config-id", "config_id"),
        ("--scenario", "scenario"),
        ("--output-json", "output"),
        ("--release", "release"),
        ("--triggered-by", "trigger"),
    ):
        args += [flag, state[key]]
    if os.environ.get("UPLOAD_TO_MINIO") == "true":
        args += ["--upload"]
    return args


def stage(action):
    state, config = context(action)
    env = environment(state)
    if "--worker" in sys.argv:
        {
            "pre_flight": pre_flight,
            "deploy": deploy,
            "post_deploy": post_deploy,
            "teardown": teardown,
        }[action](state, config, env)
        return 0
    # GNU timeout bounds the worker's whole process group, including child commands.
    worker = [sys.executable, __file__, action, "--worker"]
    limit = f"{config['global']['stage_timeouts'][action]}m"
    status = subprocess.run(
        ["timeout", "--kill-after=5s", limit, *worker],
        env=env,
        check=False,
    ).returncode
    if (
        status
        and action != "teardown"
        and os.environ.get("VSS_PERF_CORE_CLEANUP") != "true"
    ):
        subprocess.run(
            benchmark_args(state)
            + [
                "--stage-failure",
                action,
                "--failure-message",
                f"{action} exited {status}",
            ],
            env=env,
            check=False,
        )
    return status if status >= 0 else 1


def main():
    action = sys.argv[1]
    if action != "run":
        return stage(action)
    parser = argparse.ArgumentParser(
        description="Deploy, benchmark and clean up compose-base"
    )
    parser.add_argument("-s", "--scenario", default="quick_test")
    parser.add_argument("-O", "--output-json", default=str(ROOT / "results/local"))
    parser.add_argument("-r", "--release", default=os.environ.get("RELEASE", ""))
    parser.add_argument("-d", action="store_true", help="Cleanup is always performed")
    args = parser.parse_args(sys.argv[2:])
    os.environ.update(
        PERF_SCENARIO=args.scenario,
        PERF_OUTPUT_DIR=args.output_json,
        RELEASE=args.release,
    )
    for action in ("pre_flight", "deploy", "post_deploy"):
        status = stage(action)
        if status:
            return status
    state, _ = context("benchmark")
    return subprocess.run(
        benchmark_args(state), env=environment(state), check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
