# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import base64
import concurrent.futures
import io
import os
import re
import sys
import time as _time
import uuid
from typing import List, Optional

import numpy
import torch
from langchain_openai import AzureChatOpenAI
from PIL import Image

from common.chunk_info import ChunkInfo
from common.logger import TimeMeasure, logger
from common.service_exception import ServiceException
from models.base_vlm_model import (
    BaseVlmModel,
    InputConfig,
    VlmGenerationConfig,
    VlmModelOutput,
)

OPENAI_RECONNECT_ATTEMPTS = 3
DEFAULT_MAX_PARALLEL_REQUESTS = 10


def _remote_video_input_enabled() -> bool:
    raw_value = os.environ.get("REMOTE_VIDEO_INPUT", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def strip_thinking_tags(content):
    """
    Strip thinking tags from content and extract reasoning description.

    Handles three cases:
    1. Both <think> and </think> tags present
    2. Only </think> tag present (opening tag was in prompt)
    3. Only <think> tag present (incomplete generation)

    Args:
        content (str): The raw content from the model

    Returns:
        tuple: (cleaned_content, reasoning_description)
    """
    response = content.strip()
    reasoning_description = ""

    if "<think>" in response and "</think>" in response:
        reasoning_match = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL)
        if reasoning_match:
            reasoning_description = reasoning_match.group(1).strip()
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    elif "</think>" in response:
        think_end = response.find("</think>")
        reasoning_description = response[:think_end].strip()
        response = response[think_end + len("</think>") :]
    elif "<think>" in response:
        think_start = response.find("<think>")
        reasoning_description = response[think_start + len("<think>") :].strip()
        logger.warning("Incomplete reasoning response generated. Try increasing max tokens")
        response = response[:think_start].strip()

    for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
        response = response.replace(tag, "")

    response = re.sub(r"^###\s*Response\s*Json\s*", "", response, flags=re.IGNORECASE)

    return response.strip(), reasoning_description


def _decode_jpegs_gpu(numpy_arrays):
    """Decode JPEG byte arrays on GPU using torchvision nvjpeg (CUDA).

    Returns list of (3, H, W) uint8 CUDA tensors, or None if unavailable.
    Frames stay on the GPU — no CPU round-trip.
    """
    try:
        import torchvision.io as tvio

        if not torch.cuda.is_available():
            logger.info("MP4 decode: CUDA not available, using CPU JPEG decode.")
            return None

        frames = []
        with TimeMeasure(f"MP4 decode: nvjpeg GPU ({len(numpy_arrays)} frames)"):
            for numpy_array in numpy_arrays:
                raw = torch.from_numpy(numpy_array.flatten().copy())
                # decode_jpeg with device="cuda" uses nvjpeg — result stays on GPU
                rgb = tvio.decode_jpeg(raw, tvio.ImageReadMode.RGB, device="cuda")
                frames.append(rgb)  # (3, H, W) uint8 CUDA tensor
        logger.info("MP4 decode: nvjpeg GPU succeeded (%d frames, CUDA tensors).", len(frames))
        return frames if frames else None
    except Exception as e:
        logger.info("MP4 decode: nvjpeg GPU failed (%s); falling back to CPU.", e)
        return None


def _decode_jpegs_cpu(numpy_arrays):
    """Decode JPEG byte arrays on CPU using OpenCV.

    Returns list of (H, W, 3) uint8 RGB numpy arrays.
    """
    import numpy as _numpy

    try:
        import cv2
    except ImportError:
        logger.warning("OpenCV not available for CPU JPEG decode.")
        return []

    frames = []
    with TimeMeasure(f"MP4 CPU: cv2 JPEG decode ({len(numpy_arrays)} frames)"):
        for numpy_array in numpy_arrays:
            encoded = _numpy.frombuffer(numpy_array.tobytes(), dtype=_numpy.uint8)
            frame_bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if frame_bgr is not None:
                frames.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    return frames


