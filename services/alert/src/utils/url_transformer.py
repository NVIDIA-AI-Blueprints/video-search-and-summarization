# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""URL transformation utilities for video URLs.

VST returns video URLs using the internal/host IP (INTERNAL_IP), which may not be
accessible to:
1. VLM running on an external network (remote mode)
2. External UI users viewing ES data

This module provides functions to transform URLs from internal to external form
based on VLM_MODE configuration.

Environment Variables:
    EXTERNAL_IP: External IP accessible to UI users and remote VLM
    INTERNAL_IP: Internal IP used by VST in video URLs
    VLM_MODE: 'remote', 'local', or 'local_shared'

Decision Matrix:
    | VLM_MODE     | VLM Needs | ES/UI Needs | VLM Transform | ES Transform |
    |--------------|-----------|-------------|---------------|--------------|
    | local        | Internal  | External    | None          | int->ext     |
    | local_shared | Internal  | External    | None          | int->ext     |
    | remote       | External  | External    | int->ext      | int->ext     |
"""

import os


def transform_video_url(url: str, to_external: bool) -> str:
    """Transform video URL from internal to external form.

    Args:
        url: The video URL to transform
        to_external: If True, replace INTERNAL_IP with EXTERNAL_IP

    Returns:
        Transformed URL if to_external is True and both IPs are configured,
        otherwise returns the original URL unchanged.
    """
    if not to_external:
        return url

    internal_ip = os.environ.get('INTERNAL_IP', '')
    external_ip = os.environ.get('EXTERNAL_IP', '')

    if not internal_ip or not external_ip:
        return url

    if internal_ip in url:
        return url.replace(internal_ip, external_ip)

    return url


def is_vlm_local() -> bool:
    """Check if VLM is running in local mode.

    VLM_MODE can be:
        - 'remote': VLM is on external network, needs external URLs
        - 'local': VLM is on same network, can use internal URLs
        - 'local_shared': VLM is local shared instance, can use internal URLs

    Returns:
        True if VLM_MODE contains 'local' (covers both 'local' and 'local_shared'),
        False otherwise (defaults to treating as remote if not set).
    """
    vlm_mode = os.environ.get('VLM_MODE', '')
    return 'local' in vlm_mode.lower()
