####################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
####################################################################################################

import io
import json

import numpy as np
import cupy as cp
import torchvision.transforms as transforms
import torch
import time

from transformers import AutoTokenizer
from torch.utils.dlpack import from_dlpack, to_dlpack
from .utils_gpu import tokenize_captions_gpu


# triton_python_backend_utils is available in every Triton Python model. You
# need to use this module to create inference requests and responses. It also
# contains some utility functions for extracting information from model_config
# and converting Triton input/output types to numpy types.
import triton_python_backend_utils as pb_utils


def generate_masks_with_special_tokens_and_transfer_map(tokenized, special_tokens_list, tokenizer):
    """Generate attention mask between each pair of special tokens

    Args:
        input_ids (torch.Tensor): input ids. Shape: [bs, num_token]
        special_tokens_mask (list): special tokens mask.

    Returns:
        torch.Tensor: attention mask between each special tokens.
    """
    start_time = time.time()
    input_ids = tokenized["input_ids"]
    bs, num_token = input_ids.shape
    # special_tokens_mask: bs, num_token. 1 for special tokens. 0 for normal tokens
    special_tokens_mask = torch.zeros((bs, num_token), device=input_ids.device
            ).bool()
    for special_token in special_tokens_list:
        special_tokens_mask |= input_ids == special_token

    # idxs: each row is a list of indices of special tokens
    idxs = torch.nonzero(special_tokens_mask)

    # generate attention mask and positional ids
    attention_mask = (
        torch.eye(num_token#, device=input_ids.device
            ).bool().unsqueeze(0).repeat(bs, 1, 1)
    )
    #position_ids = torch.zeros((bs, num_token), device=input_ids.device)
    position_ids = torch.zeros(bs, num_token)
    #cate_to_token_mask_list = [[] for _ in range(bs)]
    previous_col = 0
    #print (idxs.shape)
    for i in range(idxs.shape[0]):
        row, col = idxs[i]
        if col in (0, num_token - 1):
            attention_mask[row, col, col] = True
            position_ids[row, col] = 0
        else:
            attention_mask[row, previous_col + 1: col + 1, previous_col + 1: col + 1] = True
            position_ids[row, previous_col + 1: col + 1] = torch.arange(
                0, col - previous_col
                #, device=input_ids.device
            )
            #start_time = time.time()
            #c2t_maski = torch.zeros((num_token), device=input_ids.device).bool()
            #c2t_maski[previous_col + 1: col] = True
            #print("c2t_maski--------- %s seconds ----------------" % (time.time() - start_time))
            #cate_to_token_mask_list[row].append(c2t_maski)
        previous_col = col

    #cate_to_token_mask_list = [
    #    torch.stack(cate_to_token_mask_listi, dim=0)
    #    for cate_to_token_mask_listi in cate_to_token_mask_list
    #]

    #print("transfer map--------- %s seconds ----------------" % (time.time() - start_time))

    return attention_mask, position_ids.to(torch.long), None #cate_to_token_mask_list

def generate_masks_with_special_tokens_and_transfer_map_np(tokenized, special_tokens_list):
    """Generate attention mask between each pair of special tokens
    Args:
        input_ids (torch.Tensor): input ids. Shape: [bs, num_token]
        special_tokens_mask (list): special tokens mask.
    Returns:
        torch.Tensor: attention mask between each special tokens.
    """
    input_ids = tokenized["input_ids"]
    bs, num_token = input_ids.shape
    # special_tokens_mask: bs, num_token. 1 for special tokens. 0 for normal tokens
    special_tokens_mask = np.zeros((bs, num_token), dtype=bool)
    for special_token in special_tokens_list:
        special_tokens_mask |= input_ids == special_token

    # idxs: each row is a list of indices of special tokens
    idxs = np.stack(np.nonzero(special_tokens_mask), axis=1)

    # generate attention mask and positional ids
    attention_mask = (
        np.tile(np.expand_dims(np.eye(num_token, dtype=bool), axis=0), (bs, 1, 1))
    )
    position_ids = np.zeros((bs, num_token))
    cate_to_token_mask_list = [[] for _ in range(bs)]
    previous_col = 0
    for i in range(idxs.shape[0]):
        row, col = idxs[i]
        if (col == 0) or (col == num_token - 1):
            attention_mask[row, col, col] = True
            position_ids[row, col] = 0
        else:
            attention_mask[row, previous_col + 1 : col + 1, previous_col + 1 : col + 1] = True
            position_ids[row, previous_col + 1 : col + 1] = np.arange(
                0, col - previous_col
            )
            c2t_maski = np.zeros((num_token), dtype=bool)
            c2t_maski[previous_col + 1 : col] = True
            cate_to_token_mask_list[row].append(c2t_maski)
        previous_col = col
    return attention_mask, position_ids



