######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import json

# import time
import numpy as np
import torch

# triton_python_backend_utils is available in every Triton Python model. You
# need to use this module to create inference requests and responses. It also
# contains some utility functions for extracting information from model_config
# and converting Triton input/output types to numpy types.
import triton_python_backend_utils as pb_utils

# from torch.utils.dlpack import from_dlpack, to_dlpack
from transformers import AutoTokenizer

from .utils import tokenize_captions


def process_captions(in_1_np):
    """Process input numpy array to extract and format captions."""
    # cat_lists = []
    for slice in in_1_np:
        try:
            caption = np.trim_zeros(slice).tobytes().decode("UTF-8")
        except Exception:
            # caption = "cars . bus . person . bicycle ."
            caption = ""

        caption = caption.lower().strip()
        if not caption.endswith("."):
            caption += "."

        captions_list = caption.split(" . ")
        captions_list[-1] = captions_list[-1].replace(" .", "")
        break  # Only process the first slice as per original code

    # print (f"caption: {caption}")
    # print (f"captions_list: {captions_list}")
    return captions_list, caption


class TritonPythonModel:
    """Your Python model must use the same class name. Every Python model
    that is created must have "TritonPythonModel" as the class name.
    """

    def initialize(self, args):
        """`initialize` is called only once when the model is being loaded.
        Implementing `initialize` function is optional. This function allows
        the model to initialize any state associated with this model.

        Parameters
        ----------
        args : dict
          Both keys and values are strings. The dictionary keys and values are:
          * model_config: A JSON string containing the model configuration
          * model_instance_kind: A string containing model instance kind
          * model_instance_device_id: A string containing model instance device ID
          * model_repository: Model repository path
          * model_version: Model version
          * model_name: Model name
        """

        # You must parse model_config. JSON string is not parsed here
        self.model_config = json.loads(args["model_config"])

        # Store data types for later use
        output_configs = {
            "input_ids": "output1_dtype",
            "attention_mask": "output2_dtype",
            "position_ids": "output3_dtype",
            "token_type_ids": "output4_dtype",
            "text_token_mask": "output5_dtype",
            "pos_map": "output6_dtype",
            "target_sizes": "output7_dtype",
        }

        for name, attr in output_configs.items():
            setattr(
                self,
                attr,
                pb_utils.triton_string_to_numpy(
                    pb_utils.get_output_config_by_name(self.model_config, name)["data_type"]
                ),
            )

        self.tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased", use_fast=True)
        # self.tokenizer = BertTokenizerFast.from_pretrained(
        #   "google-bert/bert-base-uncased",use_fast=True)
        # self.model = BertModel.from_pretrained("google-bert/bert-base-uncased")

        # Create a dictionary for caching multiple category lists and their tensors
        self.tensor_cache = {}

        # Set a cache size limit to prevent memory issues
        self.max_cache_size = (
            10  # Adjust based on expected variety of inputs and memory constraints
        )

        self._max_text_len = 256

        # Use shared memory for frequently accessed data
        self.special_tokens = np.array(
            self.tokenizer.convert_tokens_to_ids(["[CLS]", "[SEP]", ".", "?"]), dtype=np.int64
        )

        # Pre-allocate target sizes in shared memory
        self.target_size_template = np.array([[960, 544, 960, 544]], dtype=np.int32)

        # Use LRU cache for tensor cache to prevent memory leaks
        from functools import lru_cache

        self.get_cached_tensors = lru_cache(maxsize=10)(self._get_cached_tensors)

        device = "cuda" if args["model_instance_kind"] == "GPU" else "cpu"
        print(f"device: {device}")
        device_id = args["model_instance_device_id"]
        self.device = f"{device}:{device_id}"
        self.streams = torch.cuda.Stream()

    def _get_cached_tensors(self, cache_key):
        """Cache tokenization results with LRU policy."""
        return self.tensor_cache.get(cache_key)

    def execute(self, requests):
        """`execute` MUST be implemented in every Python model. `execute`
        function receives a list of pb_utils.InferenceRequest as the only
        argument. This function is called when an inference request is made
        for this model. Depending on the batching configuration (e.g. Dynamic
        Batching) used, `requests` may contain multiple requests. Every
        Python model, must create one pb_utils.InferenceResponse for every
        pb_utils.InferenceRequest in `requests`. If there is an error, you can
        set the error argument when creating a pb_utils.InferenceResponse

        Parameters
        ----------
        requests : list
          A list of pb_utils.InferenceRequest

        Returns
        -------
        list
          A list of pb_utils.InferenceResponse. The length of this list must
          be the same as `requests`
        """

        responses = []

        # Process requests in batches to reduce memory allocation
        for request in requests:
            in_1 = pb_utils.get_input_tensor_by_name(request, "PROMPT")
            in_1_np = in_1.as_numpy()
            batch_size = in_1_np.shape[0]

            # Process captions efficiently
            cat_lists, cache_key = process_captions(in_1_np)

            # Use cached results if available
            cached_tensors = self.get_cached_tensors(cache_key)
            if cached_tensors:
                (
                    input_ids,
                    attention_mask,
                    position_ids,
                    token_type_ids,
                    text_self_attention_masks,
                    pos_map,
                ) = cached_tensors
            else:
                # Generate tensors only if not in cache
                captions = [" . ".join(cat_lists) + " ."]
                tensors = tokenize_captions(self.tokenizer, cat_lists, captions, self._max_text_len)

                # Update cache with new tensors
                self.tensor_cache[cache_key] = tensors
                (
                    input_ids,
                    attention_mask,
                    position_ids,
                    token_type_ids,
                    text_self_attention_masks,
                    pos_map,
                ) = tensors

            # Efficient batch processing using pre-allocated arrays
            if batch_size > 1:
                # Tile arrays to batch size without adding extra dimensions
                input_ids = np.tile(input_ids, (batch_size, 1))
                attention_mask = np.tile(attention_mask, (batch_size, 1))
                position_ids = np.tile(position_ids, (batch_size, 1))
                token_type_ids = np.tile(token_type_ids, (batch_size, 1))

                # Handle 3D arrays correctly
                text_self_attention_masks = np.tile(text_self_attention_masks, (batch_size, 1, 1))

                # Use broadcasting for target sizes
                target_sizes = np.broadcast_to(
                    self.target_size_template, (batch_size, self.target_size_template.shape[1])
                )
            else:
                target_sizes = self.target_size_template

            pos_map = np.tile(pos_map, (batch_size, 1, 1))
            # Create output tensors efficiently
            output_tensors = [
                pb_utils.Tensor("input_ids", input_ids.astype(np.int64)),
                pb_utils.Tensor("attention_mask", attention_mask.astype(bool)),
                pb_utils.Tensor("position_ids", position_ids.astype(np.int64)),
                pb_utils.Tensor("token_type_ids", token_type_ids.astype(np.int64)),
                pb_utils.Tensor("text_token_mask", text_self_attention_masks.astype(bool)),
                pb_utils.Tensor("pos_map", pos_map.astype(np.float32)),
                pb_utils.Tensor("target_sizes", target_sizes.astype(np.int32)),
            ]

            responses.append(pb_utils.InferenceResponse(output_tensors=output_tensors))

        return responses

    def finalize(self):
        """`finalize` is called only once when the model is being unloaded.
        Implementing `finalize` function is OPTIONAL. This function allows
        the model to perform any necessary clean ups before exit.
        """
        # Clean up any cached tensors
        self.target_size_template = None
        print("Cleaning up...")
