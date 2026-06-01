# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import numpy as np
import onnx
import torch
from transformers import AutoModel, AutoProcessor

from common.logger import logger

_PRECISION_TO_TRTEXEC_FLAGS = {
    "fp32": [],
    "fp16": ["--fp16"],
    "bf16": ["--bf16"],
    "int8": ["--int8"],
    "fp8": ["--fp8"],
    "best": ["--best"],
}


def _precision_flags(precision):
    """Return trtexec command-line flags for the requested network precision.

    Reference:
    https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/command-line-programs.html
    """
    try:
        return list(_PRECISION_TO_TRTEXEC_FLAGS[precision])
    except KeyError as exc:
        raise ValueError(
            f"Unsupported precision '{precision}'. "
            f"Choose from: {sorted(_PRECISION_TO_TRTEXEC_FLAGS)}"
        ) from exc


def _parse_extra_trtexec_args(extra_args):
    """Tokenize an extra-trtexec-args string with shell-like quoting.

    Empty / whitespace-only input returns []. Mismatched quotes raise ValueError.
    """
    if not extra_args or not extra_args.strip():
        return []
    return shlex.split(extra_args)


def _engine_name_extras_suffix(extra_tokens):
    """Return a short, deterministic suffix encoding the extra-args set.

    Empty list → "" (no suffix, matches pre-extras filenames).
    Non-empty → "_<sha1[:8]>" computed from a normalized join of the tokens, so
    re-ordered or re-quoted but logically equivalent invocations produce the
    same hash. Engine filenames embed this suffix so changing extras forces a
    rebuild instead of silently re-using a stale engine.
    """
    if not extra_tokens:
        return ""
    canonical = "\x1f".join(extra_tokens)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"_{digest}"


def verify_onnx(model_path):
    onnx.checker.check_model(model_path)
    logger.info(f"✅ {model_path} is valid")


def export_onnx_video(
    model_path,
    output_dir,
    num_frames,
    height,
    width,
):

    if os.path.exists(f"{output_dir}/cosmos_embed1_video.onnx"):
        logger.info("ONNX file for video branch already exists, skipping export")
        return

    logger.info("Starting ONNX export process for video branch")
    logger.debug(f"Loading model from checkpoint: {model_path}")

    channels = 3
    model = AutoModel.from_pretrained(model_path, local_files_only=True, trust_remote_code=True).to(
        "cpu"
    )

    class VideoBranchWrapper(torch.nn.Module):
        def __init__(self, model, processor):
            super().__init__()
            self.model = model
            self.processor = processor

        def forward(self, videos):
            video_inputs = self.processor(videos=videos).to("cpu", dtype=torch.bfloat16)
            video_embeddings_output = self.model.get_video_embeddings(**video_inputs)
            video_embeddings = video_embeddings_output.visual_proj
            return video_embeddings

    # Move model to CPU for stable ONNX export
    model = model.to("cpu").eval()
    logger.debug("Model moved to CPU and set to eval mode")

    # Export video branch
    processor = AutoProcessor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=True
    )
    # Dummy video input (BTCHW format, on CPU)
    dummy_video = torch.randn(
        1, num_frames, channels, height, width, device="cpu", dtype=torch.float32
    )
    dummy_video = dummy_video.to(dtype=torch.uint8)

    logger.info("Exporting video branch to ONNX")
    video_model = VideoBranchWrapper(model, processor)
    torch.onnx.export(
        video_model,
        dummy_video,
        f"{output_dir}/cosmos_embed1_video.onnx",
        input_names=["videos"],
        output_names=["video_embeddings"],
        dynamic_axes={
            "videos": {0: "batch_size"},
            "video_embeddings": {0: "batch_size"},
        },
        opset_version=18,
        do_constant_folding=True,
        verbose=True,
        export_params=True,
    )

    # Verify ONNX Model
    logger.info("Verifying video branch ONNX model")
    verify_onnx(f"{output_dir}/cosmos_embed1_video.onnx")

    del model
    del processor
    logger.info("Video branch ONNX export completed: cosmos_embed1_video.onnx")


