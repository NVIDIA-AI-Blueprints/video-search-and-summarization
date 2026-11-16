# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hardware expansion must preserve the native workload and enable all requested targets."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SERVICES = {'behavior-analytics': 'warehouse_2d-kafka-playback', 'video-analytics-api': 'warehouse-es-dump'}
ARCHITECTURES = {'RTXPRO6000BW-SE': 'amd64', 'H100-SXM': 'amd64',
                 'RTXPRO4500BW': 'amd64', 'DGX-SPARK': 'arm64-igpu',
                 'IGX-THOR': 'arm64-igpu', 'GB300': 'arm64-igpu'}


class AnalyticsHardwareMatrixTests(unittest.TestCase):
    def test_hardware_rows_have_unique_ids_and_preserve_baseline_inputs(self):
        for service, baseline_id in SERVICES.items():
            with self.subTest(service=service):
                service_root = ROOT / 'services/analytics' / service
                configs = yaml.safe_load((service_root / 'perf/configs/perf-configs.yaml').read_text())['perf_configs']
                self.assertEqual(len(configs), 6)
                self.assertEqual(len({cfg['id'] for cfg in configs}), 6)
                self.assertEqual({cfg['nodeLabel'] for cfg in configs}, set(ARCHITECTURES))
                baseline = next(cfg for cfg in configs if cfg['id'] == baseline_id)
                self.assertEqual(baseline['nodeLabel'], 'RTXPRO6000BW-SE')
                self.assertTrue(baseline['enabled'])
                workload = lambda cfg: {k: v for k, v in cfg.items() if k not in {'id', 'nodeLabel', 'imageArch', 'enabled'}}
                for cfg in configs:
                    self.assertEqual(cfg['imageArch'], ARCHITECTURES[cfg['nodeLabel']])
                    self.assertEqual(workload(cfg), workload(baseline))
                    self.assertTrue(cfg['enabled'], 'All requested hardware targets must be enabled')
                    self.assertTrue((service_root / cfg['composePath']).is_file())
                    for path in cfg['checkoutPaths']:
                        self.assertTrue((ROOT / path).exists(), path)
                    for path in cfg['lfsPaths']:
                        self.assertTrue((ROOT / path).is_file(), path)

    def test_enabled_rows_declare_supported_image_architectures(self):
        for service in SERVICES:
            configs = yaml.safe_load((ROOT / 'services/analytics' / service / 'perf/configs/perf-configs.yaml').read_text())['perf_configs']
            for cfg in configs:
                if cfg['enabled']:
                    self.assertIn(cfg['imageArch'], {'amd64', 'arm64-sbsa', 'arm64-igpu'})


if __name__ == '__main__':
    unittest.main()
