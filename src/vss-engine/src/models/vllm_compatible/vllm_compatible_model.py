######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import asyncio
import concurrent.futures
import json
import math
import os
import random
import re
import sys
import threading
import uuid
from typing import List

import numpy
import torch
import torchvision.transforms.functional as TF
from filelock import FileLock
from PIL import Image, ImageDraw, ImageFont

from via_logger import TimeMeasure, logger

# Common parameters
FACTOR = 28
CR1_MAX_PIXELS = 16384 * 2 * FACTOR * FACTOR
CR2_MAX_PIXELS = CR1_MAX_PIXELS
MAX_PIXELS = CR1_MAX_PIXELS
MIN_PIXELS = 4 * 2 * FACTOR * FACTOR
MAX_CR2_FRAMES = 128

DEFAULT_SYSTEM_PROMPT_CR1 = (
    "Please provide captions of all the events in the video with timestamps using the following format:"
    " <start time> <end time> caption of event 1.\n<start time> <end time> caption of event 2.\n"
    "At each frame, the timestamp is embedded at the bottom of the video. You need to extract"
    " the timestamp and answer the user question."
)


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


class VllmCompatibleModel:
    def __init__(self, model_path, max_batch_size=None, vlm_model_type=None, **kwargs) -> None:
        self._model = None
        self._max_batch_size = max_batch_size or 1
        self._vlm_model_type = vlm_model_type
        self._inflight_req_ids = []
        self.model_path = model_path
        self.model_dir_name = os.path.basename(os.path.normpath(model_path))

        try:
            with open(os.path.join(model_path, "config.json")) as f:
                model_config = json.load(f)
            model_architecture = model_config["architectures"][0]
            if model_architecture == "Qwen2_5_VLForConditionalGeneration" and os.path.exists(
                "/opt/nvidia/vllm-0.12.0"
            ):
                sys.path.insert(0, "/opt/nvidia/vllm-0.12.0")
                logger.debug("Using vllm from /opt/nvidia/vllm-0.12.0")
        except Exception:
            logger.debug("Failed to get model architecture from config.json")

        # Set resize parameters
        self._max_pixels = MAX_PIXELS
        self._min_pixels = MIN_PIXELS

        if self._vlm_model_type == "cosmos-reason1":
            self._system_prompt = DEFAULT_SYSTEM_PROMPT_CR1
            self._max_pixels = CR1_MAX_PIXELS
            logger.debug("VLM default system prompt: %s", self._system_prompt)
        elif self._vlm_model_type == "cosmos-reason2":
            self._max_pixels = CR2_MAX_PIXELS
            self._system_prompt = None
        else:
            self._system_prompt = None

        os.environ["VLLM_CACHE_ROOT"] = os.path.join(model_path, ".vllm")

        from transformers import AutoProcessor
        from vllm.engine.async_llm_engine import AsyncLLMEngine

        try:
            from vllm.engine.async_llm_engine import AsyncEngineArgs
        except ImportError:
            from vllm.engine.arg_utils import AsyncEngineArgs

        self._num_time_tokens = 0
        model_lock_path = model_path + "/.lock"
        with FileLock(model_lock_path):
            logger.info("Initializing Model from: %s", model_path)
            gpu_memory_utilization = os.environ.get("VLLM_GPU_MEMORY_UTILIZATION", "0.4")
            if not gpu_memory_utilization.strip():
                gpu_memory_utilization = "0.4"
            gpu_memory_utilization = float(gpu_memory_utilization)

            logger.debug(
                "VLLM GPU memory utilization requirement set to: %s%%",
                gpu_memory_utilization * 100,
            )
            try:
                engine_args = AsyncEngineArgs(
                    model=model_path,
                    max_model_len=int(os.environ.get("VLM_MAX_MODEL_LEN", "") or 20480),
                    limit_mm_per_prompt={
                        "image": MAX_CR2_FRAMES if self._vlm_model_type == "cosmos-reason2" else 1,
                        "video": 1,
                    },
                    gpu_memory_utilization=gpu_memory_utilization,
                    max_num_seqs=self._max_batch_size,
                    tensor_parallel_size=torch.cuda.device_count(),
                )
                self._llm = AsyncLLMEngine.from_engine_args(engine_args)
                self._processor = AutoProcessor.from_pretrained(model_path)
            except Exception as e:
                logger.error("Error initializing VLLM model: %s", e)
                raise

        self._event_loop = asyncio.new_event_loop()
        logger.debug("Event loop created")
        self._event_loop_thread = threading.Thread(target=start_loop, args=(self._event_loop,))
        logger.debug("Starting event loop thread")
        self._event_loop_thread.start()
        logger.debug("Event loop thread started")

        # Initialize thread pool for VLLM`
        logger.info("Max batch size: %s", self._max_batch_size)
        logger.debug("Initializing thread pool for VLLM")
        self._output_tpool = concurrent.futures.ThreadPoolExecutor(max_workers=self._max_batch_size)
        logger.info("VLM model initialized successfully")

    @property
    def model_name(self):
        return self._vlm_model_type

    def _postprocess_vllm(self, output, video_frames_times, chunk=None):
        logger.debug("Postprocessing VLLM output")
        with TimeMeasure("VLLM postprocess"):
            original_output = output
            if hasattr(output, "result"):
                output = output.result()
                if original_output in self._inflight_req_ids:
                    self._inflight_req_ids.remove(original_output)
            elif isinstance(output, concurrent.futures.Future):
                output = output.result()
                if original_output in self._inflight_req_ids:
                    self._inflight_req_ids.remove(original_output)

            # Extract and validate response
            if not output or not output[0].outputs:
                logger.warning("No output generated from model")
                return ["Error: No response generated"], [{"input_tokens": 0, "output_tokens": 0}]

            generated_text = output[0].outputs[0].text
            logger.debug("VLLM raw text output: %s", generated_text)
            if not generated_text:
                logger.warning("Empty response from model")
                return [""], [{"input_tokens": 0, "output_tokens": 0}]

            # Step 1: Strip leading/trailing whitespace
            response = generated_text.strip()
            # Step 2: Extract reasoning description and remove it from response
            reasoning_description = ""
            if "<think>" in response and "</think>" in response:
                # Case 1: Both tags present in response
                reasoning_match = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL)
                if reasoning_match:
                    reasoning_description = reasoning_match.group(1).strip()
                # Remove the entire <think>...</think> block
                response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
            elif "</think>" in response:
                # Case 2: Only closing tag present (opening tag was in prompt)
                think_end = response.find("</think>")
                reasoning_description = response[:think_end].strip()
                # Remove everything up to and including </think>
                response = response[think_end + len("</think>") :]
            elif "<think>" in response:
                # Case 3: Only opening tag present (incomplete generation)
                think_start = response.find("<think>")
                reasoning_description = response[think_start + len("<think>") :].strip()
                # Remove everything from <think> onwards
                logger.warning("Incomplete reasoning response generated. Try increasing max tokens")
                response = response[:think_start].strip()
            logger.debug("VLLM reasoning description: %s", reasoning_description)
            # Step 3: Remove <answer>, </answer>, <summary>, and </summary> tags, but keep their content
            for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
                response = response.replace(tag, "")
            # Step 4: Final cleanup (strip whitespace)
            response = response.strip()
            logger.debug("VLLM cleaned text output: %s", response)

            try:
                input_tokens = (
                    len(output[0].prompt_token_ids) if hasattr(output[0], "prompt_token_ids") else 0
                )
                output_tokens = (
                    len(output[0].outputs[0].token_ids)
                    if hasattr(output[0].outputs[0], "token_ids")
                    else 0
                )
            except (AttributeError, IndexError):
                input_tokens = 0
                output_tokens = 0

            try:
                if chunk:
                    response = self._update_video_frames_times(response, chunk, video_frames_times)
            except Exception as e:
                logger.error("Error updating video frames times: %s", e)

            return [response], [
                {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_description": reasoning_description,
                }
            ]

    def _update_video_frames_times(self, response, chunk, video_frames_times):
        response = re.sub(
            r"<([0-9]+(?:\.[0-9]+)?)>",
            lambda m: "<"
            + chunk.get_timestamp(float(video_frames_times[0]) + float(m.group(1)))
            + ">",
            response,
        )
        return response

    def process_async_vllm(
        self, llm_inputs, vllm_sampling_params, video_frames_times, request_id, chunk=None
    ):

        async def generate_async():
            async for output_item in self._llm.generate(
                llm_inputs, sampling_params=vllm_sampling_params, request_id=request_id
            ):
                final_output = output_item
            if not final_output:
                logger.warning("Async for retuned no output")
                return ["Error: No response generated"], [{"input_tokens": 0, "output_tokens": 0}]
            return final_output

        with TimeMeasure("VLLM generate"):
            output = asyncio.run_coroutine_threadsafe(generate_async(), self._event_loop).result()
        if request_id in self._inflight_req_ids:
            logger.debug("Removing request_id from inflight_req_ids: %s", request_id)
            self._inflight_req_ids.remove(request_id)
        logger.debug("Postprocessing VLLM output")
        return self._postprocess_vllm([output], video_frames_times, chunk)

    def can_enqueue_requests(self):
        """Check if the model can accept new requests."""
        return len(self._inflight_req_ids) < self._max_batch_size

    def warmup(self):
        """Warm up the model with dummy tensors to initialize CUDA kernels and memory."""
        logger.info("Starting model warmup...")

        # VLLM warmup - create dummy tensors and follow the complete VLLM flow
        dummy_images = torch.stack(
            [torch.ones(100, 100, 3, dtype=torch.uint8).cuda() for _ in range(8)]
        )
        warmup_prompt = "Describe this video briefly."
        warmup_config = {
            "temperature": 0.7,
            "max_new_tokens": 50,  # Short for warmup
            "top_p": 0.9,
            "top_k": 100,
            "repetition_penalty": 1.1,
            "seed": 42,
        }
        ret = self.generate(warmup_prompt, dummy_images, warmup_config, list(range(8)))
        if ret.exception():
            logger.error("Error during VLLM warmup: %s", ret.exception())
            return
        ret = ret.result()
        return ret

    @property
    def num_time_tokens(self):
        return self._num_time_tokens

    def smart_resize_tensor(self, images: torch.Tensor) -> torch.Tensor:
        """
        Resize a tensor image so that:
        - Its total pixels are between min_pixels and max_pixels.
        - Height and width are divisible by 'factor'.
        - Aspect ratio is preserved.
        """
        # Assuming image is in (H, W, C) format
        n, c, h, w = images.shape
        logger.debug("smart_resize_tensor: n: %d, h: %d, w: %d, c: %d", n, h, w, c)
        orig_pixels = h * w
        n = n + n % 2

        min_pixels = MIN_PIXELS / n
        max_pixels = self._max_pixels / n

        # Determine scaling factor based on pixel bounds
        scale = None
        if orig_pixels < min_pixels:
            scale = math.sqrt(min_pixels / orig_pixels)
        elif max_pixels and orig_pixels > max_pixels:
            scale = math.sqrt(max_pixels / orig_pixels)
        logger.debug(
            "smart_resize_tensor: scale: %s, orig_pixels: %d, min_pixels: %f, max_pixels: %f",
            scale,
            orig_pixels,
            min_pixels,
            max_pixels or 0,
        )

        if scale is not None:
            new_h = int(round(h * scale))
            new_w = int(round(w * scale))

            new_w = new_w // FACTOR * FACTOR
            new_h = new_h // FACTOR * FACTOR

            images = TF.resize(
                images,
                [new_h, new_w],
                interpolation=TF.InterpolationMode.BICUBIC,
                antialias=True,
            )

        logger.debug("smart_resize_tensor: resized tensor shape: %s", images.shape)

        return images

    def generate(self, prompt, images, generation_config=None, video_frames_times=None, chunk=None):
        """Generate a response for prompt using the video embeddings

        Args:
            prompt: Conversation prompt
            video_embeds: Batch of video embeddings
            video_frames_times: Batch of video frame times used for embeddings for each chunk
            generation_config: VLM generation config. Defaults to None.

        Returns:
            List of responses for the batch of chunks
        """

        # Populate default values for the VLM generation parameters
        if not generation_config:
            generation_config = {}

        enable_reasoning = generation_config.get("enable_reasoning", False)

        if "temperature" not in generation_config:
            generation_config["temperature"] = 0.7

        if generation_config["temperature"] == 0:
            generation_config.pop("temperature")

        if "max_new_tokens" not in generation_config:
            generation_config["max_new_tokens"] = 20480 if not enable_reasoning else 2048

        if "top_p" not in generation_config:
            generation_config["top_p"] = 0.9

        if "top_k" not in generation_config:
            generation_config["top_k"] = 100
        generation_config["top_k"] = int(generation_config["top_k"])

        if "seed" in generation_config:
            seed = generation_config["seed"]
            generation_config.pop("seed")
        else:
            seed = 1
        if "repetition_penalty" not in generation_config:
            generation_config["repetition_penalty"] = 1.1
        if "no_repeat_ngram_size" not in generation_config:
            generation_config["no_repeat_ngram_size"] = 3

        # Set the seed
        random.seed(seed)
        numpy.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

        system_prompt = self._system_prompt

        if "system_prompt" in generation_config and generation_config["system_prompt"]:
            system_prompt = generation_config["system_prompt"]

        generation_config.pop("system_prompt", None)

        # Override system prompt in environment variable with reasoning prompt if enable_reasoning is True
        if (
            self._vlm_model_type in ["cosmos-reason1"]
            and enable_reasoning
            and "<think>" not in system_prompt
        ):
            system_prompt += (
                " Answer the question in the following format: "
                "<think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>.\n"
            )

        if self._vlm_model_type == "cosmos-reason1":
            # Overlay frame numbers directly on GPU tensors (no CPU conversion needed)
            images = self.overlay_frame_number_cr1(images, video_frames_times)
            # For cosmos-reason1 we need to do smart resize of the images
            images = self.smart_resize_tensor(images)
            images = images.half()
        else:
            # convert nhwc to nchw
            images = images.permute(0, 3, 1, 2)

        model_architecture = self._llm.model_config.architecture
        logger.debug("Model architecture %s", model_architecture)
        add_timestamp_to_prompt = self._vlm_model_type != "cosmos-reason1"

        if add_timestamp_to_prompt and chunk and video_frames_times:
            string_of_times = ""
            for t, frame_time in enumerate(video_frames_times):
                string_of_times += f"{chunk.get_timestamp(frame_time)}"
                string_of_times += " "
            prompt = (
                "These are the images sampled from a video at  timestamps "
                + string_of_times
                + ". "
                + prompt
            )

        if (
            self._vlm_model_type in ["cosmos-reason2"]
            and enable_reasoning
            and "<think>" not in prompt
        ):
            prompt += (
                "Answer the question using the following format:\n\n"
                "<think>\n"
                "Your reasoning.\n"
                "</think>\n\n"
                "Write your final answer immediately after the </think> tag.\n"
            )
        # Calculate fps here
        # Handle edge case: if only one frame or all timestamps are the same (multi-image case),
        # default to 1.0 fps to avoid division by zero
        if video_frames_times and len(video_frames_times) > 1:
            time_diff = video_frames_times[-1] - video_frames_times[0]
            if time_diff > 0:
                fps = (len(video_frames_times) - 1) / time_diff
            else:
                # Multi-image case where all timestamps are the same
                fps = 1.0
        else:
            fps = 1.0

        # Determine if this is a single image or video based on number of frames
        # Qwen3VL video processor requires at least 2 frames (temporal_factor=2)
        is_single_image = len(images) == 1

        video_metadata = {}

        if (
            model_architecture == "Qwen3VLMoeForConditionalGeneration"
            or model_architecture == "Qwen3VLForConditionalGeneration"
        ):
            if not is_single_image:
                # Only create video metadata for multi-frame videos
                video_metadata = {
                    "total_num_frames": len(images),
                    "frames_indices": [int(t * fps) for t in video_frames_times],
                    "fps": fps,
                    "duration": (video_frames_times[-1] - video_frames_times[0]),
                }

        if is_single_image:
            input = [images.cpu()]
        else:
            input = (
                images.cpu(),
                video_metadata,
            )

        messages = []
        logger.debug("System prompt %s user prompt %s", system_prompt, prompt)
        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        if is_single_image:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "image": "sample.jpg"},
                    ],
                },
            )
        else:
            if self._vlm_model_type == "cosmos-reason2":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "video", "video": "sample.mp4"},
                            {"type": "text", "text": prompt},
                        ],
                    },
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "video", "video": "sample.mp4"},
                        ],
                    },
                )

        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        logger.debug("Prompt to VLLM model: %s", prompt)
        vision_kwargs = {}

        # Prepare multimodal data
        # For single images, use 'image' type; for multiple frames, use 'video' type
        if is_single_image:
            mm_data = {"image": input}
        else:
            mm_data = {"video": input}

        # Prepare LLM inputs
        mm_processor_kwargs = {
            **vision_kwargs,
            "chain_of_thought": True,
            "do_sample_frames": False,
        }
        llm_inputs = {
            "prompt": prompt,
            "multi_modal_data": mm_data,
            "mm_processor_kwargs": mm_processor_kwargs,
        }
        # Generate response using generation_config parameters
        from vllm import SamplingParams

        vllm_sampling_params = SamplingParams(
            top_p=generation_config["top_p"],
            max_tokens=generation_config["max_new_tokens"],
            repetition_penalty=generation_config["repetition_penalty"],
        )
        if "temperature" in generation_config:
            vllm_sampling_params.temperature = generation_config["temperature"]
        if "no_repeat_ngram_size" in generation_config and self._vlm_model_type == "cosmos-reason2":
            vllm_sampling_params.no_repeat_ngram_size = generation_config["no_repeat_ngram_size"]
        try:
            request_id = str(uuid.uuid4())
            self._inflight_req_ids.append(request_id)

            process_func = self.process_async_vllm
            arg_llm_inputs = llm_inputs
            arg_vllm_sampling_params = vllm_sampling_params
            arg_video_frames_times = video_frames_times
            arg_request_id = request_id
            logger.debug("Submitting VLLM request to thread pool: %s", arg_request_id)

            return self._output_tpool.submit(
                process_func,
                arg_llm_inputs,
                arg_vllm_sampling_params,
                arg_video_frames_times,
                arg_request_id,
                chunk,
            )
        except Exception as e:
            logger.error("Error during VLLM async generation: %s", e)
            return ["Error: Generation failed"], [{"input_tokens": 0, "output_tokens": 0}]

    def overlay_frame_number_cr1(
        self,
        images: torch.Tensor,
        video_frames_times: List[float],
        border_height: int = 28,  # this is due to patch size of 28
        temporal_path_size: int = 2,  # Number of positions to cycle through
        font_size: int = 20,
        font_color: str = "white",
    ) -> torch.Tensor:
        """
        Overlay text on a batch of image tensors with black border using GPU acceleration.
        The timestamp position cycles through available positions.

        Args:
            images: Tensor of images on GPU with shape (N, H, W, C) with values in [0, 255]
            video_frames_times: List of timestamps for each frame
            border_height: Height of the black border in pixels (default: 28)
            temporal_path_size: Number of positions to cycle through (default: 2)
            font_size: Font size for the text (default: 20)
            font_color: Color of the text (default: "white")

        Returns:
            Tensor of images with text overlay, shape (N, C, H+border_height, W) in [0, 255] range
        """
        if images.numel() == 0:
            return images

        # Get dimensions from tensor shape (N, H, W, C)
        num_images, height, width, channels = images.shape
        new_height = height + border_height

        # Try to use DejaVu Sans Mono font for better readability
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)

        batch_images = images.permute(0, 3, 1, 2).float()
        batch_with_borders = torch.zeros(
            (num_images, channels, new_height, width), dtype=batch_images.dtype, device="cuda"
        )

        # Paste original images at the top (vectorized operation on GPU)
        batch_with_borders[:, :, :height, :] = batch_images

        text_tensors = []
        for i in range(num_images):
            text_overlay = Image.new("RGBA", (width, border_height), color=(0, 0, 0, 0))
            draw = ImageDraw.Draw(text_overlay)

            text = f"{float(video_frames_times[i])-float(video_frames_times[0]):.2f}s"

            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                text_width, text_height = draw.textsize(text, font=font)

            # Calculate position (cycling through horizontal positions)
            position_idx = i % temporal_path_size
            section_width = width // temporal_path_size
            section_center_x = position_idx * section_width + section_width // 2
            text_x = section_center_x - text_width // 2
            text_x = max(0, min(text_x, width - text_width))
            text_y = (border_height - text_height) // 2

            # Draw text
            draw.text((text_x, text_y), text, fill=font_color, font=font)

            # Convert RGBA to RGB (composite on black background)
            text_rgb = Image.new("RGB", (width, border_height), color="black")
            text_rgb.paste(text_overlay, (0, 0), text_overlay)

            # Convert PIL image directly to tensor without normalization
            # PIL format: (H, W, C) with [0, 255] -> Tensor: (C, H, W) with [0, 255]
            text_array = numpy.array(text_rgb)
            text_tensor = torch.from_numpy(text_array).cuda().permute(2, 0, 1).float()
            text_tensors.append(text_tensor)

        batch_text = torch.stack(text_tensors).cuda()

        batch_with_borders[:, :, height:, :] = batch_text

        return batch_with_borders

    def shutdown(self):
        """Gracefully shutdown the model and event loop."""
        logger.info("Shutting down VllmCompatibleModel...")

        # Shutdown the AsyncLLMEngine
        async def shutdown_engine():
            self._llm.shutdown()

        asyncio.run_coroutine_threadsafe(shutdown_engine(), self._event_loop).result(timeout=5.0)

        # Stop the event loop gracefully
        logger.debug("Stopping event loop")
        self._event_loop.call_soon_threadsafe(self._event_loop.stop)
        self._event_loop_thread.join(timeout=5.0)

        # Close the event loop
        if not self._event_loop.is_closed():
            self._event_loop.close()

        logger.info("VllmCompatibleModel shutdown complete")

    @staticmethod
    def get_model_info(vlm_model_type, model_path):
        model_id = os.path.basename(os.path.normpath(model_path))
        if vlm_model_type == "cosmos-reason1":
            return "cosmos-reason1", "internal", "NVIDIA"
        elif vlm_model_type == "cosmos-reason2":
            return "cosmos-reason2", "internal", "NVIDIA"
        else:
            return model_id, "internal", "Custom"
