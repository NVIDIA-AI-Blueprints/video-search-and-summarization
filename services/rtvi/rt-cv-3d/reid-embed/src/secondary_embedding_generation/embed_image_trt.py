#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Generate image embeddings from SigLIP 2 or RADIO-CLIP ONNX models using TensorRT.

Builds a TensorRT engine from the ONNX file on first run (cached to disk),
then uses it for fast GPU inference. All functions are importable for use
in other scripts.

Supported models:
  siglip2     — SigLIP 2 so400m-patch16-256 (256x256, 1152-dim, mean/std=0.5)
  radio-clip  — RADIO-CLIP c-radio_v3-h     (224x224, 1536-dim, [0,1] only)

Prerequisites:
    pip install tensorrt pycuda numpy Pillow

Usage:
    # SigLIP 2
    python embed_image_trt.py --model siglip2 --image patch.jpg

    # RADIO-CLIP with FP16
    python embed_image_trt.py --model radio-clip --image patch.jpg --fp16

    # Force rebuild engine
    python embed_image_trt.py --model siglip2 --image patch.jpg --rebuild

    # Import in another script
    from embed_image_trt import TRTInference, build_engine, preprocess_image, MODEL_CONFIGS
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pycuda.autoinit  # noqa: F401  — initializes CUDA context
import pycuda.driver as cuda
import tensorrt as trt
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)

# parents[2], not [1]: this module lives at <root>/src/secondary_embedding_generation/,
# and models/ sits next to src/ rather than inside it.
_REPO_ROOT = Path(__file__).resolve().parents[2]

MODEL_CONFIGS = {
    "siglip2": {
        "description": "SigLIP 2 so400m-patch16-256",
        "onnx_path": str(_REPO_ROOT / "models" / "siglip_v2_vdeployable_v1.1" / "siglip2_v1.1.onnx"),
        "image_size": 256,
        "resize_method": "bilinear",
        # SigLIP 2 preprocessing: normalize with mean=0.5, std=0.5 (HuggingFace SiglipImageProcessor)
        "image_mean": [0.5, 0.5, 0.5],
        "image_std": [0.5, 0.5, 0.5],
    },
    "radio-clip": {
        "description": "RADIO-CLIP c-radio_v3-h with siglip2-g adaptor",
        "onnx_path": str(_REPO_ROOT / "models" / "radio-clip_vdeployable_v1.0" / "radio-clip_v1.0.onnx"),
        "image_size": 224,
        "resize_method": "bicubic",
        # RADIO-CLIP: no external normalization — the ONNX model includes RADIO's
        # internal preprocessor (preprocessor.mean/std) which handles it.
        "image_mean": None,
        "image_std": None,
    },
}

_RESIZE_METHODS = {
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


def get_model_config(model_name: str) -> dict:
    """Return the configuration dict for a model name.

    Args:
        model_name: One of the keys in MODEL_CONFIGS.

    Returns:
        Config dict with keys: description, onnx_path, image_size,
        resize_method, image_mean, image_std.
    """
    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"Unknown model '{model_name}'. Available: {list(MODEL_CONFIGS.keys())}")
    return MODEL_CONFIGS[model_name]


def preprocess_image(
    image: Union[str, np.ndarray],
    config: dict,
    *,
    bgr_input: bool = True,
) -> np.ndarray:
    """Load and preprocess an image according to a model config.

    Steps:
      1. Resize to config['image_size'] x config['image_size']
      2. Convert to float32 and rescale to [0, 1]
      3. If config has mean/std, normalize per channel

    Args:
        image: Path to an image file, or a ``uint8`` HWC array. OpenCV
            crops are BGR; set ``bgr_input=False`` if the array is already RGB.
        config: Model config dict from MODEL_CONFIGS.
        bgr_input: For ndarray input only, convert BGR to RGB before resize.

    Returns:
        Preprocessed image tensor of shape [1, 3, H, W].
    """
    image_size = config["image_size"]
    resample = _RESIZE_METHODS.get(config.get("resize_method", "bilinear"), Image.Resampling.BILINEAR)

    if isinstance(image, str):
        img = Image.open(image).convert("RGB")
    elif isinstance(image, np.ndarray):
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("ndarray image must be HWC with 3 channels")
        if image.dtype != np.uint8:
            raise TypeError("ndarray image must be uint8 (e.g. OpenCV BGR patch)")
        arr = image
        if bgr_input:
            arr = np.ascontiguousarray(arr[:, :, ::-1])
        img = Image.fromarray(arr, mode="RGB")
    else:
        raise TypeError(f"image must be str or np.ndarray, got {type(image)}")
    img = img.resize((image_size, image_size), resample)

    # HWC uint8 -> float32 [0, 1]
    pixels = np.array(img, dtype=np.float32) / 255.0

    # Normalize if model requires it
    if config.get("image_mean") is not None and config.get("image_std") is not None:
        mean = np.array(config["image_mean"], dtype=np.float32)
        std = np.array(config["image_std"], dtype=np.float32)
        pixels = (pixels - mean) / std

    # HWC -> CHW, add batch dimension
    pixels = np.transpose(pixels, (2, 0, 1))
    return np.expand_dims(pixels, axis=0).astype(np.float32)