def export_onnx_text(
    model_path,
    output_dir,
):

    if os.path.exists(f"{output_dir}/cosmos_embed1_text.onnx"):
        logger.info("ONNX file for text branch already exists, skipping export")
        return

    logger.info("Starting ONNX export process for text branch")
    logger.debug(f"Loading model from checkpoint: {model_path}")

    model = AutoModel.from_pretrained(model_path, local_files_only=True, trust_remote_code=True).to(
        "cpu"
    )
    model.eval()

    # Export Text Branch
    logger.info("Exporting text branch to ONNX")

    processor = AutoProcessor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=True
    )

    class TextBranchWrapper(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, input_ids, attention_mask):
            text_embeddings_output = self.model.get_text_embeddings(
                input_ids=input_ids, attention_mask=attention_mask
            )
            text_embeddings = text_embeddings_output.text_proj
            return text_embeddings

    # Process text input using processor from CosmosModelWrapper
    dummy_text = ["A sample caption"]
    inputs = processor.tokenizer(
        dummy_text, return_tensors="pt", padding=True, truncation=True, max_length=512
    )
    inputs = {k: v.to("cpu") for k, v in inputs.items()}

    # Export text branch
    text_model = TextBranchWrapper(model)
    torch.onnx.export(
        text_model,
        (inputs["input_ids"], inputs["attention_mask"]),
        f"{output_dir}/cosmos_embed1_text.onnx",
        input_names=["input_ids", "attention_mask"],
        output_names=["text_embeddings"],
        dynamic_axes={
            "input_ids": {0: "batch_size", 1: "sequence_length"},
            "attention_mask": {0: "batch_size", 1: "sequence_length"},
            "text_embeddings": {0: "batch_size"},
        },
        opset_version=18,
        do_constant_folding=True,
        verbose=True,
        export_params=True,
    )

    # Verify ONNX Models
    logger.info("Verifying text branch ONNX model")
    verify_onnx(f"{output_dir}/cosmos_embed1_text.onnx")

    del model
    del processor
    logger.info("Text branch ONNX export completed: cosmos_embed1_text.onnx")


def get_gpu_name():
    """Return a sanitized GPU name suitable for filenames (single GPU, no spaces/newlines)."""
    try:
        # Query only the first GPU to avoid multi-line output on multi-GPU systems
        command = [
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader",
            "-i",
            "0",
        ]
        raw_output = subprocess.check_output(command, text=True).strip()
        # Take the first line and replace spaces with underscores for filename safety
        gpu_name = raw_output.splitlines()[0].replace(" ", "_")
        return gpu_name
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Error getting GPU name: {e}")
        return "unknown_gpu"


