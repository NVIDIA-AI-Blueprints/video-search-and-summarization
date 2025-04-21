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
import base64
import os
from typing import Dict

import numpy as np
import requests
import torch


class Inference:
    def __init__(self):
        self.endpoint = "https://ai.api.nvidia.com/v1/vlm/nvidia/neva-22b"
        self.api_key = os.environ.get("NVIDIA_API_KEY")

    def generate(self, prompt: str, input: torch.Tensor | list[np.ndarray], configs: Dict):
        # one frame processed once
        assert len(input) == 1
        image = input[0]
        image = image.numpy() if isinstance(image, torch.Tensor) else image
        image_b64 = base64.b64encode(image.tobytes()).decode("utf-8")

        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": (prompt + f' <img src="data:image/jpg;base64,{image_b64}" />'),
                }
            ],
            "max_tokens": configs.pop("max_new_tokens", 1024) if configs else 1024,
            "temperature": configs.pop("temperature", 0.20) if configs else 0.20,
            "top_p": configs.pop("top_p", 0.70) if configs else 0.70,
            "seed": configs.pop("seed", 0) if configs else 0,
            "stream": False,
        }
        response = requests.post(self.endpoint, headers=headers, json=payload)
        summary = response.json()["choices"][0]["message"]["content"] if response.ok else None

        return summary
