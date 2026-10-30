# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compile supplied ONNX graphs; never export or reuse engines by filename alone."""

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "release_contract", _here / "input_contract.py"
)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def graph_contract(path, kind):
    import onnx

    graph = onnx.load(str(path), load_external_data=False).graph
    names = {i.name: i for i in graph.input}
    expected = {"videos"} if kind == "video" else {"input_ids", "attention_mask"}
    if set(names) != expected:
        raise ValueError(f"Unexpected {kind} graph input names")
    for value in names.values():
        tensor = value.type.tensor_type
        shape = [
            d.dim_value if d.HasField("dim_value") else None for d in tensor.shape.dim
        ]
        target = [None, 3, 8, 448, 448] if kind == "video" else [None, 128]
        if shape != target or tensor.elem_type != (1 if kind == "video" else 7):
            raise ValueError(f"Unexpected {kind} input shape/dtype: {value.name}")
    output = graph.output
    wanted = "video_embedding" if kind == "video" else "text_embedding"
    if len(output) != 1 or output[0].name != wanted:
        raise ValueError(f"Unexpected {kind} graph output")
    tensor = output[0].type.tensor_type
    dimensions = tensor.shape.dim
    # The supplied graph names this output axis symbolically. Its actual
    # width is checked against 768 at inference, rather than rejecting a
    # legitimate graph whose static shape inference is incomplete.
    if (
        tensor.elem_type != 1
        or len(dimensions) != 2
        or (dimensions[-1].HasField("dim_value") and dimensions[-1].dim_value != 768)
    ):
        raise ValueError(f"Unexpected {kind} embedding shape/dtype")
    if kind == "video":
        locations = {
            e.value
            for t in graph.initializer
            for e in t.external_data
            if e.key == "location"
        }
        if locations != {contract.VISION + "_data"}:
            raise ValueError(
                "Vision graph external weights differ from the verified release"
            )
    onnx.checker.check_model(str(path))


def configuration(kind, batch):
    if kind == "video":
        inputs = 'input [{ name: "videos" data_type: TYPE_FP32 dims: [3,8,448,448] }]'
        output = "video_embedding"
    else:
        inputs = (
            'input [{ name: "input_ids" data_type: TYPE_INT64 dims: [128] }, '
            '{ name: "attention_mask" data_type: TYPE_INT64 dims: [128] }]'
        )
        output = "text_embedding"
    return (
        f'name: "{kind}_embeddings"\nplatform: "tensorrt_plan"\nmax_batch_size: {batch}\n'
        'default_model_filename: "model.plan"\n' + inputs + "\n"
        f'output [{{ name: "{output}" data_type: TYPE_FP32 dims: [768] }}]\n'
        "instance_group [{ kind: KIND_GPU count: 1 gpus: [0] }]\n"
    )


def build(args):
    import tensorrt

    model = Path(args.model_path).resolve()
    root = model.parent
    onnx_dir = root / "onnx"
    release_paths = [
        onnx_dir / name
        for name in (contract.VISION, contract.VISION + "_data", contract.TEXT)
    ]
    release_paths += sorted(
        p for p in model.iterdir() if p.suffix in (".py", ".json", ".txt")
    )
    release_hashes = contract.verify_release(root, release_paths)
    settings = {
        "release_files": release_hashes,
        "adapter_files": {
            p.name: contract.sha256(p) for p in sorted(_here.glob("*.py"))
        },
        "tensorrt": tensorrt.__version__,
        "gpu": subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,compute_cap",
                "--format=csv,noheader",
                "-i",
                "0",
            ],
            text=True,
        ).strip(),
        "max_batch_size": args.max_batch_size,
        "precision": "fp16",
        "contract": "cosmos-v1.2-bcthw-fp32-128-v1",
    }
    target = Path(args.triton_repo_path) / model.name.lower()
    seal_path = target / "build.json"
    if seal_path.exists():
        seal = contract.verify_build(target)
        if any(seal.get(k) != value for k, value in settings.items()):
            raise ValueError("Engine build identity changed; use a separate repository")
        print("Validated existing checkpoint-specific engines")
        return target
    if target.exists() and any(target.iterdir()):
        raise ValueError(
            "Unsealed/incomplete engine build exists; inspect it before rebuilding"
        )
    target.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for kind, name in (("video", contract.VISION), ("text", contract.TEXT)):
        graph = onnx_dir / name
        graph_contract(graph, kind)
        directory = target / (kind + "_embeddings")
        (directory / "1").mkdir(parents=True)
        engine = directory / "1/model.plan"
        if kind == "video":
            shapes = [
                f"videos:{batch}x3x8x448x448"
                for batch in (1, max(1, args.max_batch_size // 2), args.max_batch_size)
            ]
        else:
            shapes = [
                f"input_ids:{batch}x128,attention_mask:{batch}x128"
                for batch in (1, max(1, args.max_batch_size // 2), args.max_batch_size)
            ]
        command = [
            "/usr/src/tensorrt/bin/trtexec",
            f"--onnx={graph}",
            f"--saveEngine={engine}",
            "--fp16",
            "--skipInference",
        ]
        command += [
            f"--{key}Shapes={shape}"
            for key, shape in zip(("min", "opt", "max"), shapes)
        ]
        with (target / (kind + "-build.log")).open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        if not engine.is_file() or engine.stat().st_size == 0:
            raise RuntimeError("TensorRT did not publish an engine")
        config = directory / "config.pbtxt"
        config.write_text(configuration(kind, args.max_batch_size))
        for path in (engine, config):
            artifacts[path.relative_to(target).as_posix()] = contract.sha256(path)
    settings["artifacts"] = artifacts
    temporary = seal_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, seal_path)
    contract.verify_build(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model_path", required=True)
    parser.add_argument("--triton_repo_path", required=True)
    parser.add_argument("--max_batch_size", type=int, default=64)
    args = parser.parse_args()
    if args.max_batch_size != 64:
        parser.error("This release adapter supports a maximum batch size of 64")
    print(build(args))


if __name__ == "__main__":
    main()