def create_positive_map(tokenized, tokens_positive, cat_list, caption, max_text_len=256, return_tensors="np"):
    """construct a map such that positive_map[i,j] = True iff box i is associated to token j

    Args:
        tokenized:
            - input_ids: Tensor[1, ntokens]
            - attention_mask: Tensor[1, ntokens]
        token_span: list with length num_boxes.
            - each item: [start_idx, end_idx]
    """
    bs = len(cat_list)
    if return_tensors=="pt":
        positive_map = torch.zeros((bs, len(tokens_positive), max_text_len), dtype=torch.float)
    else:
        positive_map = np.zeros((bs, len(tokens_positive), max_text_len), dtype=float)
    #print (f"tokens_positive {tokens_positive}")
    #print (type(tokenized))
    for k in np.arange(bs):
        for j, label in enumerate(tokens_positive):
            start_ind = caption[k].find(cat_list[k][label])
            end_ind = start_ind + len(cat_list[k][label]) - 1
            if (start_ind < 0):
                continue

            beg_pos = tokenized.char_to_token(start_ind)
            try:
                end_pos = tokenized.char_to_token(end_ind)
            except Exception:
                end_pos = None
            if end_pos is None:
                try:
                    end_pos = tokenized.char_to_token(end_ind - 1)
                    if end_pos is None:
                        end_pos = tokenized.char_to_token(end_ind - 2)
                except Exception:
                    end_pos = None

            if beg_pos is None or end_pos is None:
                continue
            if beg_pos < 0 or end_pos < 0:
                continue
            if beg_pos > end_pos:
                continue

            #print (f"{j}-{beg_pos}-{end_pos+1}")
            # assert beg_pos is not None and end_pos is not None
            positive_map[k,j, beg_pos: end_pos + 1].fill_(1)

        #print (positive_map)
    #print (positive_map.shape)

    for k in np.arange(bs):
        pos_maps = positive_map[k]
        for label_ind in range(len(pos_maps)):
            if pos_maps[label_ind].sum() != 0:
                pos_maps[label_ind] = pos_maps[label_ind] / pos_maps[label_ind].sum()
    return positive_map

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
        self.model_config = model_config = json.loads(args["model_config"])

        # Get OUTPUT0 configuration
        #output0_config = pb_utils.get_output_config_by_name(model_config, "inputs")
        output1_config = pb_utils.get_output_config_by_name(model_config, "input_ids")
        output2_config = pb_utils.get_output_config_by_name(model_config, "attention_mask")
        output3_config = pb_utils.get_output_config_by_name(model_config, "position_ids")
        output4_config = pb_utils.get_output_config_by_name(model_config, "token_type_ids")
        output5_config = pb_utils.get_output_config_by_name(model_config, "text_token_mask")
        output6_config = pb_utils.get_output_config_by_name(model_config, "pos_map")
        output7_config = pb_utils.get_output_config_by_name(model_config, "target_sizes")

        # Convert Triton types to numpy types
        #self.output0_dtype = pb_utils.triton_string_to_numpy(
        #    output0_config["data_type"]
        #)
        self.output1_dtype = pb_utils.triton_string_to_numpy(
            output1_config["data_type"]
        )
        self.output2_dtype = pb_utils.triton_string_to_numpy(
            output2_config["data_type"]
        )
        self.output3_dtype = pb_utils.triton_string_to_numpy(
            output3_config["data_type"]
        )
        self.output4_dtype = pb_utils.triton_string_to_numpy(
            output4_config["data_type"]
        )
        self.output5_dtype = pb_utils.triton_string_to_numpy(
            output5_config["data_type"]
        )
        self.output6_dtype = pb_utils.triton_string_to_numpy(
            output6_config["data_type"]
        )
        self.output7_dtype = pb_utils.triton_string_to_numpy(
            output7_config["data_type"]
        )
        self.tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
        self.specical_tokens = self.tokenizer.convert_tokens_to_ids(
            ["[CLS]", "[SEP]", ".", "?"]
        )
        self._max_text_len = 256
        self.custom_stream = torch.cuda.Stream()

        device = "cuda" if args["model_instance_kind"] == "GPU" else "cpu"
        device_id = args["model_instance_device_id"]
        self.device = f"{device}:{device_id}"
        
        print(f"[MaskGDINO Preprocess] GPU-accelerated processing with CuPy ENABLED on {self.device}")


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

        #output0_dtype = self.output0_dtype
        output1_dtype = self.output1_dtype
        output2_dtype = self.output2_dtype
        output3_dtype = self.output3_dtype
        output4_dtype = self.output4_dtype
        output5_dtype = self.output5_dtype
        output6_dtype = self.output6_dtype
        output7_dtype = self.output7_dtype

        responses = []

        start_time = time.time()
        # Every Python backend must iterate over everyone of the requests
        # and create a pb_utils.InferenceResponse for each of them.
        #print (f"Total Requests: {len(requests)}")
        for request in requests:
            # Get INPUT0
            in_1 = pb_utils.get_input_tensor_by_name(request, "PROMPT")

            # Convert pb_utils.Tensor to DLPack tensor (handles GPU tensors)
            dlpack_tensor = in_1.to_dlpack()
            # Convert DLPack tensor to PyTorch tensor
            torch_tensor = from_dlpack(dlpack_tensor)
            # Move the tensor to CPU memory
            cpu_tensor = torch_tensor.cpu()
            in_1_np = cpu_tensor.detach().numpy()

            #print("dlpack--------- %s seconds ----------------" % (time.time() - start_time))

            caption_b = []
            captions_list_b = []
            tokens_positive_list = []
            for slice in in_1_np:
                try:
                   caption = np.trim_zeros(slice).tobytes().decode("UTF-8")
                   #print (f"decoded->{caption}")
                except (UnicodeDecodeError, ValueError) as e:
                    print(f"Exception while decoding caption: {e}")
                    caption="car . person . bus . train . "
                caption = caption.lower()
                caption = caption.strip()
                if not caption.endswith("."):
                    caption = caption + "."

                captions_list = caption.split(" . ")
                captions_list[-1] = captions_list[-1].replace(" .", "")
                caption_b.append( " . ".join(captions_list) + " .")
                captions_list_b.append(captions_list)

            # Use the first caption for all batches (consistent behavior)
            cat_lists = [item for item in captions_list_b[0]] if len(captions_list_b) > 0 else []
            captions = [" . ".join(cat_lists) + ' .'] * in_1_np.shape[0]

            # GPU-optimized tokenization
            input_ids, attention_mask, position_ids, token_type_ids, text_self_attention_masks, pos_map = tokenize_captions_gpu(self.tokenizer, cat_lists, captions, self._max_text_len)
            #print("posmapstart--------- %s seconds ----------------" % (time.time() - start_time))

            # OPTIMIZATION: Use CuPy for GPU-accelerated array operations
            batch_size = in_1_np.shape[0]
            target_sizes_base = cp.array([[960, 544, 960, 544]], dtype=cp.int32)
            pos_map_base = cp.expand_dims(cp.asarray(pos_map), axis=0)
            
            # Repeat on GPU (faster than NumPy)
            target_sizes = cp.repeat(target_sizes_base, batch_size, axis=0)
            pos_map_gpu = cp.repeat(pos_map_base, batch_size, axis=0)
            
            # Convert back to NumPy for Triton output - ensure contiguous arrays
            target_sizes = np.ascontiguousarray(cp.asnumpy(target_sizes))
            pos_map = np.ascontiguousarray(cp.asnumpy(pos_map_gpu))

            #target_sizes = np.array([960, 544, 960, 544])*in_1_np.shape[0]
            '''print (input_ids.shape)
            print (attention_mask.shape)
            print (position_ids.shape)
            print (token_type_ids.shape)
            print (text_self_attention_masks.shape)
            print (pos_map.shape)
            print (target_sizes.shape)

            out_tensor_1 = pb_utils.Tensor.from_dlpack("input_ids", input_ids)
            out_tensor_2 = pb_utils.Tensor.from_dlpack("attention_mask", attention_mask)
            out_tensor_3 = pb_utils.Tensor.from_dlpack("position_ids", position_ids)
            out_tensor_4 = pb_utils.Tensor.from_dlpack("token_type_ids", token_type_ids)
            out_tensor_5 = pb_utils.Tensor.from_dlpack("text_token_mask", text_self_attention_masks)
            out_tensor_6 = pb_utils.Tensor.from_dlpack("pos_map", pos_map)
            out_tensor_7 = pb_utils.Tensor.from_dlpack("target_sizes", target_sizes)
            '''

            out_tensor_1 = pb_utils.Tensor("input_ids", input_ids.astype(output1_dtype))
            out_tensor_2 = pb_utils.Tensor("attention_mask", attention_mask.astype(output2_dtype))
            out_tensor_3 = pb_utils.Tensor("position_ids", position_ids.astype(output3_dtype))
            out_tensor_4 = pb_utils.Tensor("token_type_ids", token_type_ids.astype(output4_dtype))
            out_tensor_5 = pb_utils.Tensor("text_token_mask", text_self_attention_masks.astype(output5_dtype))
            out_tensor_6 = pb_utils.Tensor("pos_map", pos_map.astype(output6_dtype))
            out_tensor_7 = pb_utils.Tensor("target_sizes", target_sizes.astype(output7_dtype))



            # Create InferenceResponse. You can set an error here in case
            # there was a problem with handling this inference request.
            # Below is an example of how you can set errors in inference
            # response:
            #
            # pb_utils.InferenceResponse(
            #    output_tensors=..., TritonError("An error occurred"))
            inference_response = pb_utils.InferenceResponse(
                output_tensors=[#out_tensor_0,
                    out_tensor_1, out_tensor_2, out_tensor_3, out_tensor_4, out_tensor_5, out_tensor_6, out_tensor_7]
            )
            responses.append(inference_response)

            stop_time = time.time()
            #print("------------------------------------- %s seconds --------------------------------------" % (stop_time - start_time))

        # You should return a list of pb_utils.InferenceResponse. Length
        # of this list must match the length of `requests` list.
        return responses

    def finalize(self):
        """`finalize` is called only once when the model is being unloaded.
        Implementing `finalize` function is OPTIONAL. This function allows
        the model to perform any necessary clean ups before exit.
        """
        print("Cleaning up...")
