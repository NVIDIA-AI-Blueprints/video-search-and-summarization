#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage the repository's single-pod VIOS deployment without downloading a package."""

import json
import os
from pathlib import Path
import re
import shutil


def update_env(path, values):
    text = path.read_text()
    for key, value in values.items():
        value = str(value)
        if any(char in value for char in "\n\r$'\"#"):
            raise ValueError(f"Unsupported character in {key}")
        line = f"{key}={value}"
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, text, flags=re.M):
            text = re.sub(pattern, lambda _: line, text, flags=re.M)
        else:
            text += "\n" + line + "\n"
    path.write_text(text)


def stage():
    perf = Path(__file__).resolve().parent
    source = perf.parents[2] / "vios/deployment/stream-processing/docker-compose"
    root = Path(os.environ["VST_DIR"]).resolve()
    videos = Path(os.environ.get("PERF_VIDEOS_DIR", root / "videos")).resolve()
    project = os.environ.get("VST_COMPOSE_PROJECT", "rtvi-perf-vst")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("VST_COMPOSE_PROJECT must be a lowercase Compose project name")
    for name in ("stream-processing", "nvstreamer", "scripts", "deploy.sh"):
        if (root / name).exists() or (root / name).is_symlink():
            raise ValueError(f"Use a fresh VST_DIR; refusing to overwrite {root / name}")
    ports = {}
    for key, default in (
        ("VST_SENSOR_PORT", 30000), ("VST_STREAM_PROC_PORT", 30001),
        ("VST_INGRESS_PORT", 30888), ("VST_RTSP_PORT", 30554),
        ("NVSTREAMER_HTTP_PORT", 31000), ("NVSTREAMER_RTSP_PORT", 31554),
        ("REDIS_PORT", 6379), ("CENTRALIZE_DB_PORT", 5432),
    ):
        ports[key] = int(os.environ.get(key, default))
        if not 1 <= ports[key] <= 65535:
            raise ValueError(f"Invalid port: {key}")
    root.mkdir(parents=True, exist_ok=True)
    stream = root / "stream-processing"
    nvstreamer = root / "nvstreamer"
    shutil.copytree(source, stream, ignore=shutil.ignore_patterns("nvstreamer"))
    shutil.copytree(source / "nvstreamer", nvstreamer)
    # The nvstreamer Compose file mounts ../scripts; keep that relative path valid.
    shutil.copytree(source / "scripts", root / "scripts")
    shutil.copyfile(perf / "deploy_vst.sh", root / "deploy.sh")
    (root / ".compose-project").write_text(project + "\n")
    update_env(stream / "compose.env", {
        "HOST_IP": "127.0.0.1", "VST_CONFIG_PATH": stream / "configs",
        "VST_VOLUME": root / "vst_volume", "VST_USE_SDRC": "false",
        "COMPOSE_PROFILES": "", "NGINX_MODE": "vst",
        "VST_ENABLE_NOTIFICATION": "false",
        "STREAM_PROCESSOR_MODULE_ENDPOINT": f"http://127.0.0.1:{ports['VST_STREAM_PROC_PORT']}",
        "SENSOR_MODULE_ENDPOINT": f"http://127.0.0.1:{ports['VST_SENSOR_PORT']}",
        "SENSOR_HTTP_PORT": ports["VST_SENSOR_PORT"],
        "STREAM_PROCESSOR_HTTP_PORT_1": ports["VST_STREAM_PROC_PORT"],
        "RTSP_SERVER_PORT_1": ports["VST_RTSP_PORT"],
        "VST_INGRESS_ENDPOINT": f"127.0.0.1:{ports['VST_INGRESS_PORT']}/vst",
        "CENTRALIZE_DB_PORT": ports["CENTRALIZE_DB_PORT"],
        "REDIS_PORT": ports["REDIS_PORT"],
    })
    update_env(nvstreamer / "compose.env", {
        "NVSTREAMER_CONFIG": nvstreamer / "configs",
        "NVSTREAMER_VIDEO_1": videos, "COMPOSE_PROFILES": "nvstreamer-1",
        "NVSTREAMER_HTTP_PORT_1": ports["NVSTREAMER_HTTP_PORT"],
        "NVSTREAMER_RTSP_PORT_1": ports["NVSTREAMER_RTSP_PORT"],
    })
    # Apply the same public image defaults as setup, also for standalone staging.
    registry = os.environ.get("VST_IMAGE_REGISTRY", "nvcr.io/nvidia/vss-core")
    tag = os.environ.get("VST_IMAGE_TAG", "3.2.0")
    for directory, key, override, image in (
        (stream, "VST_STREAM_PROCESSOR_IMAGE", "VST_STREAMPROCESSING_IMAGE", "streamprocessing"),
        (stream, "VST_SENSOR_IMAGE", "VST_SENSOR_IMAGE", "sensor"),
        (stream, "NGINX_IMAGE", "VST_INGRESS_IMAGE", "ingress"),
        (nvstreamer, "NVSTREAMER_IMAGE", "VST_NVSTREAMER_IMAGE", "nvstreamer"),
    ):
        update_env(directory / "compose.env", {
            key: os.environ.get(override, f"{registry}/vss-vios-{image}:{tag}")
        })
    for compose in root.rglob("*.yaml"):
        text = compose.read_text()
        text = re.sub(r"(container_name: )([\w-]+)", rf"\g<1>{project}-\2", text)
        text = text.replace("name: vios_apt_cache", f"name: {project}-apt-cache")
        # The optional UI-prefix helper is not shipped by older public images.
        text = text.replace(
            "/home/vst/vst_release/tools/configure_nvstreamer_ui.sh && exec",
            "if [ -x /home/vst/vst_release/tools/configure_nvstreamer_ui.sh ]; then "
            "/home/vst/vst_release/tools/configure_nvstreamer_ui.sh; fi && exec",
        )
        text = text.replace("127.0.0.1/30888", f"127.0.0.1/{ports['VST_INGRESS_PORT']}")
        text = text.replace("pg_isready -h 127.0.0.1", "pg_isready -h 127.0.0.1 -p ${CENTRALIZE_DB_PORT:-5432}")
        compose.write_text(text)
    nginx = stream / "configs/nginx-vst.conf"
    text = nginx.read_text().replace("listen 30888;", f"listen {ports['VST_INGRESS_PORT']};")
    for default, key in ((30000, "VST_SENSOR_PORT"), (30001, "VST_STREAM_PROC_PORT")):
        text = text.replace(f"127.0.0.1:{default}", f"127.0.0.1:{ports[key]}")
    nginx.write_text(text)
    postgres = stream / "configs/postgresql.conf"
    postgres.write_text(re.sub(r"^port\s*=.*$", f"port = {ports['CENTRALIZE_DB_PORT']}",
                               postgres.read_text(), flags=re.M))
    for directory, http, rtsp in (
        (stream, "VST_SENSOR_PORT", "VST_RTSP_PORT"),
        (nvstreamer, "NVSTREAMER_HTTP_PORT", "NVSTREAMER_RTSP_PORT"),
    ):
        config = directory / "configs/vst_config.json"
        data = json.loads(config.read_text())
        data["network"]["http_port"] = str(ports[http])
        data["network"]["rtsp_server_port"] = ports[rtsp]
        config.write_text(json.dumps(data, indent=2) + "\n")
    sources = stream / "configs/rtsp_streams.json"
    data = json.loads(sources.read_text())
    data["Nvstreamer"] = [{
        "enabled": True, "endpoint": f"localhost:{ports['NVSTREAMER_HTTP_PORT']}",
        "api": "/api/v1/sensor/streams", "max_stream_count": 100,
    }]
    sources.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Staged repository VST deployment at {root}; no package download required")


if __name__ == "__main__":
    stage()
