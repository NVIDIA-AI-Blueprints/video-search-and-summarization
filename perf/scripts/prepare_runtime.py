#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage writable production configs; source files remain immutable."""

import argparse
import configparser
import json
import os
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sensor-json", type=Path)
    args = parser.parse_args()
    root = Path(os.environ["VSS_REPO_ROOT"])
    data = Path(os.environ["VSS_DATA_DIR"])
    runtime_configs = data / "rt-cv-configs"
    runtime_configs.mkdir(parents=True, exist_ok=True)
    if args.sensor_json is None:
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
            target = data / relative
            # Service-owned paths can become private after launch. Never traverse
            # or chmod their descendants when preparing another run.
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o777)
        vios_configs = Path(os.environ.get("VST_CONFIG_PATH", data / "vios-configs"))
        if not vios_configs.exists():
            shutil.copytree(
                Path(os.environ.get("VSS_APPS_DIR", root / "deploy/docker"))
                / "services/vios/configs",
                vios_configs,
            )
        shutil.copytree(
            root / "services/rtvi/rt-cv/reference-configs/warehouse-2d",
            runtime_configs,
            dirs_exist_ok=True,
        )
        pgie = runtime_configs / "ds-ppl-analytics-pgie-config.yml"
        text = pgie.read_text().replace(
            "<PATH_TO_ONNX_MODEL>", "/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx"
        )
        # TensorRT forbids a builder FP16 flag for strongly typed networks.
        # ONNX graph types remain authoritative; mode 0 avoids adding that flag.
        text = text.replace("network-mode: 2", "network-mode: 0")
        pgie.write_text(text)
    else:
        response = json.loads(args.sensor_json.read_text())
        sensor_id = response.get("sensor_id")
        if not isinstance(sensor_id, str) or not sensor_id:
            raise ValueError(
                "VIOS did not return a nonempty sensor_id; cannot provision RT-CV"
            )
        config = configparser.ConfigParser(interpolation=None, strict=False)
        main_config = runtime_configs / "ds-main-config.txt"
        config.read(main_config)
        config["source-list"].update(
            {
                "num-source-bins": "1",
                "list": os.environ["VSS_PERF_STREAM_URL"],
                "sensor-id-list": sensor_id,
                "sensor-name-list": response["name"],
                "http-ip": "0.0.0.0",
            }
        )
        config["sink1"].update(
            {
                "enable": "1",
                "msg-broker-conn-str": "kafka;29092;mdx-raw",
                "msg-conv-payload-type": "2",
            }
        )
        config["application"].update(
            {"enable-perf-measurement": "1", "perf-measurement-interval-sec": "5"}
        )
        with main_config.open("w") as stream:
            config.write(stream, space_around_delimiters=False)


if __name__ == "__main__":
    main()
