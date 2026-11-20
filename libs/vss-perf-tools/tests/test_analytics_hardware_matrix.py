# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hardware expansion must preserve the native workload and enable all requested targets."""
from pathlib import Path
from copy import deepcopy
import importlib.util
import sys
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

    def test_every_enabled_hardware_resolves_and_enforces_the_existing_kpis(self):
        for service, baseline_id in SERVICES.items():
            root = ROOT / 'services/analytics' / service / 'perf'
            module_spec = importlib.util.spec_from_file_location(service + '_hardware_kpis', root / 'benchmark/kpis.py')
            evaluator = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(evaluator)
            configs = yaml.safe_load((root / 'configs/perf-configs.yaml').read_text())['perf_configs']
            scenarios = yaml.safe_load((root / 'configs/config.yaml').read_text())['test_scenarios']
            definitions = evaluator.load_kpis()
            # Both supported loading paths must retain the full hardware mappings.
            self.assertEqual(definitions, evaluator._load_simple_yaml((root / 'configs/kpis.yaml').read_text()))
            sys.path.insert(0, str(root / 'benchmark'))
            try:
                for scenario, options in scenarios.items():
                    if options.get('enabled', True) is False:
                        continue
                    baseline = definitions['scenarios'][scenario][baseline_id]
                    for cfg in configs:
                        if not cfg['enabled']:
                            continue
                        with self.subTest(service=service, hardware=cfg['nodeLabel'], scenario=scenario):
                            self.assertEqual(definitions['scenarios'][scenario][cfg['id']], baseline)
                            payload = {'scenario': scenario, 'config_id': cfg['id']}
                            if service == 'video-analytics-api':
                                payload['endpoints'] = {
                                    name: dict(present=True, count=20, ok_count=20, ok_ratio=1.0,
                                               avg_s=0.0, p50_s=0.0, p95_s=0.0, p99_s=0.0, max_s=0.0)
                                    for name in evaluator.resolved_required_endpoints(baseline)}
                                bad = deepcopy(payload)
                                bad['endpoints']['frames']['p50_s'] = baseline['frames']['max_p50_s'] + 1
                            else:
                                sample_count = max(limits.get('min_count', 0) for limits in baseline.values() if isinstance(limits, dict))
                                payload['raw'] = {'count': sample_count}
                                payload['topics'] = {
                                    topic: {'present': True, 'corrected': dict(count=sample_count, avg_s=0.0,
                                              p50_s=0.0, p95_s=0.0, p99_s=0.0, max_s=0.0)}
                                    for topic in baseline['required_topics'] if topic != 'raw'}
                                bad = deepcopy(payload)
                                bad['topics']['frames']['corrected']['p50_s'] = baseline['frames']['max_p50_s'] + 1
                            good_verdict = evaluator.evaluate_kpis(payload, definitions)
                            self.assertTrue(good_verdict['passed'], good_verdict)
                            bad_verdict = evaluator.evaluate_kpis(bad, definitions)
                            self.assertFalse(bad_verdict['passed'])
                            self.assertTrue(any('frames' in item and 'p50_s' in item for item in bad_verdict['violations']))
            finally:
                sys.path.pop(0)

    def test_enabled_rows_declare_supported_image_architectures(self):
        for service in SERVICES:
            configs = yaml.safe_load((ROOT / 'services/analytics' / service / 'perf/configs/perf-configs.yaml').read_text())['perf_configs']
            for cfg in configs:
                if cfg['enabled']:
                    self.assertIn(cfg['imageArch'], {'amd64', 'arm64-sbsa', 'arm64-igpu'})


if __name__ == '__main__':
    unittest.main()