def _rgb_to_nv12(rgb_frame):
    """Convert (H, W, 3) uint8 RGB numpy array to NV12 (H*3//2, W) uint8 array.

    NV12 layout: full-resolution Y plane followed by 2x2-subsampled interleaved UV plane.
    Dimensions are cropped to even values if needed (H.264 requirement).
    """
    import numpy as _numpy

    h, w = rgb_frame.shape[:2]
    h = h - (h % 2)
    w = w - (w % 2)
    rgb = rgb_frame[:h, :w].astype(_numpy.float32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    # BT.601 limited-range YCbCr
    y = (0.257 * r + 0.504 * g + 0.098 * b + 16.0).clip(0, 255).astype(_numpy.uint8)
    u = (-0.148 * r - 0.291 * g + 0.439 * b + 128.0).clip(0, 255).astype(_numpy.uint8)
    v = (0.439 * r - 0.368 * g - 0.071 * b + 128.0).clip(0, 255).astype(_numpy.uint8)

    # 2x2 subsampling (nearest)
    u_sub = u[0::2, 0::2]  # (H//2, W//2)
    v_sub = v[0::2, 0::2]  # (H//2, W//2)

    # Interleaved UV plane for NV12: (H//2, W)
    uv = _numpy.empty((h // 2, w), dtype=_numpy.uint8)
    uv[:, 0::2] = u_sub
    uv[:, 1::2] = v_sub

    return _numpy.vstack([y, uv])


def _rgb_to_nv12_tensor(rgb_tensor):
    """Convert a (3, H, W) uint8 CUDA tensor (RGB) to a (H*3//2, W) uint8 CUDA tensor (NV12).

    All colour-space arithmetic runs on the GPU — no CPU transfer.
    H and W are cropped to even values if needed (H.264 requirement).
    """
    _, h, w = rgb_tensor.shape
    h = h - (h % 2)
    w = w - (w % 2)

    rgb_f = rgb_tensor[:, :h, :w].float()  # (3, H, W) float32, on GPU
    r, g, b = rgb_f[0], rgb_f[1], rgb_f[2]

    # BT.601 limited-range YCbCr
    y = (0.257 * r + 0.504 * g + 0.098 * b + 16.0).clamp(0, 255).to(torch.uint8)
    u = (-0.148 * r - 0.291 * g + 0.439 * b + 128.0).clamp(0, 255).to(torch.uint8)
    v = (0.439 * r - 0.368 * g - 0.071 * b + 128.0).clamp(0, 255).to(torch.uint8)

    # 2x2 subsampling (nearest neighbour)
    u_sub = u[0::2, 0::2]  # (H//2, W//2)
    v_sub = v[0::2, 0::2]  # (H//2, W//2)

    # Interleaved UV plane: (H//2, W)
    uv = torch.empty(h // 2, w, dtype=torch.uint8, device=rgb_tensor.device)
    uv[:, 0::2] = u_sub
    uv[:, 1::2] = v_sub

    return torch.cat([y, uv], dim=0)  # (H*3//2, W)


def _encode_h264_nvenc(frames_rgb, fps, output_path):
    """Encode RGB frames to H.264 MP4 using NVENC via PyNvVideoCodec.

    Frames are kept as CUDA tensors throughout:
      (3, H, W) CUDA  →  _rgb_to_nv12_tensor  →  NV12 CUDA  →  EncodeSurface (zero-copy)

    Falls back to EncodeFromCPU if EncodeSurface is not supported by the
    installed PyNvVideoCodec version.

    Args:
        frames_rgb: list of (3, H, W) uint8 CUDA tensors  (from _decode_jpegs_gpu)
        fps: output frame rate
        output_path: destination .mp4 file path

    Returns:
        True on success, None if unavailable or failed.
    """
    try:
        import PyNvVideoCodec as nvc

        nvc_version = getattr(nvc, "__version__", "unknown")
        logger.info("MP4 encode: PyNvVideoCodec available (version=%s).", nvc_version)
    except ImportError:
        logger.info("MP4 encode: PyNvVideoCodec not available; using CPU fallback.")
        return None

    try:
        import av
    except ImportError:
        logger.info("MP4 encode: PyAV (av) not available; cannot mux H.264 — using CPU fallback.")
        return None

    try:
        # Derive dimensions from the first CUDA tensor: (3, H, W)
        _, h, w = frames_rgb[0].shape
        h = h - (h % 2)
        w = w - (w % 2)
        fps_int = max(1, round(fps))
        gpu_id = torch.cuda.current_device() if torch.cuda.is_available() else 0

        enc_params = {
            "codec": "h264",
            "preset": "P4",
            "tuning_info": "hq",
            "s": f"{w}x{h}",
            "fps": str(fps_int),
            "bitrate": "4M",
            "profile": "high",
            "gop": "30",
        }

        logger.info(
            "MP4 encode: NVENC starting — gpu=%d resolution=%dx%d fps=%d frames=%d",
            gpu_id,
            w,
            h,
            fps_int,
            len(frames_rgb),
        )

        # PyNvVideoCodec 2.x: PyNvEncoder(params, PixelFormat, gpu_id)
        encoder = nvc.PyNvEncoder(enc_params, nvc.PixelFormat.NV12, gpu_id)

        all_packets = []
        _use_surface = True  # prefer zero-copy EncodeSurface on first frame
        _encode_method = "EncodeSurface (zero-copy)"  # updated if fallback occurs
        with TimeMeasure(f"MP4 encode: NVENC H264 ({len(frames_rgb)} frames)"):
            for rgb_tensor in frames_rgb:
                # BT.601 RGB→NV12 on GPU — no CPU transfer
                nv12_cuda = _rgb_to_nv12_tensor(rgb_tensor)  # (H*3//2, W) uint8 CUDA

                packets = []
                if _use_surface:
                    try:
                        # EncodeSurface accepts objects with __cuda_array_interface__
                        encoder.EncodeSurface(nv12_cuda, packets)
                    except (AttributeError, TypeError):
                        # This PyNvVideoCodec build doesn't expose EncodeSurface;
                        # fall back to EncodeFromCPU for remaining frames
                        logger.info(
                            "MP4 encode: EncodeSurface not supported by this "
                            "PyNvVideoCodec build; switching to EncodeFromCPU."
                        )
                        _use_surface = False
                        _encode_method = "EncodeFromCPU (NV12 copy to CPU)"
                        encoder.EncodeFromCPU(nv12_cuda.cpu().numpy().flatten(), packets)
                else:
                    encoder.EncodeFromCPU(nv12_cuda.cpu().numpy().flatten(), packets)

                all_packets.extend(packets)

            # Flush encoder pipeline
            packets = []
            encoder.Flush(packets)
            all_packets.extend(packets)

        if not all_packets:
            logger.warning("MP4 encode: NVENC produced empty bitstream.")
            return None

        logger.info(
            "MP4 encode: NVENC succeeded via %s (%d packets).", _encode_method, len(all_packets)
        )

        # Mux raw H.264 NAL packets into MP4 using PyAV
        with TimeMeasure("MP4 encode: PyAV mux"):
            with av.open(output_path, "w", format="mp4") as container:
                stream = container.add_stream("h264", rate=fps_int)
                stream.width = w
                stream.height = h
                stream.pix_fmt = "yuv420p"
                for i, pkt_bytes in enumerate(all_packets):
                    pkt = av.Packet(bytes(pkt_bytes))
                    pkt.stream = stream
                    pkt.pts = i
                    pkt.dts = i
                    container.mux(pkt)

        return True

    except Exception as e:
        logger.warning("MP4 encode: NVENC failed (%s); will use CPU fallback.", e)
        return None


def _encode_h264_cpu(frames_rgb, fps, output_path):
    """Encode RGB frames to H.264 MP4 using OpenCV.

    Tries codecs in order: H264 → avc1 → mp4v (last resort).

    Args:
        frames_rgb: list of (H, W, 3) uint8 RGB numpy arrays
        fps: output frame rate
        output_path: destination .mp4 file path

    Returns:
        True on success, None if failed.
    """
    try:
        import cv2
    except ImportError:
        logger.warning("MP4 encode: OpenCV not available for CPU H.264 encode.")
        return None

    if not frames_rgb:
        return None

    h, w = frames_rgb[0].shape[:2]
    h = h - (h % 2)
    w = w - (w % 2)
    fps_int = max(1, round(fps))

    writer = None
    chosen_codec = None
    for fourcc_str in ("H264", "avc1", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
        candidate = cv2.VideoWriter(output_path, fourcc, fps_int, (w, h))
        if candidate.isOpened():
            writer = candidate
            chosen_codec = fourcc_str
            break
        candidate.release()

    if writer is None or not writer.isOpened():
        logger.warning("MP4 encode: cv2.VideoWriter failed with all codecs (H264/avc1/mp4v).")
        return None

    logger.info(
        "MP4 encode: CPU OpenCV codec=%s resolution=%dx%d fps=%d frames=%d",
        chosen_codec,
        w,
        h,
        fps_int,
        len(frames_rgb),
    )
    with TimeMeasure(f"MP4 encode: CPU cv2 {chosen_codec} ({len(frames_rgb)} frames)"):
        for rgb_frame in frames_rgb:
            bgr = cv2.cvtColor(rgb_frame[:h, :w], cv2.COLOR_RGB2BGR)
            writer.write(bgr)
        writer.release()

    return True


def video_embeds_to_mp4_base64(tensor, frame_times=None, output_dir="/tmp/rtvi/openai_compat"):
    """Convert a tensor of JPEG-encoded frames to an H.264 MP4 and return as base64.

    Pipeline (GPU first, CPU fallback):
      GPU:  torchvision nvjpeg decode  →  PyNvVideoCodec NVENC H.264  →  PyAV MP4 mux
      CPU:  OpenCV JPEG decode         →  OpenCV VideoWriter H.264 (avc1/H264/mp4v)
    """
    # Handle both stacked tensor (1, N, bytes) and list of individual frame tensors
    if isinstance(tensor, (list, tuple)):
        logger.info(
            "MP4 pipeline: input is list of %d frames (type=%s each)",
            len(tensor),
            type(tensor[0]).__name__ if tensor else "empty",
        )
        if not tensor:
            return None, None
        numpy_arrays = []
        for t in tensor:
            if isinstance(t, torch.Tensor):
                numpy_arrays.append(t.cpu().numpy().flatten())
            else:
                numpy_arrays.append(numpy.asarray(t, dtype=numpy.uint8).flatten())
    elif isinstance(tensor, torch.Tensor):
        with TimeMeasure("MP4: tensor_to_numpy"):
            numpy_arrays = jpeg_single_tensor_to_array_of_numpys(tensor)
    else:
        logger.warning(
            "video_embeds is not a tensor or list (type=%s); skipping mp4 conversion.",
            type(tensor).__name__,
        )
        return None, None

    if not numpy_arrays:
        logger.warning("No frames in tensor; mp4 conversion skipped.")
        return None, None

    fps = 1.0
    if frame_times and len(frame_times) > 1:
        duration = float(frame_times[-1]) - float(frame_times[0])
        if duration > 0:
            fps = max(1.0, (len(numpy_arrays) - 1) / duration)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"video_embeds_{uuid.uuid4().hex}.mp4")

    # Detect if frames are raw pixels or JPEG-encoded bytes.
    # After jpeg_single_tensor_to_array_of_numpys, frames are 1D numpy arrays regardless.
    # Check JPEG magic bytes (0xFF 0xD8) on the first frame to distinguish.
    first = numpy_arrays[0].flatten()
    is_jpeg = len(first) >= 2 and first[0] == 0xFF and first[1] == 0xD8
    logger.debug(
        "MP4 pipeline: %d frames, first frame shape=%s dtype=%s size=%d, "
        "first_bytes=[0x%02X,0x%02X,...], is_jpeg=%s",
        len(numpy_arrays),
        numpy_arrays[0].shape,
        numpy_arrays[0].dtype,
        len(first),
        first[0] if len(first) > 0 else 0,
        first[1] if len(first) > 1 else 0,
        is_jpeg,
    )

    if not is_jpeg:
        # Raw pixel data (flattened H*W*3). Try to reshape to (H, W, 3).
        pixel_count = len(first)
        if pixel_count % 3 != 0:
            logger.warning(
                "MP4 pipeline: frame size %d not divisible by 3; cannot reshape as RGB.",
                pixel_count,
            )
            return None, None

        total_pixels = pixel_count // 3
        # Try configured dimensions first, then try square
        h, w = 0, 0
        env_h = int(os.environ.get("VLM_INPUT_HEIGHT", "0") or "0")
        env_w = int(os.environ.get("VLM_INPUT_WIDTH", "0") or "0")
        if env_h > 0 and env_w > 0 and env_h * env_w == total_pixels:
            h, w = env_h, env_w
        else:
            # Try square dimensions
            sq = int(total_pixels**0.5)
            if sq * sq == total_pixels:
                h, w = sq, sq

        if h == 0 or w == 0:
            logger.warning(
                "MP4 pipeline: cannot infer dimensions for %d raw pixels (tried %dx%d, sqrt).",
                pixel_count,
                env_h,
                env_w,
            )
            return None, None

        logger.info(
            "MP4 pipeline: raw pixel frames detected (%dx%d), reshaping %d frames.",
            w,
            h,
            len(numpy_arrays),
        )
        rgb_frames = [f.flatten().astype(numpy.uint8).reshape(h, w, 3) for f in numpy_arrays]

    try:
        if not is_jpeg:
            # Raw pixel frames — encode directly to MP4 (skip JPEG decode)
            if not _encode_h264_cpu(rgb_frames, fps, output_path):
                logger.warning("MP4 pipeline: CPU encode failed for raw pixel frames.")
                return None, None
        else:
            # JPEG-encoded bytes — decode then encode
            encoded = False
            # --- GPU path ---
            frames_rgb = _decode_jpegs_gpu(numpy_arrays)
            if frames_rgb is not None:
                if _encode_h264_nvenc(frames_rgb, fps, output_path):
                    logger.info("MP4 pipeline: nvjpeg decode + NVENC encode (full GPU).")
                    encoded = True
                else:
                    logger.info("MP4 pipeline: nvjpeg decode (GPU) + OpenCV encode (CPU).")
                    numpy_frames = [t.permute(1, 2, 0).cpu().numpy() for t in frames_rgb]
                    encoded = _encode_h264_cpu(numpy_frames, fps, output_path)

            # --- CPU fallback (decode + encode) ---
            if not encoded:
                logger.info("MP4 pipeline: full CPU fallback (OpenCV decode + OpenCV encode).")
                frames_rgb = _decode_jpegs_cpu(numpy_arrays)
                if not frames_rgb:
                    logger.warning("MP4 pipeline: no frames decoded; mp4 conversion skipped.")
                    return None, None
                if not _encode_h264_cpu(frames_rgb, fps, output_path):
                    return None, None

        with open(output_path, "rb") as f:
            mp4_bytes = f.read()
        return base64.b64encode(mp4_bytes).decode("utf-8"), fps

    except OSError:
        logger.warning("Failed to read mp4 for base64 encoding: %s", output_path)
        return None, None
    finally:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            logger.debug("Failed to delete temporary mp4 file: %s", output_path)


def jpeg_single_tensor_to_array_of_numpys(tensor):
    """
    Takes a PyTorch tensor of shape (1, 10, N) and returns an array of 10 numpy arrays.
    (1,10) are example lengths - could be any
    1: number of chunks
    10: n_frms in each chunk
    """
    # Unstack the tensor into 10 PyTorch tensors
    unstacked_tensors = tensor.squeeze(0).unbind(0)

    # Convert the PyTorch tensors to numpy arrays
    numpy_arrays = [t.cpu().numpy() for t in unstacked_tensors]

    return numpy_arrays


def tensor_to_base64_jpeg(numpy_arrays, idx=0):
    """
    Selects one JPEG at index=idx from a PyTorch tensor containing N X NumPy arrays
    representing N JPEG images and converts to a base64 encoded string.

    Args:
        numpy_arrays: Either a torch.Tensor or list of numpy arrays containing JPEG-encoded bytes.
        idx: Index of the JPEG to extract.

    Returns:
        str: The base64 encoded string representing the JPEG image at idx.
    """
    # Handle both tensor and list inputs
    if isinstance(numpy_arrays, torch.Tensor):
        # Convert tensor to numpy array
        numpy_array = numpy_arrays[idx].cpu().numpy()
    else:
        # Assume it's already a list/array of numpy arrays
        numpy_array = numpy_arrays[idx]

    # Ensure the array is uint8 and contiguous
    if numpy_array.dtype != numpy.uint8:
        numpy_array = numpy_array.astype(numpy.uint8)

    # Flatten to 1D if needed (for JPEG bytes, should be 1D array)
    numpy_array = numpy_array.flatten()

    # Check if bytes are JPEG (magic: 0xFF 0xD8) or raw pixels
    is_jpeg = len(numpy_array) >= 2 and numpy_array[0] == 0xFF and numpy_array[1] == 0xD8

    if is_jpeg:
        # JPEG bytes — validate and re-encode
        encoded_image_bytes = numpy_array.tobytes()
        try:
            img = Image.open(io.BytesIO(encoded_image_bytes))
            img.verify()
            img = Image.open(io.BytesIO(encoded_image_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            output_buffer = io.BytesIO()
            img.save(output_buffer, format="JPEG", quality=95)
            encoded_image_bytes = output_buffer.getvalue()
        except Exception as e:
            error_msg = (
                f"Failed to validate/re-encode JPEG at index {idx}. "
                f"Error: {e}. Array shape: {numpy_array.shape}, dtype: {numpy_array.dtype}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg) from e
    else:
        # Raw pixel data (flattened H*W*3) — reshape and encode to JPEG
        pixel_count = len(numpy_array)
        if pixel_count % 3 != 0:
            raise ValueError(
                f"Frame at index {idx} is not JPEG and size {pixel_count} is not divisible by 3."
            )
        total_pixels = pixel_count // 3
        h, w = 0, 0
        env_h = int(os.environ.get("VLM_INPUT_HEIGHT", "0") or "0")
        env_w = int(os.environ.get("VLM_INPUT_WIDTH", "0") or "0")
        if env_h > 0 and env_w > 0 and env_h * env_w == total_pixels:
            h, w = env_h, env_w
        else:
            sq = int(total_pixels**0.5)
            if sq * sq == total_pixels:
                h, w = sq, sq
        if h == 0 or w == 0:
            raise ValueError(
                f"Cannot infer dimensions for {pixel_count} raw pixels at index {idx}."
            )
        rgb = numpy_array.reshape(h, w, 3)
        img = Image.fromarray(rgb, mode="RGB")
        output_buffer = io.BytesIO()
        img.save(output_buffer, format="JPEG", quality=95)
        encoded_image_bytes = output_buffer.getvalue()

    # Encode the bytes as base64
    base64_encoded = base64.b64encode(encoded_image_bytes)

    # Decode the base64 bytes to a string
    base64_string = base64_encoded.decode("utf-8")

    return base64_string


class CompOpenAIModel(BaseVlmModel):
    def configure_azure_openai(
        self, key=None, azureEndpointConfigured=False, nvSecretConfigured=False
    ):

        # Configure endpoint
        self._endpoint = ""
        if azureEndpointConfigured:
            # The environment variable is set to a valid string
            self._endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
            logger.info(f"Azure OpenAI Endpoint: {self._endpoint}")

            # Run your code here if the environment variable is set
            # ...
        elif nvSecretConfigured:
            from models.openai_compat.internal.util import endpoint

            self._endpoint = endpoint
        else:
            # The environment variable is not set or is an empty string
            logger.info("Azure OpenAI Endpoint environment variable is not set or is empty.")
        os.environ["AZURE_OPENAI_ENDPOINT"] = self._endpoint

        # configure key
        if nvSecretConfigured:
            if key is None:
                from models.openai_compat.internal.util import get_nv_oauth_token

                self._key = get_nv_oauth_token(120)
                os.environ["AZURE_OPENAI_API_KEY"] = self._key
            else:
                self._key = key
                os.environ["AZURE_OPENAI_API_KEY"] = self._key

        if self._model_name:
            self._model = AzureChatOpenAI(model=self._model_name, deployment_name=self._model_name)

    # Configure common environments between Azure Open AI and Open AI APIs
    def configure_openai_common(self):
        if "OPENAI_API_VERSION" in os.environ and os.environ["OPENAI_API_VERSION"]:
            self._openai_api_version = os.environ["OPENAI_API_VERSION"]
        else:
            logger.warning(
                "OPENAI_API_VERSION is not configured;"
                " May be required for certain model deployments;"
            )
        if "AZURE_OPENAI_API_VERSION" in os.environ and os.environ["AZURE_OPENAI_API_VERSION"]:
            self._azure_openai_api_version = os.environ["AZURE_OPENAI_API_VERSION"]
        else:
            logger.info(
                "AZURE_OPENAI_API_VERSION is not configured;"
                " May be required for certain model deployments;"
            )
        # Model config:
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            logger.info(
                f"VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is configured to"
                f" {os.environ['VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME']}"
            )
            self._model_name = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        else:
            logger.error("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is not configured")

    def configure_openai(self):
        from openai import OpenAI

        if "OPENAI_API_KEY" in os.environ and os.environ["OPENAI_API_KEY"]:
            logger.info("OPENAI_API_KEY configured")
        else:
            logger.error("OPENAI_API_KEY not configured")
        if self._model_name:
            if "VIA_VLM_ENDPOINT" in os.environ and os.environ["VIA_VLM_ENDPOINT"]:
                self._endpoint = base_url = os.environ["VIA_VLM_ENDPOINT"]
                logger.info(f"VIA_VLM_ENDPOINT is configured to {base_url}")
                if "VIA_VLM_API_KEY" in os.environ and os.environ["VIA_VLM_API_KEY"]:
                    logger.info("VIA_VLM_API_KEY is configured")
                    self._key = os.environ["VIA_VLM_API_KEY"]
                    self._client = OpenAI(
                        base_url=base_url, api_key=self._key, max_retries=OPENAI_RECONNECT_ATTEMPTS
                    )
                else:
                    logger.info("VIA_VLM_API_KEY is not configured; will try use OPENAI_API_KEY")
                    self._client = OpenAI(base_url=base_url, max_retries=OPENAI_RECONNECT_ATTEMPTS)
            else:
                logger.info("VIA_VLM_ENDPOINT is not configured; using OpenAI() default")
                self._client = OpenAI(max_retries=OPENAI_RECONNECT_ATTEMPTS)
                self._endpoint = "https://api.openai.com/v1/"  # default
                if not os.environ.get("OPENAI_API_KEY", ""):
                    raise Exception("OPENAI_API_KEY not configured")

    def init_gpt_4(self, key=None):
        self._key = key

        # Model config:
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            self._model_name = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]

        self.configure_openai_common()
        self._azureEndpointConfigured = (
            "AZURE_OPENAI_ENDPOINT" in os.environ and os.environ["AZURE_OPENAI_ENDPOINT"]
        )

        try:
            from models.openai_compat.internal.util import is_nv_secret_configured

            self._nvSecretConfigured = is_nv_secret_configured()
        except ModuleNotFoundError:
            self._nvSecretConfigured = False

        if self._azureEndpointConfigured or self._nvSecretConfigured:
            self.configure_azure_openai(
                key,
                azureEndpointConfigured=self._azureEndpointConfigured,
                nvSecretConfigured=self._nvSecretConfigured,
            )
        else:
            self.configure_openai()

    def _initialize_model(self, **kwargs):
        """Initialize the OpenAI compatible model"""
        self._model_name = None
        self._model = None
        self._client = None
        self._endpoint = ""

        self.init_gpt_4()
        # Overwrite environment with final selected endpoint
        logger.info(f"endpoint is {self._endpoint}")
        os.environ["VIA_VLM_ENDPOINT"] = self._endpoint

        # Initialize thread pool for parallel requests
        max_parallel = int(
            os.environ.get("OPENAI_MAX_PARALLEL_REQUESTS", DEFAULT_MAX_PARALLEL_REQUESTS)
        )
        logger.info(f"Initializing OpenAI thread pool with max_workers={max_parallel}")
        self._output_tpool = concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel)

    def warmup(self):
        # Call the synchronous version directly for warmup
        self._generate_sync(
            query="",
            chunks=[[]],
            video_frames=[[]],
            video_frames_times=[[]],
            generation_config=None,
        )

    @property
    def model_name(self):
        return self._model_name

    @property
    def model_config(self):
        return None

    def get_conv(self):
        return self._conv.copy()

    @staticmethod
    def get_model_info(model_path: str, vlm_model_type: str = ""):
        api_type = "openai"
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            id = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        else:
            id = "ModelNotLoaded"
            logger.error("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is not configured")
        if "VIA_VLM_ENDPOINT" in os.environ and os.environ["VIA_VLM_ENDPOINT"]:
            owned_by = os.environ["VIA_VLM_ENDPOINT"]
            owned_by = "".join(
                char.replace(".", "-").replace("/", "-")
                for char in owned_by
                if char.isalnum() or char in "./"
            )
        else:
            owned_by = "ModelNotLoaded"
            logger.info("VIA_VLM_ENDPOINT is not configured")
        return id, api_type, owned_by

    @staticmethod
    def get_input_config(model_path: str, vlm_model_type: str = "") -> InputConfig:
        """Get input-specific configuration parameters for CompOpenAIModel."""
        return InputConfig(
            num_frames=10,
            use_jpeg_encoding=True,  # For OpenAI compatible models, JPEG images are used
            width=0,
            height=0,
        )

    def _generate_sync(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: List[List[float]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> List[VlmModelOutput]:
        """Internal synchronous generation method.

        Args:
            query: Prompt for the VLM model
            chunks: List of chunk information
            video_frames: List of video frame tensors
            video_frames_times: List of video frame times
            generation_config: VLM generation config. Defaults to None.
            **kwargs: Additional keyword arguments for future extensibility and API compatibility
                     across different model implementations. Currently unused but preserved for
                     maintaining consistent interface across all model classes.

        Returns:
            List of responses for the batch of chunks
        """
        query_text = query

        logger.debug(f"query is {str(query_text)}")
        model_outputs = []

        # Get generation config with defaults
        config = generation_config or VlmGenerationConfig()

        # Set the seed
        numpy.random.seed(config.seed)
        torch.manual_seed(config.seed)
        torch.cuda.manual_seed_all(config.seed)

        if chunks:
            if len(video_frames_times) != len(chunks):
                logger.error("chunk size not matching in openai-compat generate")

        is_remote_video_input = _remote_video_input_enabled()

        for tidx, video_frames_times_ in enumerate(video_frames_times):
            is_single_image = int(len(video_frames_times_)) == 1
            num_of_frames_in_one_chunk = int(len(video_frames_times_))
            video_list = []
            fps = None
            mp4_duration = None
            nim_duration = None

            if is_single_image or not is_remote_video_input:
                video_list = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "data:image/jpeg;base64,"
                                + tensor_to_base64_jpeg(video_frames[tidx], j)
                            ),
                            "detail": "auto",
                        },
                    }
                    for j in range(num_of_frames_in_one_chunk)
                ]
            elif is_remote_video_input:
                _mp4_t0 = _time.time()
                mp4_base64, fps = video_embeds_to_mp4_base64(
                    video_frames[tidx], video_frames_times_
                )
                mp4_duration = _time.time() - _mp4_t0
                logger.debug(
                    "MP4 base64 encoding took %.3fs for chunk %d (%d frames)",
                    mp4_duration,
                    tidx,
                    num_of_frames_in_one_chunk,
                )
                if mp4_base64:
                    video_list = [
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{mp4_base64}"},
                            "fps": fps,
                        }
                    ]
                else:
                    # MP4 encoding failed — fall back to individual images
                    # NIM allows max 10 images; uniformly sample if more frames
                    max_nim_images = 10
                    frame_indices = list(range(num_of_frames_in_one_chunk))
                    if num_of_frames_in_one_chunk > max_nim_images:
                        import torch as _torch

                        frame_indices = (
                            _torch.linspace(0, num_of_frames_in_one_chunk - 1, max_nim_images)
                            .long()
                            .tolist()
                        )
                        logger.warning(
                            "MP4 encoding failed for chunk %d — sending %d/%d frames as images",
                            tidx,
                            max_nim_images,
                            num_of_frames_in_one_chunk,
                        )
                    video_list = []
                    for j in frame_indices:
                        try:
                            b64 = tensor_to_base64_jpeg(video_frames[tidx], j)
                            video_list.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64}",
                                        "detail": "auto",
                                    },
                                }
                            )
                        except (ValueError, Exception) as e:
                            logger.warning(
                                "Failed to encode frame %d of chunk %d as JPEG: %s",
                                j,
                                tidx,
                                e,
                            )

            string_of_times = ""
            string_timestamp = ""
            time_format_str = ""

            for j in range(num_of_frames_in_one_chunk):
                if chunks:
                    if tidx <= len(chunks):
                        string_timestamp = chunks[tidx].get_timestamp(video_frames_times_[j])
                        if not time_format_str:
                            if chunks[tidx].file.startswith("rtsp://"):
                                time_format_str = " at timestamps in RFC3339 format"
                            else:
                                time_format_str = " at timestamps in seconds"
                    else:
                        logger.error("Chunk ID going out of chunk size")
                        string_timestamp = str(video_frames_times_[j])
                        time_format_str = " at timestamps in seconds"
                else:
                    string_timestamp = str(video_frames_times_[j])
                    time_format_str = " at timestamps in seconds"

                string_of_times += "<" + string_timestamp + "> "

            if video_frames_times_ and len(video_frames_times_) > 0 and is_remote_video_input:
                first_ts = (
                    chunks[tidx].get_timestamp(video_frames_times_[0])
                    if chunks
                    else str(video_frames_times_[0])
                )
                last_ts = (
                    chunks[tidx].get_timestamp(video_frames_times_[-1])
                    if chunks
                    else str(video_frames_times_[-1])
                )
                frame_mapping = (
                    f"Frame 1 corresponds to timestamp {first_ts} seconds, "
                    f"and the last frame corresponds to timestamp {last_ts} seconds. "
                )
                timestamp_instruction = (
                    f" IMPORTANT: {frame_mapping}"
                    f"All timestamps in your response MUST be between {first_ts} and {last_ts}"
                    f" seconds. Do NOT use timestamps starting from 0. The video segment starts"
                    f" at {first_ts} seconds in the original video."
                )
            else:
                timestamp_instruction = "Make sure the answer contains correct timestamps."

            PROMPT = (
                "These are images sampled from a video "
                + time_format_str
                + " : "
                + string_of_times
                + ".\n"
                + query_text
                + "\n"
                + timestamp_instruction
            )

            logger.debug(f"PROMPT is  {PROMPT}")
            messages = [
                {
                    "role": "user",
                    "content": [
                        *video_list,
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ]
            if config.system_prompt:
                messages.insert(0, {"role": "system", "content": config.system_prompt})

            if self._nvSecretConfigured:
                from models.openai_compat.internal.util import get_nv_oauth_token

                new_key = get_nv_oauth_token(120)
                if self._key != new_key:
                    logger.info("NV key changed:")
                    self.init_gpt_4(new_key)
                else:
                    logger.info("No change in NV key")

            with TimeMeasure("OpenAI model inference"):
                logger.debug("Invoke call")
                try:
                    input_tokens = 0
                    output_tokens = 0
                    content = ""
                    reasoning_description = ""

                    extra_body = {}
                    if config.media_io_kwargs:
                        extra_body["media_io_kwargs"] = config.media_io_kwargs
                    elif is_remote_video_input and fps is not None and video_frames_times_:
                        extra_body["media_io_kwargs"] = {"video": {"num_frames": -1}}
                    if config.min_tokens is not None:
                        extra_body["min_tokens"] = config.min_tokens
                    if config.ignore_eos:
                        extra_body["ignore_eos"] = config.ignore_eos
                    if not extra_body:
                        extra_body = None

                    _nim_t0 = _time.time()
                    if self._model:
                        response_obj = self._model.invoke(
                            messages,
                            max_tokens=config.max_new_tokens,
                            temperature=config.temperature,
                            seed=config.seed,
                            top_p=config.top_p,
                            extra_body=extra_body,
                        )
                        content = response_obj.content
                        if hasattr(response_obj, "usage_metadata"):
                            input_tokens = getattr(response_obj.usage_metadata, "input_tokens", 0)
                            output_tokens = getattr(response_obj.usage_metadata, "output_tokens", 0)
                    elif self._client:
                        resp = self._client.chat.completions.create(
                            model=self._model_name,
                            messages=messages,
                            max_tokens=config.max_new_tokens,
                            temperature=config.temperature,
                            seed=config.seed,
                            top_p=config.top_p,
                            extra_body=extra_body,
                        )
                        content = ""
                        for choice in resp.choices:
                            content += str(choice.message.content)
                        if hasattr(resp, "usage") and resp.usage:
                            input_tokens = getattr(resp.usage, "prompt_tokens", 0)
                            output_tokens = getattr(resp.usage, "completion_tokens", 0)
                    else:
                        raise RuntimeError("Neither _model nor _client is configured")
                    nim_duration = _time.time() - _nim_t0
                    logger.debug(
                        "NIM inference took %.3fs for chunk %d (in=%d out=%d tokens)",
                        nim_duration,
                        tidx,
                        input_tokens,
                        output_tokens,
                    )

                    logger.debug("Invoke call done")
                    logger.debug(f"content is {str(content)}")
                    if config.preserve_reasoning_tags:
                        logger.debug("Preserving OpenAI reasoning tags in model output")
                    else:
                        content, reasoning_description = strip_thinking_tags(content)
                        logger.debug("OpenAI reasoning description: %s", reasoning_description)
                        logger.debug("OpenAI cleaned text output: %s", content)
                    generated_text = content
                except Exception as ex:
                    # Map OpenAI HTTP errors to ServiceException so FastAPI returns
                    # the correct status code instead of a generic 500.
                    try:
                        from openai import APIConnectionError as _APIConnErr
                        from openai import APIStatusError as _APIStatusErr

                        if isinstance(ex, _APIStatusErr):
                            raise ServiceException(
                                str(ex),
                                type(ex).__name__,
                                ex.status_code,
                            ) from None
                        if isinstance(ex, _APIConnErr):
                            raise ServiceException(
                                f"OpenAI API connection error: {ex}",
                                "APIConnectionError",
                                503,
                            ) from None
                    except ImportError:
                        pass

                    import traceback

                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    logger.error(
                        "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                    )
                    raise ex from None

                model_outputs.append(
                    VlmModelOutput(
                        output=generated_text,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        reasoning_description=reasoning_description,
                    )
                )
        return model_outputs

    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: List[List[float]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> concurrent.futures.Future[List[VlmModelOutput]]:
        """Generate a response for prompt using the video frames.

        This method submits the generation task to a thread pool and returns a Future
        that will resolve to the list of VlmModelOutput objects.

        Args:
            query: Prompt for the VLM model
            chunks: List of chunk information
            video_frames: List of video frame tensors
            video_frames_times: List of video frame times
            generation_config: VLM generation config. Defaults to None.
            **kwargs: Additional keyword arguments for future extensibility and API compatibility
                     across different model implementations. Currently unused but preserved for
                     maintaining consistent interface across all model classes.

        Returns:
            A Future that resolves to a list of VlmModelOutput objects (one per chunk)
        """
        return self._output_tpool.submit(
            self._generate_sync,
            query,
            chunks,
            video_frames,
            video_frames_times,
            generation_config,
            **kwargs,
        )

    def generate_text_only(
        self,
        messages: list[dict],
        generation_config: Optional[VlmGenerationConfig] = None,
    ) -> concurrent.futures.Future[List[VlmModelOutput]]:
        """Text-only generation using OpenAI/Azure client (no multimodal data)."""
        return self._output_tpool.submit(self._generate_text_only_sync, messages, generation_config)

    def _generate_text_only_sync(
        self,
        messages: list[dict],
        generation_config: Optional[VlmGenerationConfig] = None,
    ) -> List[VlmModelOutput]:
        config = generation_config or VlmGenerationConfig()
        input_tokens = 0
        output_tokens = 0

        kwargs = {
            "max_tokens": config.max_new_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
        }
        if config.seed:
            kwargs["seed"] = config.seed
        extra_body = {}
        if config.min_tokens is not None:
            extra_body["min_tokens"] = config.min_tokens
        if config.ignore_eos:
            extra_body["ignore_eos"] = config.ignore_eos
        if extra_body:
            kwargs["extra_body"] = extra_body

        if self._model:
            response_obj = self._model.invoke(messages, **kwargs)
            content = response_obj.content
            if hasattr(response_obj, "usage_metadata") and response_obj.usage_metadata:
                input_tokens = getattr(response_obj.usage_metadata, "input_tokens", 0)
                output_tokens = getattr(response_obj.usage_metadata, "output_tokens", 0)
        elif self._client:
            resp = self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                **kwargs,
            )
            content = resp.choices[0].message.content
            if hasattr(resp, "usage") and resp.usage:
                input_tokens = getattr(resp.usage, "prompt_tokens", 0)
                output_tokens = getattr(resp.usage, "completion_tokens", 0)
        else:
            raise RuntimeError("Neither _model nor _client is configured")

        reasoning_description = ""
        if not config.preserve_reasoning_tags:
            content, reasoning_description = strip_thinking_tags(content)

        return [
            VlmModelOutput(
                output=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_description=reasoning_description,
            )
        ]

    async def generate_text_only_stream(
        self,
        messages: list[dict],
        generation_config: Optional[VlmGenerationConfig] = None,
    ):
        """Async generator yielding text deltas for token-level streaming."""
        config = generation_config or VlmGenerationConfig()

        kwargs = {
            "max_tokens": config.max_new_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "stream": True,
        }
        if config.seed:
            kwargs["seed"] = config.seed
        extra_body = {}
        if config.min_tokens is not None:
            extra_body["min_tokens"] = config.min_tokens
        if config.ignore_eos:
            extra_body["ignore_eos"] = config.ignore_eos
        if extra_body:
            kwargs["extra_body"] = extra_body

        if self._client:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                **kwargs,
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        elif self._model:
            # LangChain streaming
            for chunk in self._model.stream(
                messages, **{k: v for k, v in kwargs.items() if k != "stream"}
            ):
                if hasattr(chunk, "content") and chunk.content:
                    yield chunk.content
        else:
            raise RuntimeError("Neither _model nor _client is configured")

    def can_enqueue_requests(self) -> bool:
        """Check if the model can accept new requests.

        Returns:
            True since OpenAI models can always accept new requests
        """
        return True

    def _shutdown_model(self):
        """Shutdown the model and clean up resources."""
        logger.info("Shutting down CompOpenAIModel...")

        # Shutdown thread pool executor
        if self._output_tpool is not None:
            logger.debug("Shutting down thread pool executor")
            self._output_tpool.shutdown(wait=True)
            self._output_tpool = None

        # Clean up client and model references
        if self._client is not None:
            logger.debug("Cleaning up OpenAI client")
            # OpenAI client doesn't have an explicit close method, just delete reference
            del self._client
            self._client = None

        if self._model is not None:
            logger.debug("Cleaning up AzureChatOpenAI model")
            del self._model
            self._model = None

        logger.info("CompOpenAIModel shutdown complete")


if __name__ == "__main__":
    # To test and debug, please use harness:
    # PYTHONPATH=src pytest tests/model/gpt4/test_gpt4v_jpeg_tensor_gen.py -s
    # Please add new test case for each bug
    pass