def build_engine(onnx_path: str, engine_path: str, fp16: bool = False) -> trt.ICudaEngine:
    """Build a TensorRT engine from an ONNX file and save it to disk.

    Args:
        onnx_path: Path to the ONNX model.
        engine_path: Path to save the serialized engine.
        fp16: Whether to enable FP16 precision.

    Returns:
        The built TensorRT engine.
    """
    logger.info("Building TensorRT engine from: %s", onnx_path)
    if fp16:
        logger.info("  FP16 mode enabled")

    builder = trt.Builder(TRT_LOGGER)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, TRT_LOGGER)

    # parse_from_file resolves external weights paths relative to the ONNX
    # file directory, unlike parse() which reads raw bytes
    logger.info("  Parsing ONNX model...")
    if not parser.parse_from_file(onnx_path):
        for i in range(parser.num_errors):
            logger.error("  ONNX parse error: %s", parser.get_error(i))
        raise RuntimeError("Failed to parse ONNX model")

    logger.info("  Network inputs: %d, outputs: %d", network.num_inputs, network.num_outputs)
    for i in range(network.num_inputs):
        inp = network.get_input(i)
        logger.info("    Input  %d: %s %s %s", i, inp.name, inp.shape, inp.dtype)
    for i in range(network.num_outputs):
        out = network.get_output(i)
        logger.info("    Output %d: %s %s %s", i, out.name, out.shape, out.dtype)

    # Configure builder
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 2 << 30)  # 2 GB

    if fp16 and builder.platform_has_fast_fp16:
        config.set_flag(trt.BuilderFlag.FP16)
        logger.info("  FP16 enabled on this platform")
    elif fp16:
        logger.warning("  FP16 requested but not supported on this platform, using FP32")

    # Set optimization profiles for dynamic batch size
    profile = builder.create_optimization_profile()
    for i in range(network.num_inputs):
        inp = network.get_input(i)
        shape = list(inp.shape)
        min_shape = [1 if d <= 0 else d for d in shape]
        opt_shape = [1 if d <= 0 else d for d in shape]
        max_shape = [1 if d <= 0 else d for d in shape]
        profile.set_shape(inp.name, min_shape, opt_shape, max_shape)
    config.add_optimization_profile(profile)

    # Build engine
    logger.info("  Building engine (this may take several minutes)...")
    serialized_engine = builder.build_serialized_network(network, config)
    if serialized_engine is None:
        raise RuntimeError("Failed to build TensorRT engine")

    # Save to disk
    with open(engine_path, "wb") as f:
        f.write(serialized_engine)
    size_mb = Path(engine_path).stat().st_size / (1024 * 1024)
    logger.info("  Engine saved: %s (%.1f MB)", engine_path, size_mb)

    runtime = trt.Runtime(TRT_LOGGER)
    engine = runtime.deserialize_cuda_engine(serialized_engine)
    return engine


def load_engine(engine_path: str) -> trt.ICudaEngine:
    """Load a serialized TensorRT engine from disk.

    Args:
        engine_path: Path to the .engine file.

    Returns:
        The deserialized TensorRT engine.
    """
    logger.info("Loading TensorRT engine: %s", engine_path)
    runtime = trt.Runtime(TRT_LOGGER)
    with open(engine_path, "rb") as f:
        engine = runtime.deserialize_cuda_engine(f.read())
    if engine is None:
        raise RuntimeError(f"Failed to load engine from {engine_path}")
    return engine