def create_trt_engine_video(
    output_dir, num_frames, height, width, max_batch_size, precision, extra_args=""
):
    """Create a TRT engine for the model."""

    gpu_name = get_gpu_name()
    extra_tokens = _parse_extra_trtexec_args(extra_args)
    extras_suffix = _engine_name_extras_suffix(extra_tokens)
    engine_name = (
        f"cosmos_embed1_video_{gpu_name}_{max_batch_size}_{precision}{extras_suffix}.engine"
    )

    if os.path.exists(f"{output_dir}/1/{engine_name}"):
        logger.info("TensorRT engine for video branch already exists, skipping creation")
        return
    logger.info("Starting TensorRT engine creation")
    # Define parameters for video branch
    logger.debug(
        f"TRT parameters - frames: {num_frames}, "
        f"height: {height}, width: {width}, max_batch: {max_batch_size}, "
        f"precision: {precision}, extra_args: {extra_tokens}"
    )
    channels = 3

    # Video branch TRT engine creation
    os.makedirs(f"{output_dir}/1", exist_ok=True)
    onnx_video_path = f"{output_dir}/cosmos_embed1_video.onnx"
    trt_video_path = f"{output_dir}/1/{engine_name}"

    opt_batch_size = max(1, max_batch_size // 2)
    video_cmd = [
        "/usr/src/tensorrt/bin/trtexec",
        f"--onnx={onnx_video_path}",
        f"--saveEngine={trt_video_path}",
        f"--minShapes=videos:1x{num_frames}x{channels}x{height}x{width}",
        f"--optShapes=videos:{opt_batch_size}x{num_frames}x{channels}x{height}x{width}",
        f"--maxShapes=videos:{max_batch_size}x{num_frames}x{channels}x{height}x{width}",
        "--skipInference",
        *_precision_flags(precision),
        *extra_tokens,
    ]

    logger.info(
        f"Creating TRT engine for video branch: {trt_video_path} "
        f"(precision={precision}, extras={extra_tokens})"
    )
    subprocess.run(video_cmd, check=True)
    logger.info("✅ Video TRT engine created successfully")


def create_trt_engine_text(output_dir, max_batch_size, precision, extra_args=""):
    """Create a TRT engine for the model."""
    gpu_name = get_gpu_name()
    extra_tokens = _parse_extra_trtexec_args(extra_args)
    extras_suffix = _engine_name_extras_suffix(extra_tokens)
    engine_name = (
        f"cosmos_embed1_text_{gpu_name}_{max_batch_size}_{precision}{extras_suffix}.engine"
    )
    if os.path.exists(f"{output_dir}/1/{engine_name}"):
        logger.info("TensorRT engine for text branch already exists, skipping creation")
        return
    # Text branch TRT engine creation
    os.makedirs(f"{output_dir}/1", exist_ok=True)
    onnx_text_path = f"{output_dir}/cosmos_embed1_text.onnx"
    trt_text_path = f"{output_dir}/1/{engine_name}"

    text_cmd = [
        "/usr/src/tensorrt/bin/trtexec",
        f"--onnx={onnx_text_path}",
        f"--saveEngine={trt_text_path}",
        "--minShapes=input_ids:1x1,attention_mask:1x1",
        f"--optShapes=input_ids:{max_batch_size}x128,attention_mask:{max_batch_size}x128",
        f"--maxShapes=input_ids:{max_batch_size}x256,attention_mask:{max_batch_size}x256",
        "--skipInference",
        *_precision_flags(precision),
        *extra_tokens,
    ]

    logger.info(
        f"Creating TRT engine for text branch: {trt_text_path} "
        f"(precision={precision}, extras={extra_tokens})"
    )
    subprocess.run(text_cmd, check=True)
    logger.info("✅ Text TRT engine created successfully")
    logger.info("TensorRT engine creation completed successfully")


def copy_config_to_triton_repo(
    output_dir_video,
    output_dir_text,
    max_batch_size,
    num_frames,
    height,
    width,
    embed_dim,
    precision,
    extra_args="",
):
    gpu_name = get_gpu_name()
    extras_suffix = _engine_name_extras_suffix(_parse_extra_trtexec_args(extra_args))
    video_engine_name = (
        f"cosmos_embed1_video_{gpu_name}_{max_batch_size}_{precision}{extras_suffix}.engine"
    )
    text_engine_name = (
        f"cosmos_embed1_text_{gpu_name}_{max_batch_size}_{precision}{extras_suffix}.engine"
    )
    logger.info("Copying config files to TRITON repository")
    src_triton_repo_path = os.path.join(
        "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1/triton_model_repo"
    )
    video_src_path = os.path.join(src_triton_repo_path, "video_embeddings", "config.pbtxt")
    text_src_path = os.path.join(src_triton_repo_path, "text_embeddings", "config.pbtxt")
    if os.path.exists(video_src_path):
        shutil.copy(video_src_path, os.path.join(output_dir_video, "config.pbtxt"))
    if os.path.exists(text_src_path):
        shutil.copy(text_src_path, os.path.join(output_dir_text, "config.pbtxt"))

    # Update the config.pbtxt files with the new max_batch_size and other parameters
    video_config_path = os.path.join(output_dir_video, "config.pbtxt")
    text_config_path = os.path.join(output_dir_text, "config.pbtxt")

    if os.path.exists(text_config_path):
        # Read the config file and update parameters
        with open(text_config_path, "r") as f:
            config_content = f.read()

        # Update max_batch_size
        config_content = re.sub(
            r"max_batch_size:\s*\d+", f"max_batch_size: {max_batch_size}", config_content
        )

        # Update embedding dimensions (dims: [768] -> dims: [embed_dim])
        config_content = re.sub(r"dims:\s*\[768\]", f"dims: [{embed_dim}]", config_content)

        # Update default_model_filename
        config_content = re.sub(
            r'default_model_filename:\s*"[^"]*"',
            f'default_model_filename: "{text_engine_name}"',
            config_content,
        )

        # Write the updated config back to file
        with open(text_config_path, "w") as f:
            f.write(config_content)

    if os.path.exists(video_config_path):
        # Read the config file
        with open(video_config_path, "r") as f:
            config_content = f.read()

        # Update video input dims
        config_content = re.sub(
            r"dims:\s*\[8,\s*3,\s*448,\s*448\]",
            f"dims: [{num_frames}, 3, {height}, {width}]",
            config_content,
        )

        # Update video output dims
        config_content = re.sub(r"dims:\s*\[768\]", f"dims: [{embed_dim}]", config_content)

        # Update max_batch_size
        config_content = re.sub(
            r"max_batch_size:\s*\d+", f"max_batch_size: {max_batch_size}", config_content
        )

        # Update default_model_filename
        config_content = re.sub(
            r'default_model_filename:\s*"[^"]*"',
            f'default_model_filename: "{video_engine_name}"',
            config_content,
        )

        # Write the updated config back to file
        with open(video_config_path, "w") as f:
            f.write(config_content)
    logger.info("Config files copied successfully")


def create_triton_repo_path(triton_repo_path, output_dir_video, output_dir_text):
    """Create a TRITON repository path for the model."""
    os.makedirs(triton_repo_path, exist_ok=True)
    logger.debug(f"Created Triton repo path: {triton_repo_path}")
    os.makedirs(output_dir_video, exist_ok=True)
    logger.debug(f"Created Triton repo path for video embeddings model: {output_dir_video}")
    os.makedirs(output_dir_text, exist_ok=True)
    logger.debug(f"Created Triton repo path for text embeddings model: {output_dir_text}")


def test_with_triton_server(
    model_path,
    model_repo_path,
    num_frames,
    height,
    width,
):
    logger.info("Testing with TRITON server")
    logger.info("Loading video model")

    import time

    import tritonserver
    from transformers import AutoProcessor

    channels = 3
    server_options = tritonserver.Options(model_repository=model_repo_path, exit_timeout=30)
    server = tritonserver.Server(server_options)
    server.start(wait_until_ready=True)
    logger.info("TRITON server started successfully")

    processor = AutoProcessor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=True
    )
    text_model = server.model("text_embeddings")

    dummy_text = ["A sample caption"]
    start_time = time.time()
    text_inputs = processor(text=dummy_text, return_tensors="pt")
    feed_dict = {
        "input_ids": text_inputs.input_ids.cpu().numpy().astype(np.int64),
        "attention_mask": text_inputs.attention_mask.cpu().numpy().astype(np.int64),
    }
    text_responses = text_model.infer(inputs=feed_dict)

    text_embeddings = []
    for text_response in text_responses:
        tensor = text_response.outputs["text_embeddings"]
        torch_array = torch.from_dlpack(tensor)
        logger.info(f"Text embeddings shape: {torch_array.shape}")
        text_embeddings.extend(torch_array.cpu().tolist())

    end_time = time.time()
    logger.info(f"Text inference time: {end_time - start_time} seconds")
    logger.info(f"Text embeddings shape: {len(text_embeddings)}, {len(text_embeddings[0])}")
    logger.info(f"Text embeddings type: {type(text_embeddings)}, {type(text_embeddings[0])}")

    video_model = server.model("video_embeddings")

    # iterate 2 times
    for _ in range(2):
        # dummy_video = torch.randn(1, num_frames, channels, height, width, device="cpu", dtype=torch.float32)
        dummy_video = torch.randn(
            1, num_frames, channels, height, width, device="cuda", dtype=torch.float32
        )
        dummy_video = dummy_video.to(dtype=torch.uint8)
        start_time = time.time()
        feed_dict = {
            # "videos": dummy_video.cpu().numpy().astype(np.float32),
            "videos": dummy_video,
        }
        video_responses = video_model.infer(inputs=feed_dict)
        video_embeddings = []
        for video_response in video_responses:
            tensor = video_response.outputs["video_embeddings"]
            torch_array = torch.from_dlpack(tensor)
            logger.info(f"Video embeddings shape: {torch_array.shape}")
            video_embeddings.extend(torch_array.cpu().tolist())
        end_time = time.time()
        logger.info(f"Video inference time: {end_time - start_time} seconds")
        logger.info(f"Video embeddings shape: {len(video_embeddings)}, {len(video_embeddings[0])}")
        logger.info(f"Video embeddings type: {type(video_embeddings)}, {type(video_embeddings[0])}")

    server.stop()
    del video_model
    del text_model
    del processor
    logger.info("Test completed successfully")


