# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the pipeline cancellation regressions without importing CUDA/DeepStream.

Production methods are compiled from their AST and the conventional test cases
are reused unchanged. Multiprocessing startup and GPU transport remain covered
by the container tests; this suite isolates ownership and cancellation races.
"""

from __future__ import annotations

import ast
import concurrent.futures
import gc
import logging
import multiprocessing
import os
import queue
import sys
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


def _load_classes(name, source, classes, adapters):
    module = types.ModuleType(name)
    sys.modules[name] = module
    module.__dict__.update(
        concurrent=concurrent,
        gc=gc,
        os=os,
        queue=queue,
        time=time,
        traceback=traceback,
        dataclass=dataclass,
        field=field,
        uuid4=uuid4,
    )
    module.__dict__.update(adapters)
    nodes = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    ]
    for node in ast.parse(source.read_text()).body:
        if not isinstance(node, ast.ClassDef) or node.name not in classes:
            continue
        selected = classes[node.name]
        if selected is not None:
            node.body = [
                child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name in selected
            ]
        if node.name == "ProcessBase":
            node.bases = []
        nodes.append(node)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            str(source),
            "exec",
        ),
        module.__dict__,
    )
    return module


_root = Path(__file__).resolve().parents[2]
process_base_module = _load_classes(
    "_isolated_request_process_base",
    _root / "src/vlm_pipeline/process_base.py",
    {
        "ProcessBase": {
            "send_command",
            "_request_is_cancelled",
            "_finish_cancelled_chunk",
            "_filter_cancelled_items",
            "_discard_queued_request",
            "_put_queue_item",
            "_put_output_item",
            "_abort_request",
            "_release_request_tracking",
            "_handle_result",
            "__process_int",
            "_supports_batching",
            "_cmd_handler_thread_func",
        }
    },
    {
        "mp_ctx": multiprocessing.get_context("spawn"),
        "logger": logging.getLogger(__name__),
        "_safe_cuda_empty_cache": lambda **_: None,
        "_move_cuda_frames_to_cpu": lambda value: value,
        "_USE_CUDA_MM_TENSOR_IPC": False,
    },
)
_pipeline_module = _load_classes(
    "_isolated_request_pipeline",
    _root / "src/vlm_pipeline/vlm_pipeline.py",
    {
        "VlmRequestParams": None,
        "PipelineChunkResult": None,
        "VlmProcess": {"_abort_request", "_release_request_tracking"},
        "VlmPipeline": {
            "_complete_chunk",
            "_reserve_chunk_callback",
            "_all_vlm_processes_healthy",
            "abort_request",
            "release_request_cancellation",
            "enqueue_vlm_text_chunk",
            "enqueue_text_chunk",
        },
    },
    {
        "ProcessBase": process_base_module.ProcessBase,
        "logger": logging.getLogger(__name__),
        "ServiceException": RuntimeError,
    },
)
ProcessBase = process_base_module.ProcessBase
PipelineChunkResult = _pipeline_module.PipelineChunkResult
VlmPipeline = _pipeline_module.VlmPipeline
VlmProcess = _pipeline_module.VlmProcess
VlmRequestParams = _pipeline_module.VlmRequestParams

_tests = _root / "tests/rtvi_vlm/test_vlm_pipeline_request_cancellation.py"
_tree = ast.parse(_tests.read_text())
_tree.body = [
    node
    for node in _tree.body
    if not (
        isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("vlm_pipeline")
        or isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets)
    )
]
exec(compile(_tree, str(_tests), "exec"), globals())
