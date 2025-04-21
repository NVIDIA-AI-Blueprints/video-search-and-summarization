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


from functools import lru_cache

from langchain_nvidia_ai_endpoints import ChatNVIDIA, Model, register_model
from nemoguardrails.llm.providers import register_llm_provider


@lru_cache
def get_nvcf_llama():
    register_model(
        Model(
            id="meta/llama-3.1-70b-instruct",
            model_type="chat",
            client="ChatNVIDIA",
            endpoint="https://api.nvcf.nvidia.com/v2/nvcf/pexec"
            "/functions/ca7d4a69-52ca-438c-ab4c-6a0b5447fe35",
        )
    )
    llm = ChatNVIDIA(model="meta/llama-3.1-70b-instruct")

    return llm


register_llm_provider("nvcf_llm", get_nvcf_llama)
