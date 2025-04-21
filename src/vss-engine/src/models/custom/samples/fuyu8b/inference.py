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
import os
from typing import Dict

import torch
from transformers import FuyuForCausalLM, FuyuProcessor

DEVICE = "cuda"


class Inference:
    def __init__(self):
        self.model_path = os.path.dirname(os.path.abspath(__file__))
        self.processor = FuyuProcessor.from_pretrained(self.model_path)
        self.model = FuyuForCausalLM.from_pretrained(self.model_path, device_map=DEVICE)

    def generate(self, prompt: str, input: torch.tensor, configs: Dict):
        assert input.dim() == 4
        images = list(torch.unbind(input, dim=0))
        prompt = "These are images from the same video. " + prompt
        inputs = self.processor(text=prompt, images=images, return_tensors="pt").to(DEVICE)
        max_new_tokens = 128
        try:
            max_new_tokens = configs["max_new_tokens"]
        except Exception:
            pass
        generation_output = self.model.generate(**inputs, max_new_tokens=max_new_tokens)
        generation_text = self.processor.batch_decode(
            generation_output[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )

        return generation_text[0]
