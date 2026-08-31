# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Patch installed vLLM Qwen2.5-VL vision attention to keep upstream FlashAttention.

vLLM 0.11.1 can select the bundled vLLM FlashAttention backend for Qwen2.5-VL
vision attention, then falls back to TORCH_SDPA when the vision head dimension
is not a multiple of 32. Cosmos Reason 2 uses head_dim=72, so this fallback
underfeeds H100 during max-live-stream benchmarks.

The upstream flash_attn package supports this shape. This patch mirrors the
older working behavior: when upstream FlashAttention is available and the user
did not explicitly override the multimodal encoder backend, keep FLASH_ATTN but
mark it as upstream FA so Qwen2.5-VL does not fall back to TORCH_SDPA.
"""

VLLM_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
QWEN25_FILE = f"{VLLM_ROOT}/model_executor/models/qwen2_5_vl.py"


def patch_qwen25_vision_attention():
    content = open(QWEN25_FILE).read()
    orig = content

    if (
        "maybe_get_vit_flash_attn_backend" not in content
        and "self.attn = MMEncoderAttention(" in content
        and "class Qwen2_5_VisionAttention" in content
    ):
        print(
            "  ✓ installed vLLM uses the unified MMEncoderAttention path, "
            "legacy Qwen2.5-VL FlashAttention patch not required."
        )
        return

    def apply(old, new, tag):
        nonlocal content
        if new in content:
            print(f"  ✓ {tag} already patched, skipping.")
            return
        assert old in content, f"PATCH ANCHOR NOT FOUND: {tag}"
        content = content.replace(old, new, 1)
        print(f"  ✓ {tag}")

    apply(
        "from vllm.attention.layer import maybe_get_vit_flash_attn_backend",
        "from vllm.attention.layer import (\n"
        "    check_upstream_fa_availability,\n"
        "    maybe_get_vit_flash_attn_backend,\n"
        ")",
        "import check_upstream_fa_availability",
    )

    apply(
        "        use_upstream_fa = False\n"
        "        self.attn_backend = get_vit_attn_backend(\n"
        "            head_size=head_dim,\n"
        "            dtype=torch.get_default_dtype(),\n"
        "            attn_backend_override=attn_backend_override,\n"
        "        )\n"
        "\n"
        "        self.attn_backend, self.flash_attn_varlen_func = (\n",
        "        use_upstream_fa = False\n"
        "        self.attn_backend = get_vit_attn_backend(\n"
        "            head_size=head_dim,\n"
        "            dtype=torch.get_default_dtype(),\n"
        "            attn_backend_override=attn_backend_override,\n"
        "        )\n"
        "\n"
        "        if (\n"
        "            head_dim % 32 != 0\n"
        "            and attn_backend_override is None\n"
        "            and check_upstream_fa_availability(torch.get_default_dtype())\n"
        "        ):\n"
        "            self.attn_backend = AttentionBackendEnum.FLASH_ATTN\n"
        "            use_upstream_fa = True\n"
        "\n"
        "        self.attn_backend, self.flash_attn_varlen_func = (\n",
        "prefer upstream FlashAttention for incompatible bundled head_dim",
    )

    apply(
        "        if self.attn_backend in {\n"
        "            AttentionBackendEnum.FLASH_ATTN,\n"
        "            AttentionBackendEnum.ROCM_AITER_FA,\n"
        "        } and self.hidden_size_per_attention_head % 32 != 0:\n",
        "        if (\n"
        "            not self.use_upstream_fa\n"
        "            and self.attn_backend in {\n"
        "                AttentionBackendEnum.FLASH_ATTN,\n"
        "                AttentionBackendEnum.ROCM_AITER_FA,\n"
        "            }\n"
        "            and self.hidden_size_per_attention_head % 32 != 0\n"
        "        ):\n",
        "skip TORCH_SDPA fallback for upstream FlashAttention",
    )

    if content != orig:
        with open(QWEN25_FILE, "w") as f:
            f.write(content)
    else:
        print("  No changes needed.")


if __name__ == "__main__":
    print("Applying vLLM Qwen2.5-VL vision attention patch...")
    patch_qwen25_vision_attention()
    print("Done.")
