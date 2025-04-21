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

import numpy as np
import torch

from via_logger import TimeMeasure, logger


def save_jpeg_buffer_as_tensor(numpy_array):
    tensor = torch.from_numpy(numpy_array)
    logger.debug(f"DEBUGME len(tensor)  {len(tensor)}")
    return tensor


def save_jpeg_buffers_as_single_tensor(numpy_arrays):
    """
    Takes an array of 10 numpy arrays and returns a single PyTorch tensor of shape (1, 10, N),
    where N is the size of the largest numpy array after padding all other arrays with zeros.
    """
    # Find the size of the largest numpy array
    max_size = max(arr.size for arr in numpy_arrays)

    # Pad all other numpy arrays with zeros to match the largest size
    padded_arrays = []
    for arr in numpy_arrays:
        padded_arr = np.pad(arr, (0, max_size - arr.size), mode="constant", constant_values=0)
        padded_arrays.append(padded_arr)

    # Stack the padded numpy arrays into a single PyTorch tensor
    stacked_tensor = torch.stack(list(map(torch.from_numpy, padded_arrays)))

    return stacked_tensor.unsqueeze(0)


class FrameJPEGTensorGenerator:

    def __init__(self) -> None:
        self._initialized = True

    def get_embeddings(self, frames_: list):
        embeds = []
        with TimeMeasure("Frame JPEG to tensor"):
            for frames in frames_:
                logger.debug(f"len of frames  {len(frames)}")
                embeds.append(save_jpeg_buffers_as_single_tensor(frames))
        return embeds


if __name__ == "__main__":
    # To test and debug, please use harness:
    # PYTHONPATH=src pytest tests/model/gpt4/test_gpt4v_jpeg_tensor_gen.py -s
    # Please add new test case for each bug
    pass
