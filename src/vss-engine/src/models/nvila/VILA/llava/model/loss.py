# Copyright 2024 NVIDIA CORPORATION & AFFILIATES
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

from typing import List, Union

import torch
from torch.nn.functional import cross_entropy

from llava.constants import IGNORE_INDEX

__all__ = ["soft_cross_entropy"]


def soft_cross_entropy(
    outputs: torch.Tensor,
    targets: torch.Tensor,
    soft_tokens: Union[torch.Tensor, List[int]],
    std: float = 1,
    ignore_index: int = IGNORE_INDEX,
) -> torch.Tensor:
    # Remove last token from outputs and first token from targets
    outputs = outputs[..., :-1, :].contiguous()
    targets = targets[..., 1:].contiguous()

    # Flatten outputs and targets
    targets = targets.view(-1)
    outputs = outputs.view(targets.size(0), -1)

    # Remove outputs and targets with ignore_index
    indices = targets != ignore_index
    outputs = outputs[indices]
    targets = targets[indices]

    # Convert soft token IDs to tensor
    if isinstance(soft_tokens, list):
        soft_tokens = torch.tensor(soft_tokens).to(targets)

    # Calculate loss for non-soft tokens
    indices = torch.isin(targets, soft_tokens, invert=True)
    loss = cross_entropy(outputs[indices], targets[indices], reduction="sum")

    # Calculate loss for soft tokens
    indices = torch.isin(targets, soft_tokens)
    targets_indices = torch.zeros_like(outputs[indices])
    for k, target in enumerate(targets[indices]):
        dist = torch.exp(-((target - soft_tokens) ** 2) / (2 * std**2))
        targets_indices[k][soft_tokens] = dist / dist.sum()
    loss += cross_entropy(outputs[indices], targets_indices, reduction="sum")

    # Return average loss
    return loss / targets.size(0)
