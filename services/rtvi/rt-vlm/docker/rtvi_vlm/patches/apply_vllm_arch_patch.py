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
Patch installed vLLM to register NemotronH_Nano_Omni_Reasoning_V3 (GA checkpoint).

The GA HuggingFace checkpoint (nvidia/Nemotron-Nano-V3-Omni-GA*) uses the
architecture name 'NemotronH_Nano_Omni_Reasoning_V3' in its config.json, while
the installed vLLM only knows 'NemotronH_Nano_VL_V2'.  The two architectures
share identical model structure; only the name changed between EA and GA.

This script adds NemotronH_Nano_Omni_Reasoning_V3 → nano_nemotron_vl.NemotronH_Nano_VL_V2
to vllm/model_executor/models/registry.py in the installed vLLM package.
"""

VLLM_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
REGISTRY_FILE = f"{VLLM_ROOT}/model_executor/models/registry.py"


def patch_registry():
    content = open(REGISTRY_FILE).read()
    anchor = '    "NemotronH_Nano_VL_V2": ("nano_nemotron_vl", "NemotronH_Nano_VL_V2"),'
    addition = (
        '    "NemotronH_Nano_Omni_Reasoning_V3": ("nano_nemotron_vl", "NemotronH_Nano_VL_V2"),'
    )
    assert anchor in content, f"PATCH ANCHOR NOT FOUND in registry.py: {anchor!r}"
    if addition in content:
        print("  ✓ registry.py already patched, skipping.")
        return
    content = content.replace(anchor, f"{anchor}\n{addition}", 1)
    with open(REGISTRY_FILE, "w") as f:
        f.write(content)
    print("  ✓ registry.py: registered NemotronH_Nano_Omni_Reasoning_V3")


if __name__ == "__main__":
    print(
        "Applying vLLM architecture patch for NemotronH_Nano_Omni_Reasoning_V3 (GA checkpoint)..."
    )
    patch_registry()
    print("Done.")
