# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for local evaluation preparation and execution evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'local_vision_eval', Path(__file__).resolve().parents[1] / 'local/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LocalVisionEval(unittest.TestCase):
    def test_nvidia_key_alias_configures_agent_and_judge(self):
        env = {"NVIDIA_API_KEY": "test-key"}
        runner.configure_credentials(env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "test-key")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://inference-api.nvidia.com")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_THINKING"], "1")

    def test_explicit_provider_configuration_is_preserved(self):
        env = {"NVIDIA_API_KEY": "nvidia-test", "ANTHROPIC_API_KEY": "explicit-test",
               "ANTHROPIC_BASE_URL": "https://example.invalid", "ANTHROPIC_MODEL": "custom-model"}
        before = env.copy()
        runner.configure_credentials(env)
        self.assertEqual(env, before)

    def test_preparation_preserves_checks_and_ci_hardware(self):
        for path in (runner.SKILL / 'evals').glob('*.json'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                original = json.loads(path.read_text())
                rendered, workspace, prompt = runner.prepare(path, Path(td), 'RTX 4090')
                self.assertEqual(rendered['resources'], original['resources'])
                self.assertEqual(rendered['expects'][0]['checks'], original['expects'][0]['checks'])
                self.assertEqual(prompt, original['expects'][0]['query'])
                self.assertEqual((Path(td) / 'instruction.md').read_text(), prompt)
                self.assertNotIn('{{', json.dumps(rendered))
                self.assertTrue((workspace / '.claude/skills' / runner.SKILL.name / 'SKILL.md').is_file())
                manifest = json.loads((Path(td) / 'manifest.json').read_text())
                self.assertEqual(manifest['local_hardware_override'], 'RTX 4090')
                self.assertFalse(manifest['ci_equivalent'])

    def test_local_and_harbor_requests_match_exact_user_prompts(self):
        adapter_spec = importlib.util.spec_from_file_location(
            "vision_adapter", runner.HARNESS / "adapters/vss-build-vision-pipeline/generate.py")
        adapter = importlib.util.module_from_spec(adapter_spec)
        adapter_spec.loader.exec_module(adapter)
        expected = {
            "yolo26_object_detection": "Create an object detection pipeline using the Yolo26 model",
            "peoplenet_transformer_four_streams": "Create a multi-stream pipeline for 4 video inputs with the PeopleNet Transformer model",
            "rf_detr_instance_segmentation": "Create an object segmentation pipeline using RF-DETR",
            "yolo26_object_detection_microservice": "Create an object detection microservice using the Yolo26 model.",
        }
        for name, prompt in expected.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                path = runner.SKILL / "evals" / (name + ".json")
                original = json.loads(path.read_text())
                self.assertEqual(original["expects"][0]["query"], prompt)
                _, _, local_prompt = runner.prepare(path, Path(td) / "local", "RTX 4090")
                self.assertEqual(local_prompt, prompt)
                task = adapter.generate(path, "L40S", Path(td) / "harbor", runner.SKILL)
                self.assertEqual((task / "instruction.md").read_text(), prompt)

    def test_trace_preserves_tool_identity_and_observations(self):
        events = [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'call1', 'name': 'Bash', 'input': {'command': 'docker ps'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'call1', 'content': 'result'}]}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Finished'}]}},
        ]
        trace = runner.normalize_trace(events)
        self.assertEqual(trace['steps'][0]['tool_calls'][0]['tool_call_id'], 'call1')
        self.assertEqual(trace['steps'][1]['observation'][0]['content'], 'result')
        self.assertEqual(trace['steps'][-1]['message'], 'Finished')
        self.assertEqual(sum(len(s['tool_calls']) for s in trace['steps']), 1)

    def test_api_error_never_counts_as_success_even_with_zero_exit(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (
            json.dumps({'type': 'result', 'subtype': 'success', 'is_error': True}) + '\n', '')
        with tempfile.TemporaryDirectory() as td, \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.dict(runner.os.environ, {'ANTHROPIC_MODEL': 'test-model'}):
            self.assertFalse(runner.execute_agent('prompt', Path(td), Path(td), 1))
            self.assertFalse((Path(td) / 'reward.txt').exists())

    def test_missing_final_result_is_failure(self):
        process = Mock(returncode=0)
        process.communicate.return_value = ('', '')
        with tempfile.TemporaryDirectory() as td, \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.dict(runner.os.environ, {'ANTHROPIC_MODEL': 'test-model'}):
            self.assertFalse(runner.execute_agent('prompt', Path(td), Path(td), 1))


if __name__ == '__main__':
    unittest.main()
