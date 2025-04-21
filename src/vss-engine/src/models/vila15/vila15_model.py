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

import concurrent.futures
import json
import os
import random
import subprocess
import sys

import numpy
import torch
from filelock import FileLock
from transformers import AutoConfig, AutoTokenizer, GenerationConfig

from via_logger import TimeMeasure, logger

sys.path.append(os.path.dirname(__file__) + "/VILA")

import llava.model.language_model.llava_llama  # noqa: E402, F401
from llava.conversation import conv_templates  # noqa: E402
from llava.mm_utils import get_model_name_from_path  # noqa: E402
from llava.utils import disable_torch_init  # noqa: E402


class Vila15:
    TRTLLM_EXECUTOR_INFLIGHT_BATCHING = True

    def __init__(
        self, model_path, use_trt=False, trt_engine_dir="", async_output=False, max_batch_size=None
    ) -> None:
        disable_torch_init()
        self._model = None
        self._model_config = AutoConfig.from_pretrained(model_path).llm_cfg
        self._generation_config = GenerationConfig.from_pretrained(model_path + "/llm")
        self._max_batch_size = max_batch_size
        self._inflight_req_ids = []
        self._next_extra_id = 1

        self._lora_config = None
        self._lora_weights = None
        self._lora_config_id = 10

        lora_model_path = os.environ.get("VILA_LORA_PATH", "")
        if lora_model_path:
            logger.info("LoRA model path is set: %s", lora_model_path)
            lock_file_path = os.path.join(lora_model_path, ".lock")
            lora_trt_weights_path = os.path.join(lora_model_path, "trt_weights")
            with FileLock(lock_file_path):
                if not os.path.isfile(
                    os.path.join(lora_trt_weights_path, "model.lora_config.npy")
                ) or not os.path.isfile(
                    os.path.join(lora_trt_weights_path, "model.lora_weights.npy")
                ):
                    logger.info("Converting LoRA weights ...")
                    result = subprocess.run(
                        [
                            "python3",
                            os.path.join(
                                os.path.dirname(__file__), "trt_helper/hf_lora_convert.py"
                            ),
                            "-i",
                            lora_model_path,
                            "-o",
                            lora_trt_weights_path,
                            "--storage-type",
                            "float16",
                        ]
                    )
                    if result.returncode:
                        logger.error("Failed to convert LoRA weights")
                        raise Exception("Failed to convert LoRA weights")

            self._lora_config = torch.from_numpy(
                numpy.load(os.path.join(lora_trt_weights_path, "model.lora_config.npy"))
            ).squeeze(0)
            self._lora_weights = torch.from_numpy(
                numpy.load(os.path.join(lora_trt_weights_path, "model.lora_weights.npy"))
            ).squeeze(0)
            logger.info(f"LoRA weights loaded from {lora_trt_weights_path}")

        with TimeMeasure("VILA TRT model load"):

            # Load the TRT model
            import tensorrt_llm.bindings.executor as trtllm

            if self._lora_weights is not None:
                self._trt_lora_config = trtllm.LoraConfig(
                    self._lora_config_id, self._lora_weights, self._lora_config
                )
            else:
                self._trt_lora_config = None

            with open(os.path.join(trt_engine_dir, "config.json")) as f:
                config = json.load(f)
                if config["build_config"]["plugin_config"]["lora_plugin"]:
                    peft_config = trtllm.PeftCacheConfig(
                        device_cache_percent=float(
                            os.environ.get("TRT_LLM_LORA_CACHE_DEVICE_MEM_USAGE_FRACTION", "")
                            or 0.1
                        ),
                        host_cache_size=int(
                            os.environ.get("TRT_LLM_LORA_CACHE_HOST_MEM_USAGE_BYTES", "")
                            or 10 * 1024 * 1024 * 1024
                        ),
                    )
                else:
                    peft_config = trtllm.PeftCacheConfig()

            executor_config = trtllm.ExecutorConfig(
                kv_cache_config=trtllm.KvCacheConfig(
                    free_gpu_memory_fraction=float(
                        os.environ.get("TRT_LLM_MEM_USAGE_FRACTION", "") or 0.4
                    )
                ),
                peft_cache_config=peft_config,
            )
            self._executor = trtllm.Executor(
                trt_engine_dir, trtllm.ModelType.DECODER_ONLY, executor_config
            )
        self._output_tpool = (
            concurrent.futures.ThreadPoolExecutor(
                max_workers=max_batch_size if self.TRTLLM_EXECUTOR_INFLIGHT_BATCHING else 2
            )
            if async_output
            else None
        )

        self._model_name = get_model_name_from_path(model_path)

        # Load the tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(model_path + "/llm", use_fast=True)

        # Conversation template from the model name
        model_name = get_model_name_from_path(model_path)

        # Create a copy of the conversation template
        self._conv = conv_templates["hermes-2"].copy()
        if "mpt" in model_name.lower():
            self._roles = ("user", "assistant")
        else:
            self._roles = self._conv.roles

    @property
    def model_name(self):
        return self._model_name

    @property
    def model_config(self):
        return self._model_config

    def get_conv(self):
        return self._conv.copy()

    def can_enqueue_requests(self):
        return (
            not self.TRTLLM_EXECUTOR_INFLIGHT_BATCHING
            or len(self._inflight_req_ids) < self._max_batch_size
        )

    def _postprocess(self, req_id, output_ids, input_token_length):
        if req_id:
            with TimeMeasure("TRT generate"):
                responses = self._executor.await_responses(req_id)
                self._inflight_req_ids.remove(req_id)
            output_ids = torch.tensor([responses[0].result.output_token_ids[0]])
            output_token_length = output_ids.shape[-1]

        # Decode the output_ids to get the output string
        outputs = self._tokenizer.batch_decode(output_ids, skip_special_tokens=True)
        outputs = [output.strip() for output in outputs]
        return outputs, [
            {"input_tokens": input_token_length, "output_tokens": output_token_length},
        ] * len(outputs)

    def _get_input_ids_from_prompt(self, prompt, image_embeds):
        """
        Tokenize the input prompt. Replace <image> tag with pointer to the
        image embeddings
        """
        # Split the input into chunks.
        prompt_chunks = prompt.split("<image>")
        input_ids = []
        extra_input_ids = []
        extra_input_id = self._next_extra_id
        self._next_extra_id = 1 + (self._next_extra_id % 10000)

        # Tokenize the prompt chunk by chunk
        for idx, prompt_chunk in enumerate(prompt_chunks):
            if idx > 0:
                # Replace <image> tag with pointer to the image embedding
                image_embed_input_ids = torch.arange(
                    self.model_config["vocab_size"] + image_embeds.shape[1] * (idx - 1),
                    self.model_config["vocab_size"] + image_embeds.shape[1] * idx,
                )
                image_embed_input_ids = image_embed_input_ids.reshape(1, image_embeds.shape[1])

                # Insert pointer to image embedding in input_ids
                input_ids.extend(image_embed_input_ids)
                extra_input_ids.extend(
                    [
                        extra_input_id,
                    ]
                    * image_embed_input_ids.shape[1]
                )
            # Insert tokenized prompt chunk in input_ids
            text_token_ids = self._tokenizer(
                prompt_chunk, return_tensors="pt", padding=True
            ).input_ids
            input_ids.extend(text_token_ids)
            extra_input_ids.extend(
                [
                    0,
                ]
                * len(text_token_ids[0])
            )
        # Convert list of input ids to a single tensor
        input_ids = torch.cat(input_ids)
        return input_ids, extra_input_ids

    def warmup(self):
        result = self.generate("Say Hi", [torch.zeros(size=(0, 0, 0))], [])
        if isinstance(result, concurrent.futures.Future):
            result.result()

    def generate(
        self, prompt, video_embeds, video_frames_times, generation_config=None, chunk=None
    ):
        """Generate a response for prompt using the video embeddings

        Args:
            prompt: Conversation prompt
            video_embeds: Batch of video embeddings
            video_frames_times: Batch of video frame times used for embeddings for each chunk
            generation_config: VLM generation config. Defaults to None.

        Returns:
            List of responses for the batch of chunks
        """
        import tensorrt_llm.bindings.executor as trtllm

        # Populate default values for the VLM generation parameters
        if not generation_config:
            generation_config = {}

        if "temperature" not in generation_config:
            generation_config["temperature"] = 0.4

        if generation_config["temperature"] == 0:
            generation_config.pop("temperature")

        if "max_new_tokens" not in generation_config:
            generation_config["max_new_tokens"] = 512

        if "top_p" not in generation_config:
            generation_config["top_p"] = 1

        if "top_k" not in generation_config:
            generation_config["top_k"] = 100
        generation_config["top_k"] = int(generation_config["top_k"])

        if "seed" in generation_config:
            seed = generation_config["seed"]
            generation_config.pop("seed")
        else:
            seed = 1

        # Set the seed
        random.seed(seed)
        numpy.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        prompt_template_with_timestamp = ""
        string_of_times = ""
        string_timestamp = ""
        time_format_str = ""

        # Need to handle if batch size is not 1
        tidx = 0
        if len(video_frames_times) == 1:
            video_frames_times_ = video_frames_times[0]
            num_of_embeds_in_one_chunk = int(len(video_frames_times_))
            for j in range(num_of_embeds_in_one_chunk):
                if chunk:
                    if tidx <= len(chunk):
                        string_timestamp = chunk[tidx].get_timestamp(video_frames_times_[j])
                        if not time_format_str:
                            if chunk[tidx].file.startswith("rtsp://"):
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

            prompt_template_with_timestamp = (
                "<|im_start|>system\n These are images sampled from a video "
                + time_format_str
                + " : "
                + string_of_times
                + "."
                + "Make sure the answer contain correct timestamps.<|im_end|>"
            )

        if prompt_template_with_timestamp:
            prompt = prompt_template_with_timestamp + prompt

        # Tokenize the prompt, create a batched input_ids of the same size as video_embeds
        input_ids, extra_input_ids = self._get_input_ids_from_prompt(prompt, video_embeds[0])

        prompt_table = video_embeds[0].view(
            (
                video_embeds[0].shape[0] * video_embeds[0].shape[1],
                video_embeds[0].shape[2],
            )
        )
        prompt_table = prompt_table.cuda().to(dtype=torch.float16).unsqueeze(0)

        # Populate TRT-LLM SamplingConfig
        req_id = None
        output_ids = None

        output_config = trtllm.OutputConfig(exclude_input_from_output=True)
        max_new_tokens = generation_config.pop("max_new_tokens")
        sampling_config = trtllm.SamplingConfig(**generation_config, seed=seed)
        output_ids = []
        with torch.no_grad():
            prompt_tuning_config = trtllm.PromptTuningConfig(
                embedding_table=prompt_table[0].detach(),
                input_token_extra_ids=extra_input_ids,
            )
            request = trtllm.Request(
                input_token_ids=input_ids.tolist(),
                max_tokens=max_new_tokens,
                sampling_config=sampling_config,
                output_config=output_config,
                prompt_tuning_config=prompt_tuning_config,
                end_id=self._tokenizer.eos_token_id,
                pad_id=self._tokenizer.pad_token_id,
                lora_config=self._trt_lora_config,
            )
            # Recreate TRT Lora Config with just the ID. Sending a 2nd request
            # with the same ID and weights in the config results in an error
            if self._trt_lora_config:
                self._trt_lora_config = trtllm.LoraConfig(self._lora_config_id)
            req_id = self._executor.enqueue_request(request)
            self._inflight_req_ids.append(req_id)

        if self._output_tpool:
            return self._output_tpool.submit(
                self._postprocess, req_id, output_ids, input_ids.shape[-1]
            )
        else:
            return self._postprocess(req_id, output_ids, input_ids.shape[-1])

    @staticmethod
    def get_model_info():
        return "vila-1.5", "internal", "NVIDIA"
