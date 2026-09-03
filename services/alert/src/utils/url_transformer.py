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

A VST media URL has two audiences that need different origins, and neither can
use the other's: the UI and Elasticsearch are read from outside the deployment,
while this service and a local VLM fetch the bytes from inside it.

VST mints the URL on the origin an outside caller can reach
(``VST_INGRESS_ENDPOINT``, derived from the public origin), because that copy is
handed onward to consumers we do not control -- a browser, a webhook body, an
alert payload -- and nothing out there can repair an origin it cannot resolve.
So the outward copies need no transformation, and the inward ones are translated
back to ``VST_INTERNAL_URL`` here. That is the same direction the agent
translates in ``vss_agents/utils/url_translation.rewrite_to_internal_vst_url``.

``transform_video_url`` remains for deployments that still mint on
``INTERNAL_IP``: it is a no-op unless that string actually appears in the URL, so
it neither helps nor harms once the origin is public.

Environment Variables:
    VST_INTERNAL_URL: Origin this deployment's own containers reach VST on
    EXTERNAL_IP: External IP accessible to UI users and remote VLM
    INTERNAL_IP: Internal IP used by VST in video URLs
    VLM_MODE: 'remote', 'local', or 'local_shared'

Decision Matrix:
    | VLM_MODE     | VLM fetches from | VLM URL  | ES/UI URL |
    |--------------|------------------|----------|-----------|
    | local        | inside           | internal | as minted |
    | local_shared | inside           | internal | as minted |
    | remote       | outside          | as minted| as minted |
"""

import os
from urllib.parse import urlparse
from urllib.parse import urlunparse


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


def rewrite_to_internal_url(url: str) -> str:
    """Re-anchor a VST media URL on the origin this deployment reaches VST on.

    Keeps the path and query and replaces only scheme and authority, so a URL
    minted on the public origin can be fetched from inside the network -- where
    that origin may be a TLS host that terminates upstream, or a name with no
    record on the container's resolver.

    Returns *url* unchanged when there is nothing to do: no
    ``VST_INTERNAL_URL`` configured, a URL with no authority to replace, or a
    path outside ``/vst/`` that VST does not serve. Leaving it alone is right in
    each of those cases -- rewriting a URL we cannot reason about would turn a
    fetch that works into one that does not.
    """
    internal_url = os.environ.get('VST_INTERNAL_URL', '')
    if not url or not internal_url:
        return url

    parsed = urlparse(url)
    if not parsed.netloc or not (parsed.path or '').startswith('/vst/'):
        return url

    internal = urlparse(internal_url.rstrip('/'))
    if not internal.netloc:
        return url

    return urlunparse(parsed._replace(scheme=internal.scheme, netloc=internal.netloc))


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
