# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only contract checks for RTVI's CUDA tensor IPC default and overrides."""

import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
RTVI = ROOT / "services/rtvi/rt-vlm"
sys.path.insert(0, str(RTVI / "src"))

from utils.env_validation import get_vllm_mm_tensor_ipc


class TensorIpcDefaultsTest(unittest.TestCase):
    def test_default_and_overrides(self):
        cases = [
            ({}, "torch_shm"),
            ({"VLLM_MM_TENSOR_IPC": ""}, "torch_shm"),
            ({"RTVI_VLLM_MM_TENSOR_IPC": "  "}, "torch_shm"),
            ({"VLLM_MM_TENSOR_IPC": "direct_rpc"}, "direct_rpc"),
            ({"RTVI_VLLM_MM_TENSOR_IPC": " direct_rpc "}, "direct_rpc"),
            ({"NUM_GPUS_PER_VLM_PROC": "2"}, "direct_rpc"),
            ({"NUM_GPUS_PER_VLM_PROC": "2", "VLLM_MM_TENSOR_IPC": ""}, "direct_rpc"),
            ({"NUM_GPUS_PER_VLM_PROC": "2", "VLLM_MM_TENSOR_IPC": "torch_shm"}, "torch_shm"),
            (
                {"VLLM_MM_TENSOR_IPC": "torch_shm", "RTVI_VLLM_MM_TENSOR_IPC": "direct_rpc"},
                "direct_rpc",
            ),
            (
                {"VLLM_MM_TENSOR_IPC": "direct_rpc", "RTVI_VLLM_MM_TENSOR_IPC": "torch_shm"},
                "torch_shm",
            ),
        ]
        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ):
                self.assertEqual(get_vllm_mm_tensor_ipc(), expected)

    def test_engine_default_override_and_capability_gate(self):
        path = RTVI / "src/models/vllm_compatible/vllm_compatible_model.py"
        nodes = list(ast.walk(ast.parse(path.read_text())))
        assignment = next(
            node
            for node in nodes
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "mm_tensor_ipc"
                for target in node.targets
            )
        )
        guard = next(
            node
            for node in nodes
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mm_tensor_ipc"
        )
        policy = compile(ast.Module(body=[assignment, guard], type_ignores=[]), str(path), "exec")
        for mode, supported, architecture, tp_size in (
            (None, True, "Qwen3VLForConditionalGeneration", 1),
            ("direct_rpc", True, "Qwen3VLForConditionalGeneration", 1),
            (None, False, "Qwen3VLForConditionalGeneration", 1),
            (None, True, "Cosmos3EdgeForConditionalGeneration", 1),
            (None, True, "Qwen3VLForConditionalGeneration", 2),
        ):
            with self.subTest(
                mode=mode, supported=supported, architecture=architecture
            ), patch.dict(os.environ, {}, clear=True):
                if mode is not None:
                    os.environ["RTVI_VLLM_MM_TENSOR_IPC"] = mode
                model = SimpleNamespace(
                    _model_architecture=architecture, _use_cuda_mm_tensor_ipc=False
                )
                args = {"tensor_parallel_size": tp_size}
                exec(  # noqa: S102 - Execute only the checked-in configuration block.
                    policy,
                    {
                        "self": model,
                        "get_vllm_mm_tensor_ipc": get_vllm_mm_tensor_ipc,
                        "_engine_supported_params": {"mm_tensor_ipc"} if supported else set(),
                        "engine_args_kwargs": args,
                        "logger": Mock(),
                        "_QWEN3VL_ARCHS": {"Qwen3VLForConditionalGeneration"},
                    },
                )
                expected_mode = mode or ("torch_shm" if tp_size == 1 else "direct_rpc")
                expected_args = {"tensor_parallel_size": tp_size}
                if supported:
                    expected_args["mm_tensor_ipc"] = expected_mode
                self.assertEqual(args, expected_args)
                self.assertEqual(
                    model._use_cuda_mm_tensor_ipc,
                    supported
                    and expected_mode == "torch_shm"
                    and architecture == "Qwen3VLForConditionalGeneration",
                )

    def test_decoder_default_and_allocator_safety(self):
        path = RTVI / "src/vlm_pipeline/process_base.py"
        statements = ast.parse(path.read_text()).body
        start = next(
            index
            for index, node in enumerate(statements)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_USE_CUDA_MM_TENSOR_IPC"
                for target in node.targets
            )
        )
        # Execute the real import-time policy without importing GPU/GStreamer dependencies.
        policy = compile(
            ast.Module(body=statements[start : start + 2], type_ignores=[]), str(path), "exec"
        )
        for mode, num_gpus, enabled in (
            (None, 1, True),
            ("torch_shm", 1, True),
            ("direct_rpc", 1, False),
            (None, 2, False),
        ):
            with self.subTest(mode=mode), patch.dict(os.environ, {}, clear=True):
                os.environ["NUM_GPUS_PER_VLM_PROC"] = str(num_gpus)
                if mode is not None:
                    os.environ["RTVI_VLLM_MM_TENSOR_IPC"] = mode
                os.environ["PYTORCH_CUDA_ALLOC_CONF"] = (
                    "max_split_size_mb:128,expandable_segments:True"
                )
                scope = {"os": os, "get_vllm_mm_tensor_ipc": get_vllm_mm_tensor_ipc}
                exec(policy, scope)  # noqa: S102 - Execute only the checked-in allocation policy.
                self.assertEqual(scope["_USE_CUDA_MM_TENSOR_IPC"], enabled)
                self.assertEqual(
                    os.environ["PYTORCH_CUDA_ALLOC_CONF"],
                    f"max_split_size_mb:128,expandable_segments:{not enabled}",
                )

    def test_deployment_defaults(self):
        for path in (
            RTVI / "docker/compose.yaml",
            RTVI / "docker/compose.perf.yaml",
            ROOT / "deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml",
        ):
            with self.subTest(path=path):
                self.assertIn('VLLM_MM_TENSOR_IPC: "${VLLM_MM_TENSOR_IPC:-}"', path.read_text())
        for name in ("values.yaml", "overrides_rtvi_vlm.yaml"):
            path = ROOT / "deploy/helm/services/rtvi/charts/rtvi-vlm" / name
            self.assertRegex(path.read_text(), r'- name: VLLM_MM_TENSOR_IPC\n\s+value: ""')
        self.assertIn(
            'VLLM_MM_TENSOR_IPC="${VLLM_MM_TENSOR_IPC:-}"',
            (RTVI / "perf/setup_perf_env.sh").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
