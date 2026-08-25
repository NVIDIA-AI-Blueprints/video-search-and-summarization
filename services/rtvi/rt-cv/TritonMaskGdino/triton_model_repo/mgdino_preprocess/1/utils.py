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

"""Utility functions to be used for Grounding DINO."""

import numpy as np

def generate_masks_with_special_tokens_and_transfer_map(tokenized, special_tokens_list):
    """Generate attention mask between each pair of special tokens.

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
        if col in (0, num_token - 1):
            attention_mask[row, col, col] = True
            position_ids[row, col] = 0
        else:
            attention_mask[row, previous_col + 1: col + 1, previous_col + 1: col + 1] = True
            position_ids[row, previous_col + 1: col + 1] = np.arange(
                0, col - previous_col
            )
            c2t_maski = np.zeros((num_token), dtype=bool)
            c2t_maski[previous_col + 1: col] = True
            cate_to_token_mask_list[row].append(c2t_maski)
        previous_col = col
    return attention_mask, position_ids


def create_positive_map(tokenized, tokens_positive, cat_list, caption, max_text_len=256):
    """construct a map such that positive_map[i,j] = True iff box i is associated to token j

    Args:
        tokenized:
            - input_ids: Tensor[1, ntokens]
            - attention_mask: Tensor[1, ntokens]
        token_span: list with length num_boxes.
            - each item: [start_idx, end_idx]
    """
    positive_map = np.zeros((len(tokens_positive), max_text_len), dtype=float)

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
        # assert beg_pos is not None and end_pos is not None
        positive_map[j, beg_pos: end_pos + 1].fill(1)
    return positive_map


def tokenize_captions(tokenizer, cat_list, caption, max_text_len=256):
    """tokenize captions."""
    specical_tokens = tokenizer.convert_tokens_to_ids(["[CLS]", "[SEP]", ".", "?"])
    tokenized = tokenizer(caption, padding="max_length", return_tensors="np", max_length=max_text_len)

    label_list = np.arange(len(cat_list))
    pos_map = create_positive_map(tokenized, label_list, cat_list, caption[0], max_text_len=max_text_len)

    (
        text_self_attention_masks,
        position_ids,
    ) = generate_masks_with_special_tokens_and_transfer_map(
        tokenized, specical_tokens)

    if text_self_attention_masks.shape[1] > max_text_len:
        text_self_attention_masks = text_self_attention_masks[
            :, : max_text_len, : max_text_len]

        position_ids = position_ids[:, : max_text_len]
        tokenized["input_ids"] = tokenized["input_ids"][:, : max_text_len]
        tokenized["attention_mask"] = tokenized["attention_mask"][:, : max_text_len]
        tokenized["token_type_ids"] = tokenized["token_type_ids"][:, : max_text_len]

    input_ids = tokenized["input_ids"].astype(int)
    attention_mask = tokenized["attention_mask"].astype(bool)
    position_ids = position_ids.astype(int)
    token_type_ids = tokenized["token_type_ids"].astype(int)
    text_self_attention_masks = text_self_attention_masks.astype(bool)

    return input_ids, attention_mask, position_ids, token_type_ids, text_self_attention_masks, pos_map
