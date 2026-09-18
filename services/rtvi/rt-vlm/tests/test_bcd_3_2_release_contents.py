######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
######################################################################################################

import os
import subprocess
from pathlib import Path

import yaml


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_bcd_3_2_vlm_release_is_complete():
    required_paths = [
        "docker/compose.perf.yaml",
        "docker/prometheus.perf.yml",
        "perf/setup_perf_env.sh",
        "perf/teardown_perf_env.sh",
        "perf/benchmark/base.py",
        "perf/benchmark/concurrent_live_streams_benchmark.py",
        "perf/benchmark/file_burst_benchmark.py",
        "perf/benchmark/generate_perf_xlsx.py",
        "perf/benchmark/live_streams_benchmark.py",
        "perf/benchmark/rtvi_perf_benchmark.py",
        "perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml",
        "perf/benchmark/rtvi_vlm_bcd_3_2_spark_config.yaml",
        "perf/benchmark/rtvi_vlm_bcd_3_2_thor_config.yaml",
    ]
    assert not [path for path in required_paths if not (SERVICE_ROOT / path).is_file()]

    config = yaml.safe_load(
        (SERVICE_ROOT / "perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml").read_text()
    )
    expected_scenarios = {
        f"{family}_{output_tokens}_token_{token_tier}"
        for family in (
            "max_live_streams_test",
            "concurrency_test",
            "file_burst",
            "e2e_latency",
        )
        for output_tokens in (1, 100)
        for token_tier in ("2k", "4k", "8k")
    }
    assert set(config["test_scenarios"]) == expected_scenarios


def test_perf_compose_tracks_github_runtime_controls():
    compose = yaml.safe_load((SERVICE_ROOT / "docker/compose.perf.yaml").read_text())
    image = compose["services"]["rtvi-server"]["image"]
    environment = compose["services"]["rtvi-server"]["environment"]

    assert image == (
        "${RTVI_IMAGE:-ghcr.io/nvidia-ai-blueprints/vss/"
        "vss-rt-vlm:develop-latest}"
    )

    expected = {
        "MAX_ASSET_STORAGE_SIZE_GB": "${MAX_ASSET_STORAGE_SIZE_GB:-}",
        "ASSET_MAX_AGE_HOURS": "${ASSET_MAX_AGE_HOURS:-0}",
        "ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS": "${ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS:-}",
        "ASSET_DOWNLOAD_MAX_REDIRECTS": "${ASSET_DOWNLOAD_MAX_REDIRECTS:-0}",
        "ASSET_DOWNLOAD_AUTH_TOKENS": "${ASSET_DOWNLOAD_AUTH_TOKENS:-}",
        "RTVI_ENABLE_GOP_DECODE_OPT": "${RTVI_ENABLE_GOP_DECODE_OPT:-true}",
        "VLM_USE_FPS_FOR_CHUNKING": "${VLM_USE_FPS_FOR_CHUNKING:-}",
        "TORCH_CUDNN_V8_API_DISABLED": "${TORCH_CUDNN_V8_API_DISABLED:-false}",
        "VLLM_MM_PROCESSOR_CACHE_GB": "${RTVI_VLLM_MM_PROCESSOR_CACHE_GB:-0}",
        "VLLM_MOE_BACKEND": "${RTVI_VLLM_MOE_BACKEND:-}",
        "VLM_MAX_GENERATION_TOKENS": "${RTVI_VLM_MAX_GENERATION_TOKENS:-16384}",
        "KAFKA_ASYNC_SEND_QUEUE_MAXSIZE": "${RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE:-1024}",
        "VLLM_USE_STANDALONE_COMPILE": "${VLLM_USE_STANDALONE_COMPILE:-1}",
    }
    assert {key: environment.get(key) for key in expected} == expected

    setup = (SERVICE_ROOT / "perf/setup_perf_env.sh").read_text()
    assert 'RTVI_IMAGE="${RTVI_IMAGE:-}"' in setup
    assert setup.index('if [[ -z "${RTVI_IMAGE}" ]]') > setup.index('PLATFORM="unknown"')
    assert 'if [[ "${PLATFORM}" == "dgx_spark" ]]; then' in setup
    assert (
        'RTVI_IMAGE="ghcr.io/nvidia-ai-blueprints/vss/'
        'vss-rt-vlm:develop-latest-sbsa"' in setup
    )
    assert (
        'RTVI_IMAGE="ghcr.io/nvidia-ai-blueprints/vss/'
        'vss-rt-vlm:develop-latest"' in setup
    )
    assert '[[ -n "${RTVI_IMAGE:-}" ]]' not in setup
    setup_keys = (
        "MAX_ASSET_STORAGE_SIZE_GB",
        "ASSET_MAX_AGE_HOURS",
        "ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS",
        "ASSET_DOWNLOAD_MAX_REDIRECTS",
        "ASSET_DOWNLOAD_AUTH_TOKENS",
        "RTVI_ENABLE_GOP_DECODE_OPT",
        "VLM_USE_FPS_FOR_CHUNKING",
        "TORCH_CUDNN_V8_API_DISABLED",
        "RTVI_VLLM_MM_PROCESSOR_CACHE_GB",
        "RTVI_VLLM_MOE_BACKEND",
        "RTVI_VLM_MAX_GENERATION_TOKENS",
        "RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE",
    )
    assert all(f"\n{key}=" in setup for key in setup_keys)
    assert "\nVLLM_MM_PROCESSOR_CACHE_GB=" not in setup


def test_setup_derives_bcd_videos_from_lvs_source():
    setup = (SERVICE_ROOT / "perf/setup_perf_env.sh").read_text()

    assert "benchmark-video-summarization/scripts/fetch-videos.sh" in setup
    assert "\nVIDEOS_URL=" in setup
    assert (
        'LVS_VIDEO_SOURCE_PATH="${LVS_VIDEO_DATA_DIR}/videos/warehouse_10min.mp4"'
        in setup
    )
    assert '-stream_loop -1' in setup
    assert '-vf fps=10' in setup
    assert 'validate_bcd_video "${dest}" "${duration}"' in setup
    assert '_bcd_10s_tmp="${_bcd_10s_dest}.tmp.mp4"' in setup
    assert 'generate_bcd_video 10 "${BCD_10S_VIDEO_FILENAME}"' in setup
    assert 'generate_bcd_video 600 "${BCD_10M_VIDEO_FILENAME}"' in setup
    assert 'generate_bcd_video 3600 "${BCD_60M_VIDEO_FILENAME}"' in setup
    assert 'download_video "${BCD_10M_VIDEO_FILENAME}"' not in setup
    assert 'download_video "${BCD_60M_VIDEO_FILENAME}"' not in setup


def test_bcd_video_validation_rejects_wrong_duration(tmp_path):
    setup = (SERVICE_ROOT / "perf/setup_perf_env.sh").read_text()
    start = setup.index("validate_bcd_video() {")
    end = setup.index("\n}\n\n# Returns", start) + 3
    validator = setup[start:end]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ffprobe = bin_dir / "ffprobe"
    ffprobe.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"stream=width,height"*) echo 1920x1080 ;;\n'
        '  *"stream=avg_frame_rate"*) echo 10/1 ;;\n'
        '  *"format=duration"*) echo "${FAKE_DURATION:-10}" ;;\n'
        "esac\n"
    )
    ffprobe.chmod(0o755)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    command = f'{validator}\nvalidate_bcd_video "{video}" 10'

    subprocess.run(["bash", "-c", command], check=True, env=env)
    env["FAKE_DURATION"] = "9"
    assert subprocess.run(["bash", "-c", command], env=env).returncode != 0
