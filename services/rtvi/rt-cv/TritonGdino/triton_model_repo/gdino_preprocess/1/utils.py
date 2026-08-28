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

"""Utility functions to be used for Grounding DINO."""

import numpy as np

# import torch


def generate_masks_with_special_tokens_and_transfer_map(tokenized, special_tokens_list):
    """Generate attention mask between each pair of special tokens.

    Args:
        tokenized (dict): Contains "input_ids" tensor. Shape: [bs, num_token]
        special_tokens_list (list): List of special token ids.
    Returns:
        tuple: (attention_mask, position_ids)
    """
    # Convert input_ids to numpy array
    input_ids_np = tokenized["input_ids"]
    bs, num_token = input_ids_np.shape

    # Create special_tokens_mask using numpy operations
    special_tokens_mask = np.zeros((bs, num_token), dtype=bool)
    for special_token in special_tokens_list:
        special_tokens_mask = special_tokens_mask | (input_ids_np == special_token)

    # Get indices of special tokens
    idxs = np.argwhere(special_tokens_mask)

    # Initialize attention_mask and position_ids
    attention_mask = np.zeros((bs, num_token, num_token), dtype=bool)
    for i in range(bs):
        np.fill_diagonal(attention_mask[i], True)

    position_ids = np.zeros((bs, num_token), dtype=np.int64)

    # Process each special token
    for b in range(bs):
        # Get indices for current batch
        batch_idxs = idxs[idxs[:, 0] == b]
        previous_col = 0

        for i in range(len(batch_idxs)):
            row, col = batch_idxs[i]
            if col in (0, num_token - 1):
                # No need for extra operation for first and last tokens
                position_ids[row, col] = 0
            else:
                # Update attention mask
                attention_mask[row, previous_col + 1 : col + 1, previous_col + 1 : col + 1] = True
                # Update position ids
                position_ids[row, previous_col + 1 : col + 1] = np.arange(col - previous_col)
            previous_col = col

    # Convert back to PyTorch tensors on GPU
    return attention_mask, position_ids


def create_positive_map(tokenized, tokens_positive, cat_list, caption, max_text_len=256):
    """Construct a map such that positive_map[i,j] = True iff box i is associated to token j

    Args:
        tokenized: Tokenized input with methods to map characters to tokens.
        tokens_positive: List with length num_boxes containing label indices.
        cat_list: List of category strings.
        caption: Caption string.
        max_text_len: Maximum text length.
    """
    # Initialize positive_map with NumPy
    positive_map = np.zeros((len(tokens_positive), max_text_len), dtype=np.float32)

    # Process each label
    for j, label in enumerate(tokens_positive):
        # String operations on CPU
        start_ind = caption.find(cat_list[label])
        if start_ind == -1:
            continue
        end_ind = start_ind + len(cat_list[label]) - 1

        # Get token positions
        beg_pos = tokenized.char_to_token(start_ind)
        end_pos = None

        try:
            end_pos = tokenized.char_to_token(end_ind)
        except Exception:
            pass

        if end_pos is None:
            try:
                end_pos = tokenized.char_to_token(end_ind - 1)
                if end_pos is None:
                    end_pos = tokenized.char_to_token(end_ind - 2)
            except Exception:
                pass

        if beg_pos is None or end_pos is None:
            continue
        if beg_pos < 0 or end_pos < 0 or beg_pos > end_pos:
            continue

        # Set values in positive_map
        positive_map[j, beg_pos : end_pos + 1] = 1

    # Convert to PyTorch tensor on GPU
    return positive_map


def tokenize_captions(tokenizer, cat_list, caption, max_text_len=256):
    """Tokenize captions and prepare all tensors for the model.

    Args:
        tokenizer: Tokenizer to use.
        cat_list: List of category strings.
        caption: Caption string.
        max_text_len: Maximum text length.

    Returns:
        tuple: (input_ids, attention_mask, position_ids, token_type_ids,
        text_self_attention_masks, pos_map)
    """
    # Get special tokens
    special_tokens = tokenizer.convert_tokens_to_ids(["[CLS]", "[SEP]", ".", "?"])

    # Tokenize
    tokenized = tokenizer(
        caption, padding="max_length", return_tensors="np", max_length=max_text_len
    )

    # Create label list
    label_list = np.arange(len(cat_list))

    # Generate positive map
    pos_map = create_positive_map(
        tokenized, label_list, cat_list, caption[0], max_text_len=max_text_len
    )

    # Generate masks
    text_self_attention_masks, position_ids = generate_masks_with_special_tokens_and_transfer_map(
        tokenized, special_tokens
    )

    # Handle tensor slicing if needed
    if text_self_attention_masks.shape[1] > max_text_len:
        text_self_attention_masks = text_self_attention_masks[:, :max_text_len, :max_text_len]
        position_ids = position_ids[:, :max_text_len]
        tokenized["input_ids"] = tokenized["input_ids"][:, :max_text_len]
        tokenized["attention_mask"] = tokenized["attention_mask"][:, :max_text_len]
        tokenized["token_type_ids"] = tokenized["token_type_ids"][:, :max_text_len]

    # Convert tensors to GPU
    # input_ids = tokenized["input_ids"].cpu().numpy()
    # attention_mask = tokenized["attention_mask"].cpu().numpy()
    # token_type_ids = tokenized["token_type_ids"].cpu().numpy()

    return (
        tokenized["input_ids"],
        tokenized["attention_mask"],
        position_ids,
        tokenized["token_type_ids"],
        text_self_attention_masks,
        pos_map,
    )