def main():
    """Main function to export ONNX models."""
    import argparse

    parser = argparse.ArgumentParser(description="Export Cosmos Embed1 model to ONNX")
    parser.add_argument(
        "--model_path",
        type=str,
        default="/opt/nvidia/rtvi/.rtvi/ngc_model_cache/Cosmos-Embed1-448p",
        help=(
            "Path to the model checkpoint "
            "(default: /opt/nvidia/rtvi/.rtvi/ngc_model_cache/Cosmos-Embed1-448p)"
        ),
    )
    parser.add_argument(
        "--triton_repo_path",
        type=str,
        default="/tmp/triton_model_repo",
        help="TRITON repository path for the model",
    )
    parser.add_argument(
        "--max_batch_size",
        type=int,
        default=16,
        help="Max batch size for the model",
    )
    parser.add_argument(
        "--precision",
        type=str,
        default=os.environ.get("COSMOS_EMBED1_TRT_PRECISION", "fp16"),
        choices=sorted(_PRECISION_TO_TRTEXEC_FLAGS.keys()),
        help=(
            "trtexec network precision (default: fp16, overridable via "
            "COSMOS_EMBED1_TRT_PRECISION env var). 'fp32' omits all precision flags. "
            "'int8' and 'fp8' typically require a calibration cache or scaled ONNX; without "
            "those, trtexec may fail or fall back. See "
            "https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/command-line-programs.html"
        ),
    )
    parser.add_argument(
        "--extra-trtexec-args",
        dest="extra_trtexec_args",
        type=str,
        default=os.environ.get("COSMOS_EMBED1_TRT_EXTRA_ARGS", ""),
        help=(
            "Additional trtexec args (shell-quoted string) appended verbatim to both "
            "the video and text engine builds, e.g. "
            "'--stronglyTyped --builderOptimizationLevel=5'. Overridable via the "
            "COSMOS_EMBED1_TRT_EXTRA_ARGS env var. Note: '--stronglyTyped' is mutually "
            "exclusive with --fp16/--bf16/--int8/--fp8/--best; pair it with "
            "'--precision fp32' to avoid trtexec errors. Engine filename includes a "
            "short hash of these args so engines are rebuilt on change."
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode, test with dummy data",
    )
    args = parser.parse_args()

    logger.info("Starting ONNX export via main function")
    logger.info(f"Model path: {args.model_path}")

    if not os.path.exists(args.model_path):
        logger.error(f"Model path {args.model_path} does not exist")
        sys.exit(1)

    model_dir_name = os.path.basename(os.path.normpath(args.model_path))
    args.model_name = model_dir_name.lower()

    config_path = os.path.join(args.model_path, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config_data = json.load(f)
        logger.debug(f"Loaded config from {config_path}")
    else:
        logger.error(f"Config file {config_path} does not exist")
        sys.exit(1)

    args.num_frames = config_data.get("num_video_frames", 8)
    args.height = config_data.get("resolution", 448)
    args.width = config_data.get("resolution", 448)
    args.embed_dim = config_data.get("embed_dim", 768)

    args.triton_repo_path = os.path.join(args.triton_repo_path, args.model_name)
    args.output_dir_video = os.path.join(args.triton_repo_path, "video_embeddings")
    args.output_dir_text = os.path.join(args.triton_repo_path, "text_embeddings")

    # Export ONNX models
    create_triton_repo_path(args.triton_repo_path, args.output_dir_video, args.output_dir_text)
    export_onnx_video(
        model_path=args.model_path,
        output_dir=args.output_dir_video,
        num_frames=args.num_frames,
        height=args.height,
        width=args.width,
    )
    export_onnx_text(model_path=args.model_path, output_dir=args.output_dir_text)

    # Create TensorRT engines
    create_trt_engine_video(
        output_dir=args.output_dir_video,
        num_frames=args.num_frames,
        height=args.height,
        width=args.width,
        max_batch_size=args.max_batch_size,
        precision=args.precision,
        extra_args=args.extra_trtexec_args,
    )
    create_trt_engine_text(
        output_dir=args.output_dir_text,
        max_batch_size=args.max_batch_size,
        precision=args.precision,
        extra_args=args.extra_trtexec_args,
    )
    copy_config_to_triton_repo(
        args.output_dir_video,
        args.output_dir_text,
        args.max_batch_size,
        args.num_frames,
        args.height,
        args.width,
        args.embed_dim,
        args.precision,
        args.extra_trtexec_args,
    )

    if args.test:
        logger.info("Test mode, testing with dummy data")
        test_with_triton_server(
            model_path=args.model_path,
            model_repo_path=args.triton_repo_path,
            num_frames=args.num_frames,
            height=args.height,
            width=args.width,
        )


if __name__ == "__main__":
    main()
