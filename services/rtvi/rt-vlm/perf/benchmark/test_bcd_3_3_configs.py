# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check BCD 3.3 platform configs remain aligned with the frozen workload."""

import unittest
from copy import deepcopy

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
