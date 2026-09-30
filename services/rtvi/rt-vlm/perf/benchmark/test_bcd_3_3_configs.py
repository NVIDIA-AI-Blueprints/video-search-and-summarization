# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check BCD 3.3 platform configs remain aligned with the frozen workload."""

import unittest

import yaml

from generate_bcd_3_3_configs import HERE, PROFILES, SOURCE, render


class PlatformConfigTest(unittest.TestCase):
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
                self.assertEqual(
                    text, render(SOURCE.read_text(), platform, preset, initial, step, levels)
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


if __name__ == "__main__":
    unittest.main()
