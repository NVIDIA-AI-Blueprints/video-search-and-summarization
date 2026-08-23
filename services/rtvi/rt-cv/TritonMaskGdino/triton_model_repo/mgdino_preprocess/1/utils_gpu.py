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

"""GPU-optimized utility functions for Grounding DINO using CuPy."""

import numpy as np
import cupy as cp

def generate_masks_with_special_tokens_and_transfer_map_gpu(tokenized, special_tokens_list):
    """Generate attention mask between each pair of special tokens using GPU acceleration.

    Args:
        input_ids (np.ndarray): input ids. Shape: [bs, num_token]
        special_tokens_mask (list): special tokens mask.
    Returns:
        cp.ndarray: attention mask between each special tokens.
    """
    input_ids = tokenized["input_ids"]
    bs, num_token = input_ids.shape
    
    # Move to GPU for processing
    input_ids_gpu = cp.asarray(input_ids)
    
    # special_tokens_mask: bs, num_token. 1 for special tokens. 0 for normal tokens
    special_tokens_mask = cp.zeros((bs, num_token), dtype=cp.bool_)
    for special_token in special_tokens_list:
        special_tokens_mask |= (input_ids_gpu == special_token)

    # idxs: each row is a list of indices of special tokens
    idxs = cp.stack(cp.nonzero(special_tokens_mask), axis=1)
    idxs = cp.asnumpy(idxs)  # Move back to CPU for indexing operations

    # generate attention mask and positional ids
    attention_mask = cp.tile(cp.expand_dims(cp.eye(num_token, dtype=cp.bool_), axis=0), (bs, 1, 1))
    position_ids = cp.zeros((bs, num_token))
    cate_to_token_mask_list = [[] for _ in range(bs)]
    previous_col = 0
    
    for i in range(idxs.shape[0]):
        row, col = idxs[i]
        if col in (0, num_token - 1):
            attention_mask[row, col, col] = True
            position_ids[row, col] = 0
        else:
            attention_mask[row, previous_col + 1: col + 1, previous_col + 1: col + 1] = True
            position_ids[row, previous_col + 1: col + 1] = cp.arange(0, col - previous_col)
            c2t_maski = cp.zeros((num_token), dtype=cp.bool_)
            c2t_maski[previous_col + 1: col] = True
            cate_to_token_mask_list[row].append(c2t_maski)
        previous_col = col
    
    # Keep on GPU for further processing
    return attention_mask, position_ids


def create_positive_map_gpu(tokenized, tokens_positive, cat_list, caption, max_text_len=256):
    """Construct a map such that positive_map[i,j] = True iff box i is associated to token j
    
    GPU-accelerated version using CuPy where applicable.

    Args:
        tokenized:
            - input_ids: Tensor[1, ntokens]
            - attention_mask: Tensor[1, ntokens]
        token_span: list with length num_boxes.
            - each item: [start_idx, end_idx]
    """
    # Initialize on GPU
    positive_map = cp.zeros((len(tokens_positive), max_text_len), dtype=cp.float32)

    for j, label in enumerate(tokens_positive):
        start_ind = caption.find(cat_list[label])
        end_ind = start_ind + len(cat_list[label]) - 1
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
        # Fill the positive map on GPU
        positive_map[j, beg_pos: end_pos + 1] = 1.0
    
    # Convert to NumPy for return
    return cp.asnumpy(positive_map)


def tokenize_captions_gpu(tokenizer, cat_list, caption, max_text_len=256):
    """GPU-optimized tokenization of captions using CuPy for array operations.
    
    Args:
        tokenizer: HuggingFace tokenizer instance
        cat_list: List of category labels
        caption: Caption text (single string or list with one string)
        max_text_len: Maximum text length (default: 256)
    
    Returns:
        Tuple of (input_ids, attention_mask, position_ids, token_type_ids, 
                 text_self_attention_masks, pos_map) as NumPy arrays
    """
    # Tokenization happens on CPU (HuggingFace tokenizer)
    specical_tokens = tokenizer.convert_tokens_to_ids(["[CLS]", "[SEP]", ".", "?"])
    tokenized = tokenizer(caption, padding="max_length", return_tensors="np", max_length=max_text_len)

    label_list = np.arange(len(cat_list))
    
    # Use GPU-accelerated positive map creation
    pos_map = create_positive_map_gpu(tokenized, label_list, cat_list, caption[0], max_text_len=max_text_len)

    # Use GPU-accelerated mask generation
    text_self_attention_masks_gpu, position_ids_gpu = generate_masks_with_special_tokens_and_transfer_map_gpu(
        tokenized, specical_tokens)

    # Truncate if needed (on GPU)
    if text_self_attention_masks_gpu.shape[1] > max_text_len:
        text_self_attention_masks_gpu = text_self_attention_masks_gpu[:, :max_text_len, :max_text_len]
        position_ids_gpu = position_ids_gpu[:, :max_text_len]
        tokenized["input_ids"] = tokenized["input_ids"][:, :max_text_len]
        tokenized["attention_mask"] = tokenized["attention_mask"][:, :max_text_len]
        tokenized["token_type_ids"] = tokenized["token_type_ids"][:, :max_text_len]

    # Convert GPU arrays to NumPy with proper dtypes
    # Ensure all arrays are contiguous NumPy arrays (not CuPy)
    input_ids = np.ascontiguousarray(tokenized["input_ids"].astype(np.int64))
    attention_mask = np.ascontiguousarray(tokenized["attention_mask"].astype(bool))
    position_ids = np.ascontiguousarray(cp.asnumpy(position_ids_gpu).astype(np.int64))
    token_type_ids = np.ascontiguousarray(tokenized["token_type_ids"].astype(np.int64))
    text_self_attention_masks = np.ascontiguousarray(cp.asnumpy(text_self_attention_masks_gpu).astype(bool))
    pos_map = np.ascontiguousarray(pos_map.astype(np.float32))

    return input_ids, attention_mask, position_ids, token_type_ids, text_self_attention_masks, pos_map

