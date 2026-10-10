# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check BCD 3.3 platform configs remain aligned with the frozen workload."""

import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

from generate_bcd_3_3_configs import HERE, PROFILES, SOURCE, render
from perf_utils import calc_vision_tokens

SETUP_BINDINGS = {
    "rtsp_url", "rtvi_backend", "vlm_gpus", "dcgm_exporter_url", "node_exporter_url"
}


def without_setup_bindings(value):
    if isinstance(value, dict):
        return {
            key: without_setup_bindings(item)
            for key, item in value.items()
            if key not in SETUP_BINDINGS
        }
    if isinstance(value, list):
        return [without_setup_bindings(item) for item in value]
    return value


class PlatformConfigTest(unittest.TestCase):
    def test_platform_model_selection_resolves_exact_artifact(self):
        # Exercise setup's real model-selection block without deployment side effects.
        setup = (HERE.parent / "setup_perf_env.sh").read_text()
        selection = setup[
            setup.index("apply_model_preset() {"):
            setup.index('if [[ "${VLLM_ENABLE_PREFIX_CACHING,,}')
        ]
        expected = {
            "h100_sxm": ("super", "fp8"),
            "rtx_pro_6000_se": ("super", "nvfp4"),
            "rtx_pro_4500": ("super", "nvfp4"),
            "b200_sxm": ("super", "nvfp4"),
            "l40s": ("nano", "fp8"),
            "agx_thor_t5000": ("nano", "nvfp4"),
            "dgx_spark": ("nano", "nvfp4"),
            "agx_orin": ("edge", "bf16"),
        }
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "benchmark.yaml"
            for platform, (model, precision) in expected.items():
                config.write_text(render(SOURCE.read_text(), platform, *PROFILES[platform]))
                version = {
                    "fp8": "modelopt-fp8-final_format_fix",
                    "nvfp4": "modelopt-nvfp4-full-quantize-final_format_fix",
                    "bf16": "344d602b128d1bbdacb43b08d0a3626f46343e29",
                }[precision]
                result = subprocess.run(
                    ["bash", "-c", 'set -e; die() { echo "$*" >&2; exit 1; };\n'
                     + selection + '\nprintf "%s\\n%s\\n" "$VLM_MODEL_TO_USE" "$MODEL_PATH"'],
                    env={"PATH": "/usr/bin:/bin", "BENCHMARK_CONFIG": str(config),
                         "VLM_MODEL_TO_USE": "cosmos-reason2", "MODEL_PATH": "stale-cr2-path"},
                    capture_output=True, text=True, check=False, timeout=10,
                )
                with self.subTest(platform=platform):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected_model = [
                        "cosmos-reason3", f"ngc:nim/nvidia/cosmos3-{model}-reasoner:{version}",
                    ] if model != "edge" else [
                        "vllm-compatible", f"git:https://huggingface.co/nvidia/Cosmos3-Edge@{version}",
                    ]
                    self.assertEqual(result.stdout.splitlines(), expected_model)

    def test_explicit_super_path_infers_cr3_backend(self):
        setup = (HERE.parent / "setup_perf_env.sh").read_text()
        function = setup[
            setup.index("infer_vlm_model_from_model_path() {"):
            setup.index('[[ -f "${BENCHMARK_CONFIG}" ]]')
        ]
        for version in ("modelopt-fp8-final_format_fix", "modelopt-nvfp4-full-quantize-final_format_fix"):
            with self.subTest(version=version):
                result = subprocess.run(
                    ["bash", "-c", function + '\ninfer_vlm_model_from_model_path\nprintf "%s" "$VLM_MODEL_TO_USE"'],
                    env={"PATH": "/usr/bin:/bin", "VLM_MODEL_TO_USE": "cosmos-reason2",
                         "MODEL_PATH": f"ngc:nim/nvidia/cosmos3-super-reasoner:{version}"},
                    capture_output=True, text=True, check=True, timeout=10,
                )
                self.assertEqual(result.stdout, "cosmos-reason3")

    def test_tegrastats_fallback_disables_nvml_and_dcgm_but_keeps_node_exporter(self):
        setup = (HERE.parent / "setup_perf_env.sh").read_text()
        validator = re.search(r"python3 -c '([^']+)' \"\$\{BENCHMARK_CONFIG\}\"", setup)
        self.assertIsNotNone(validator)
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "benchmark.yaml"
            for legacy, dcgm, node, expected in (
                (False, False, True, 0),
                (True, False, True, 1),
                (False, True, True, 1),
                (False, False, False, 1),
            ):
                config_path.write_text(yaml.safe_dump({"global": {"gpu_monitoring": {
                    "enabled": legacy,
                    "prometheus": {"enabled": dcgm, "node_exporter_enabled": node},
                }}}))
                result = subprocess.run(
                    [sys.executable, "-c", validator.group(1), str(config_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                with self.subTest(legacy=legacy, dcgm=dcgm, node=node):
                    self.assertEqual(result.returncode, expected, result.stderr)

    def assert_frozen_profile_equal(self, actual, expected):
        self.assertEqual(without_setup_bindings(actual), without_setup_bindings(expected))

    def test_setup_bindings_do_not_change_frozen_profile(self):
        platform = "agx_orin"
        preset, initial, step, levels = PROFILES[platform]
        expected = yaml.safe_load(render(SOURCE.read_text(), platform, preset, initial, step, levels))
        configured = deepcopy(expected)
        configured["global"]["rtvi_backend"] = "http://localhost:9000/v1"
        configured["global"]["vlm_gpus"] = [3]
        configured["test_scenarios"]["max_live_streams_test_100_token_2k"]["videos"][0][
            "rtsp_url"
        ] = "rtsp://localhost:8554/live"
        self.assert_frozen_profile_equal(configured, expected)

        configured["test_scenarios"]["max_live_streams_test_100_token_2k"]["videos"][0][
            "initial_stream_count"
        ] += 1
        with self.assertRaises(AssertionError):
            self.assert_frozen_profile_equal(configured, expected)

    def test_model_specific_vision_token_estimate(self):
        for frames, expected in ((5, 2000), (10, 4000), (20, 8000)):
            self.assertEqual(
                calc_vision_tokens(640, 640, frames, "cosmos3-edge-bf16"), expected
            )
        self.assertEqual(calc_vision_tokens(640, 640, 10, "cr3-nano-reasoner-nvfp4"), 2000)

    def test_cosmos3_edge_frame_budgets(self):
        for platform, expected in (
            ("agx_orin", {"2k": 5, "4k": 10, "8k": 20}),
            ("rtx_pro_6000_se", {"2k": 10, "4k": 20, "8k": 40}),
        ):
            config = yaml.safe_load(
                (HERE / f"rtvi_vlm_bcd_3_3_{platform}_config.yaml").read_text()
            )
            for name, scenario in config["test_scenarios"].items():
                budget = name.rsplit("_", 1)[-1]
                frames = scenario.get("generate_captions_params", {}).get(
                    "num_frames_per_second_or_fixed_frames_chunk",
                    config["global"]["generate_captions_params"]["num_frames_per_second_or_fixed_frames_chunk"],
                )
                for video in scenario["videos"]:
                    actual = video.get("generate_captions_params", {}).get(
                        "num_frames_per_second_or_fixed_frames_chunk", frames
                    )
                    with self.subTest(platform=platform, scenario=name):
                        self.assertEqual(actual, expected[budget])

    def test_platform_profiles(self):
        base = yaml.safe_load(SOURCE.read_text())
        self.assertEqual(len(base["test_scenarios"]), 24)
        self.assertEqual(len(PROFILES), 9)
        self.assertEqual(PROFILES["agx_orin"][0], "cosmos3-edge-bf16")
        self.assertRegex(
            (HERE.parent / "setup_perf_env.sh").read_text(),
            r'cosmos3-edge-bf16\)\s+_preset_model="vllm-compatible"\s+'
            r'_preset_path="git:https://huggingface.co/nvidia/Cosmos3-Edge@'
            r'344d602b128d1bbdacb43b08d0a3626f46343e29"',
        )
        self.assertEqual(
            {platform for platform, (preset, *_rest) in PROFILES.items() if preset.endswith("fp8")},
            {"h100_sxm", "l40s"},
        )
        for platform in ("rtx_pro_4500", "dgx_spark", "agx_thor_t5000", "agx_orin"):
            self.assertLess(PROFILES[platform][3][-1], PROFILES["b200_sxm"][3][-1])
        for platform, (preset, initial, step, levels) in PROFILES.items():
            with self.subTest(platform=platform):
                path = HERE / f"rtvi_vlm_bcd_3_3_{platform}_config.yaml"
                text = path.read_text()
                self.assert_frozen_profile_equal(
                    yaml.safe_load(text),
                    yaml.safe_load(render(SOURCE.read_text(), platform, preset, initial, step, levels)),
                )
                config = yaml.safe_load(text)
                self.assertEqual(config["global"]["model_preset"], preset)
                self.assertEqual(set(config["test_scenarios"]), set(base["test_scenarios"]))
                for name, scenario in config["test_scenarios"].items():
                    for video in scenario["videos"]:
                        if name.startswith("max_live_streams_test_"):
                            self.assertEqual(video["initial_stream_count"], initial)
                            self.assertEqual(video["add_stream_count"], step)
                            self.assertEqual(initial, 1)
                            self.assertEqual(video["latency_plateau_counts"], levels)
                            self.assertEqual(video["latency_plateau_duration_seconds"], 300)
                        elif name.startswith("concurrency_test_"):
                            self.assertEqual(video["stream_count"], levels)
                        elif name.startswith("file_burst_"):
                            self.assertEqual(video["concurrency_levels"], levels)

    def test_perf_compose_mounts_keep_source_and_monitoring_files_valid(self):
        compose = (HERE.parent.parent / "docker/compose.perf.yaml").read_text()
        setup = (HERE.parent / "setup_perf_env.sh").read_text()
        self.assertIn("${RTVI_SRC_DIR:+/rtvi:/opt/nvidia/rtvi/rtvi:ro}", compose)
        self.assertIn("${DCGM_METRICS_CONFIG:-../perf/dcgm/dcgm-metrics-config.csv}", compose)
        self.assertIn("${PROMETHEUS_CONFIG:-./prometheus.perf.yml}", compose)
        self.assertIn('[[ -f "${DCGM_METRICS_CONFIG}" ]]', setup)
        self.assertIn('[[ -f "${PROMETHEUS_CONFIG}" ]]', setup)


if __name__ == "__main__":
    unittest.main()