def get_engine(onnx_path: str, fp16: bool = False, rebuild: bool = False) -> trt.ICudaEngine:
    """Get a TensorRT engine, building from ONNX if no cached engine exists.

    The engine is cached next to the ONNX file with a .engine suffix.

    Args:
        onnx_path: Path to the ONNX model.
        fp16: Whether to build/use an FP16 engine.
        rebuild: Force rebuild even if a cached engine exists.

    Returns:
        A TensorRT engine ready for inference.
    """
    onnx_p = Path(onnx_path)
    # with_suffix() only accepts a single dotted suffix (e.g. ".engine"), not "_fp16.engine"
    if fp16:
        engine_path = str(onnx_p.parent / f"{onnx_p.stem}_fp16.engine")
    else:
        engine_path = str(onnx_p.with_suffix(".engine"))

    if not rebuild and Path(engine_path).exists():
        return load_engine(engine_path)

    return build_engine(onnx_path, engine_path, fp16=fp16)


class TRTInference:
    """Manages TensorRT engine execution for image embedding extraction.

    Automatically discovers I/O bindings, allocates GPU memory, and handles
    combined (vision+text) models by providing dummy text inputs.

    Args:
        engine: A TensorRT engine (from build_engine or load_engine).
    """

    def __init__(self, engine: trt.ICudaEngine):
        self.engine = engine
        self.context = engine.create_execution_context()
        self.stream = cuda.Stream()

        self._inputs: Dict[str, dict] = {}
        self._outputs: Dict[str, dict] = {}
        # set_input_shape is only valid for INPUT tensors (not outputs like image_embedding).
        for i in range(engine.num_io_tensors):
            name = engine.get_tensor_name(i)
            if engine.get_tensor_mode(name) != trt.TensorIOMode.INPUT:
                continue
            shape = list(engine.get_tensor_shape(name))
            shape = [1 if d <= 0 else d for d in shape]
            self.context.set_input_shape(name, shape)

        for i in range(engine.num_io_tensors):
            name = engine.get_tensor_name(i)
            mode = engine.get_tensor_mode(name)
            shape = list(self.context.get_tensor_shape(name))
            dtype = trt.nptype(engine.get_tensor_dtype(name))
            shape = [1 if d <= 0 else d for d in shape]

            size = int(np.prod(shape)) * np.dtype(dtype).itemsize
            device_mem = cuda.mem_alloc(size)

            binding = {"name": name, "shape": shape, "dtype": dtype, "device": device_mem, "size": size}
            if mode == trt.TensorIOMode.INPUT:
                self._inputs[name] = binding
            else:
                self._outputs[name] = binding

        logger.info("  TRT Inputs:  %s", {n: (b["shape"], b["dtype"]) for n, b in self._inputs.items()})
        logger.info("  TRT Outputs: %s", {n: (b["shape"], b["dtype"]) for n, b in self._outputs.items()})

    def infer(self, pixel_values: np.ndarray) -> np.ndarray:
        """Run inference and return the L2-normalized image embedding.

        Args:
            pixel_values: Preprocessed image of shape [1, 3, H, W].

        Returns:
            L2-normalized embedding of shape [1, embedding_dim].
        """
        # Find the vision input
        image_input = None
        for name in self._inputs:
            if "image" in name.lower() or "pixel" in name.lower():
                image_input = name
                break
        if image_input is None:
            image_input = next(iter(self._inputs))

        # Copy image to device
        img_binding = self._inputs[image_input]
        host_buf = np.ascontiguousarray(pixel_values.astype(img_binding["dtype"]))
        cuda.memcpy_htod_async(img_binding["device"], host_buf, self.stream)

        # Provide dummy data for other inputs (text, attention_mask)
        for name, binding in self._inputs.items():
            if name == image_input:
                continue
            dummy = np.zeros(binding["shape"], dtype=binding["dtype"])
            cuda.memcpy_htod_async(binding["device"], np.ascontiguousarray(dummy), self.stream)

        # Set tensor addresses
        for name, binding in self._inputs.items():
            self.context.set_tensor_address(name, int(binding["device"]))
        for name, binding in self._outputs.items():
            self.context.set_tensor_address(name, int(binding["device"]))

        # Execute
        self.context.execute_async_v3(stream_handle=self.stream.handle)
        self.stream.synchronize()

        # Find image embedding output
        output_name = None
        for name in self._outputs:
            if "image" in name.lower():
                output_name = name
                break
        if output_name is None:
            output_name = next(iter(self._outputs))

        # Copy result back to host
        out_binding = self._outputs[output_name]
        host_output = np.empty(out_binding["shape"], dtype=out_binding["dtype"])
        cuda.memcpy_dtoh_async(host_output, out_binding["device"], self.stream)
        self.stream.synchronize()

        # L2 normalize
        norms = np.linalg.norm(host_output, axis=-1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        return host_output / norms

    def __del__(self) -> None:
        """Free CUDA memory."""
        for binding in list(self._inputs.values()) + list(self._outputs.values()):
            try:
                binding["device"].free()
            except Exception:
                pass


def embed_images(
    image_paths: List[str],
    model_name: str = "siglip2",
    onnx_path: Optional[str] = None,
    fp16: bool = False,
    rebuild: bool = False,
) -> Dict[str, np.ndarray]:
    """High-level function: embed one or more images and return a dict of embeddings.

    Builds or loads a TRT engine, preprocesses images, and runs inference.

    Args:
        image_paths: List of image file paths.
        model_name: Model key, one of MODEL_CONFIGS.
        onnx_path: Override the default ONNX path for the model.
        fp16: Whether to build/use an FP16 engine.
        rebuild: Force rebuild the engine.

    Returns:
        Dict mapping image_path -> L2-normalized embedding (1-D numpy array).
    """
    config = get_model_config(model_name)
    path = onnx_path or config["onnx_path"]
    engine = get_engine(path, fp16=fp16, rebuild=rebuild)
    inferencer = TRTInference(engine)

    results = {}
    for image_path in image_paths:
        pixel_values = preprocess_image(image_path, config)
        embedding = inferencer.infer(pixel_values).squeeze(0)
        results[image_path] = embedding

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate image embeddings from SigLIP 2 or RADIO-CLIP ONNX models using TensorRT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    python embed_image_trt.py --model siglip2 --image patch.jpg
    python embed_image_trt.py --model radio-clip --image patch.jpg --fp16
    python embed_image_trt.py --model siglip2 --image img1.jpg img2.jpg --output embeddings.npy
    python embed_image_trt.py --model radio-clip --image patch.jpg --rebuild
""",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=list(MODEL_CONFIGS.keys()),
        required=True,
        help="Model to use: siglip2 or radio-clip",
    )
    parser.add_argument(
        "--onnx",
        type=str,
        default=None,
        help="Override ONNX model path (default: auto-resolved per model)",
    )
    parser.add_argument(
        "--image",
        type=str,
        nargs="+",
        required=True,
        help="Path(s) to input image(s)",
    )
    parser.add_argument(
        "--fp16",
        action="store_true",
        help="Build/use FP16 TensorRT engine (faster, slightly less precise)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild the TensorRT engine even if cached",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save embeddings to file (.npy for numpy, .json for JSON)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print embeddings as JSON to stdout",
    )
    args = parser.parse_args()

    config = get_model_config(args.model)
    onnx_path = Path(args.onnx or config["onnx_path"])
    if not onnx_path.exists():
        logger.error("ONNX file not found: %s", onnx_path)
        sys.exit(1)

    # Validate image paths
    image_paths = []
    for p in args.image:
        path = Path(p)
        if not path.exists():
            logger.error("Image not found: %s", path)
            sys.exit(1)
        image_paths.append(str(path))

    logger.info("Model: %s (%s)", args.model, config["description"])

    # Build or load TensorRT engine
    engine = get_engine(str(onnx_path), fp16=args.fp16, rebuild=args.rebuild)
    inferencer = TRTInference(engine)

    # Process each image
    all_embeddings = {}
    for image_path in image_paths:
        logger.info("Processing: %s", image_path)
        pixel_values = preprocess_image(image_path, config)
        embedding = inferencer.infer(pixel_values)
        embedding = embedding.squeeze(0)
        all_embeddings[image_path] = embedding
        logger.info("  Embedding shape: %s, norm: %.4f", embedding.shape, np.linalg.norm(embedding))

    # Output results
    if args.output:
        out_path = Path(args.output)
        if out_path.suffix == ".json":
            data = {k: v.tolist() for k, v in all_embeddings.items()}
            with open(out_path, "w") as f:
                json.dump(data, f, indent=2)
        else:
            stacked = np.stack(list(all_embeddings.values()))
            np.save(out_path, stacked)
        logger.info("Saved embeddings to %s", out_path)

    if args.json:
        data = {k: v.tolist() for k, v in all_embeddings.items()}
        print(json.dumps(data, indent=2))

    if not args.output and not args.json:
        for path, emb in all_embeddings.items():
            print(f"{path}: shape={emb.shape}, first 5 values={emb[:5]}")
