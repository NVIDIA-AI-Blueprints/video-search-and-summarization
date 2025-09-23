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
import threading
import uuid
from typing import List

import numpy
import torch
import torchvision.transforms.functional as TF
from filelock import FileLock
from packaging.version import parse as parse_version
from PIL import Image, ImageDraw, ImageFont

from via_logger import TimeMeasure, logger

# Common parameters
FACTOR = 28
MAX_PIXELS = 16384 * 2 * FACTOR * FACTOR
MIN_PIXELS = 4 * 2 * FACTOR * FACTOR

DEFAULT_SYSTEM_PROMPT_CR1 = (
    "Please provide captions of all the events in the video with timestamps using the following format:"
    " <start time> <end time> caption of event 1.\n<start time> <end time> caption of event 2.\n"
    "At each frame, the timestamp is embedded at the bottom of the video. You need to extract"
    " the timestamp and answer the user question."
)


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


class CosmosReason1:
    def __init__(self, model_path, max_batch_size=None, use_trt=False, **kwargs) -> None:
        self._model = None
        self._max_batch_size = max_batch_size or 1
        self._inflight_req_ids = []
        self._use_trt = use_trt
        self.model_path = model_path
        self.model_dir_name = os.path.basename(os.path.normpath(model_path))

        # Set resize parameters
        self._max_pixels = MAX_PIXELS
        self._min_pixels = MIN_PIXELS

        self._use_trt = os.environ.get("COSMOS_REASON1_USE_TRT", "false").lower() == "true"

        self._system_prompt = DEFAULT_SYSTEM_PROMPT_CR1
        logger.info("Cosmos Reason1 default system prompt: %s", self._system_prompt)

        if self._use_trt:
            from transformers import AutoProcessor

            logger.info("Using TRT model for cosmos-reason1")
            with TimeMeasure("Cosmos Reason1 TRT model load"):
                # Load the TRT model
                import tensorrt_llm

                if parse_version(
                    parse_version(tensorrt_llm.__version__).base_version
                ) >= parse_version("1.0.0"):
                    from tensorrt_llm import LLM
                else:
                    from tensorrt_llm._torch.llm import LLM

                gpu_memory_utilization = os.environ.get("TRT_LLM_MEM_USAGE_FRACTION", "0.4")
                if not gpu_memory_utilization.strip():
                    gpu_memory_utilization = "0.4"
                gpu_memory_utilization = float(gpu_memory_utilization)

                logger.debug(
                    "TRT GPU memory utilization requirement set to: %s%%",
                    gpu_memory_utilization * 100,
                )
                with FileLock(model_path + "/.lock"):
                    logger.info("Initializing Cosmos-Reason1-7B from: %s", model_path)
                    self._llm = LLM(
                        model=model_path,
                        kv_cache_config={
                            "free_gpu_memory_fraction": gpu_memory_utilization,
                            "enable_block_reuse": False,
                        },
                    )

            logger.debug("Model loaded successfully")
            logger.debug("Initializing thread pool for TRT")
            logger.info("Max batch size: %s", self._max_batch_size)
            self._output_tpool = concurrent.futures.ThreadPoolExecutor(max_workers=max_batch_size)
            self._model_name = "cosmos-reason1"
            with open(os.path.join(model_path, "config.json"), "r") as f:
                self._model_config = json.load(f)
                self._num_time_tokens = self._model_config.get("num_time_tokens", 0)

            self._processor = AutoProcessor.from_pretrained(model_path)
            logger.info("Cosmos Reason1 TRT model initialized successfully")

        else:
            logger.info("Using VLLM model for cosmos-reason1")
            os.environ["VLLM_CACHE_ROOT"] = os.path.join(model_path, ".vllm")

            from transformers import AutoProcessor
            from vllm.engine.async_llm_engine import AsyncEngineArgs, AsyncLLMEngine

            self._num_time_tokens = 0
            self._model_name = "cosmos-reason1"
            model_lock_path = model_path + "/.lock"
            with FileLock(model_lock_path):
                logger.info("Initializing Cosmos-Reason1-7B from: %s", model_path)
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
                        limit_mm_per_prompt={"image": 0, "video": 1},
                        gpu_memory_utilization=gpu_memory_utilization,
                        max_num_seqs=self._max_batch_size,
                    )
                    self._llm = AsyncLLMEngine.from_engine_args(engine_args)
                    self._processor = AutoProcessor.from_pretrained(model_path)
                except Exception as e:
                    logger.error("Error initializing VLLM model: %s", e)
                    raise

            self._event_loop = asyncio.new_event_loop()
            logger.debug("Event loop created")
            t = threading.Thread(target=start_loop, args=(self._event_loop,))
            logger.debug("Starting event loop thread")
            t.start()
            logger.debug("Event loop thread started")

            # Initialize thread pool for VLLM`
            logger.info("Max batch size: %s", self._max_batch_size)
            logger.debug("Initializing thread pool for VLLM")
            self._output_tpool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_batch_size
            )
            logger.info("Cosmos Reason1 VLLM model initialized successfully")

    @property
    def model_name(self):
        return self._model_name

    def get_conv(self):
        # Initialize _conv if not already done
        if not hasattr(self, "_conv"):
            self._conv = []
        return self._conv.copy()

    def _postprocess_trt(self, output, video_frames_times):
        logger.debug("Postprocessing TRT output")
        with TimeMeasure("TRT generate"):
            output.result()
            self._inflight_req_ids.remove(output)
        # Extract and validate response
        if not output or not output.outputs[0]:
            logger.warning("No output generated from model")
            return ["Error: No response generated"], [{"input_tokens": 0, "output_tokens": 0}]
        result = output.outputs[0].text
        logger.debug("TRT raw text output: %s", result)
        if not result:
            logger.warning("Empty response from model")
            return [""], [{"input_tokens": 0, "output_tokens": 0}]

        # Step 1: Strip leading/trailing whitespace
        result = result.strip()
        # Step 2: Extract reasoning description
        reasoning_description = re.search(r"<think>(.*?)</think>", result, flags=re.DOTALL)
        if reasoning_description:
            reasoning_description = reasoning_description.group(1)
        else:
            reasoning_description = ""
        logger.debug("TRT-LLM reasoning description: %s", reasoning_description)
        # Remove the entire <think>...</think> block (including tags and content)
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL)
        # Step 3: Remove <answer>, </answer>, <summary>, and </summary> tags, but keep their content
        for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
            result = result.replace(tag, "")
        # Step 4: Final cleanup (strip whitespace)
        result = result.strip()
        logger.debug("TRT cleaned text output: %s", result)

        return [result], [
            {
                "input_tokens": len(output.prompt_token_ids),
                "output_tokens": output.outputs[0].length,
                "reasoning_description": reasoning_description,
            }
        ]

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
            # Step 2: Extract reasoning description
            reasoning_description = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL)
            if reasoning_description:
                reasoning_description = reasoning_description.group(1)
            else:
                reasoning_description = ""
            logger.debug("VLLM reasoning description: %s", reasoning_description)
            # Step 3: Remove the entire <think>...</think> block (including tags and content)
            response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
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

        if self._use_trt:
            # TRT warmup - create dummy tensors and run a simple generation
            dummy_images = torch.stack(
                [torch.ones(100, 100, 3, dtype=torch.uint8).cuda() for _ in range(8)]
            )
            ret = self.generate("Say Hi", dummy_images, video_frames_times=list(range(8)))
            if isinstance(ret, concurrent.futures.Future):
                result = ret.result()
                logger.info("TRT warmup completed successfully with result: %s", result)
                return result
        else:
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
        max_pixels = MAX_PIXELS / n

        # Determine scaling factor based on pixel bounds
        scale = None
        if orig_pixels < min_pixels:
            scale = math.sqrt(min_pixels / orig_pixels)
        elif orig_pixels > max_pixels:
            scale = math.sqrt(max_pixels / orig_pixels)
        logger.debug(
            "smart_resize_tensor: scale: %s, orig_pixels: %d, min_pixels: %f, max_pixels: %f",
            scale,
            orig_pixels,
            min_pixels,
            max_pixels,
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

        if "temperature" not in generation_config:
            generation_config["temperature"] = 0.7

        if generation_config["temperature"] == 0:
            generation_config.pop("temperature")

        if "max_new_tokens" not in generation_config:
            generation_config["max_new_tokens"] = 2048

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
        if generation_config.get("enable_reasoning") and "<think>" not in system_prompt:
            system_prompt += (
                " Answer the question in the following format: "
                "<think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>.\n"
            )

        images = [Image.fromarray(image.cpu().numpy()) for image in images]
        images = self.overlay_frame_number(images, video_frames_times)

        # convert PIL Images to tensors
        images = torch.stack([TF.pil_to_tensor(image) for image in images])

        images = self.smart_resize_tensor(images)
        if self._use_trt:
            from tensorrt_llm import SamplingParams

            # Smart resize images to appropriate dimensions
            # Convert to format expected by TRT (C, H, W) and normalize
            images = [image.half().div(255) for image in images]

            messages = [
                {
                    "role": "system",
                    "content": (system_prompt),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "video", "video": "sample.mp4"},
                    ],
                },
            ]
            prompt = self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            input = {
                "prompt": prompt,
                "multi_modal_data": {
                    "video": [images],
                },
            }

            logger.debug("Prompt to TRT model: %s", input["prompt"])
            # TRT mode
            # Remove enable_reasoning from generation_config as it's not a valid SamplingParams argument
            generation_config_copy = generation_config.copy()
            generation_config_copy.pop("enable_reasoning", None)

            sampling_params = SamplingParams(
                max_tokens=generation_config_copy.pop("max_new_tokens"),
                **generation_config_copy,
                seed=seed,
            )
            output = self._llm.generate_async(
                inputs=input,
                sampling_params=sampling_params,
            )

            self._inflight_req_ids.append(output)

            return self._output_tpool.submit(
                self._postprocess_trt,
                output,
                video_frames_times,
                chunk,
            )
        else:
            # VLLM model generation

            input = [images]  # Wrap in list as expected by the model

            messages = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "video", "video": "sample.mp4"},
                    ],
                },
            ]
            prompt = self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            logger.debug("Prompt to VLLM model: %s", prompt)
            vision_kwargs = {}

            # Prepare multimodal data
            mm_data = {"video": input}

            # Prepare LLM inputs
            mm_processor_kwargs = {
                **vision_kwargs,
                "chain_of_thought": True,
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

    def overlay_frame_number(
        self,
        images: List[Image.Image],
        video_frames_times: List[float],
        border_height: int = 28,  # this is due to patch size of 28
        temporal_path_size: int = 2,  # Number of positions to cycle through
        font_size: int = 20,
        font_color: str = "white",
    ) -> List[Image.Image]:
        """
        Overlay text on a list of PIL images with black border.
        The timestamp position cycles through available positions.

        Args:
            images: List of PIL images to process
            fps: Frames per second
            border_height: Height of the black border in pixels (default: 28)
            temporal_path_size: Number of positions to cycle through (default: 2)
            font_size: Font size for the text (default: 20)
            font_color: Color of the text (default: "white")

        Returns:
            List of PIL images with text overlay
            List of timestamps
        """

        # Try to use DejaVu Sans Mono font for better readability
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)

        # Process each image
        processed_images = []

        for i, image in enumerate(images):
            # Get original dimensions
            width, height = image.size

            # Create new image with black border at the bottom
            new_height = height + border_height
            new_image = Image.new("RGB", (width, new_height), color="black")

            # Paste original image at the top
            new_image.paste(image, (0, 0))

            # Draw text on the black border
            draw = ImageDraw.Draw(new_image)

            # Calculate timestamp for current frame
            text = f"{float(video_frames_times[i])-float(video_frames_times[0]):.2f}s"

            # Get text dimensions
            try:
                # Get text bounding box
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                # Fallback for older PIL versions
                text_width, text_height = draw.textsize(text, font=font)

            # Define available positions (cycling through horizontal positions)
            position_idx = i % temporal_path_size
            section_width = width // temporal_path_size

            # Calculate x position based on cycling position
            section_center_x = position_idx * section_width + section_width // 2
            text_x = section_center_x - text_width // 2

            # Ensure text doesn't go outside bounds
            text_x = max(0, min(text_x, width - text_width))

            # Center vertically in the border
            text_y = height + (border_height - text_height) // 2

            # Draw the single timestamp
            draw.text((text_x, text_y), text, fill=font_color, font=font)

            processed_images.append(new_image)

        return processed_images

    @staticmethod
    def get_model_info():
        return "cosmos-reason1", "internal", "NVIDIA"
