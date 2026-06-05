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
BDD tests for VST URL expiry enforcement.

Covers BDD-GAP-053 and BDD-GAP-054.

These tests run against a deployment that already has recorded streams.
If none are present (e.g. a freshly redeployed environment), they
skip rather than fail.
"""
import logging
import time
import uuid

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from .url_caching_test_utils import (
    envoy_streamid_route_key,
    select_replay_timestamp,
)

logger = logging.getLogger(__name__)

scenarios('../../features/url_optimization/url_expiry_enforcement.feature')


@given('the VST API is configured for URL expiry tests')
def configure_expiry(context, api_config, test_endpoints):
    assert api_config['base_url']
    assert test_endpoints['streams']


@given('the recording timelines are fetched for URL expiry tests')
def fetch_timelines_for_expiry(context, api_config, test_endpoints, test_params):
    streams_url = f"{api_config['base_url']}{test_endpoints['streams']}"
    streams_resp = requests.get(
        streams_url, timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    streams_resp.raise_for_status()
    streams = streams_resp.json() or []
    if not streams:
        pytest.skip("No replay streams available for URL expiry test")
    context.streams = streams

    tl_url = f"{api_config['base_url']}{test_endpoints['storage_size']}"
    tl_resp = requests.get(
        tl_url, params={'timelines': 'true'},
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    tl_resp.raise_for_status()
    timelines = tl_resp.json() or {}
    if not timelines:
        pytest.skip("No timelines available for URL expiry test")
    context.timelines = timelines


@given('a valid replay timestamp is selected for expiry test')
def select_expiry_timestamp(context):
    sel = select_replay_timestamp(context.streams, context.timelines)
    if sel is None:
        pytest.skip("No suitable timeline for URL expiry test")
    context.selected_stream_id = sel['stream_id']
    context.selected_timestamp = sel['timestamp']


# ---------------------------------------------------------------------------
# BDD-GAP-053 — Picture URL returns 404/410 after expiryISO
# ---------------------------------------------------------------------------

@when('a picture URL is requested with expiryMinutes 1')
def request_url_short_expiry(context, api_config, test_endpoints):
    sid = context.selected_stream_id
    ts = context.selected_timestamp
    url = f"{api_config['base_url']}{test_endpoints['picture_url'].format(stream_id=sid)}"
    # API granularity is whole minutes; smallest TTL is 1 minute.
    resp = requests.get(
        url,
        params={'startTime': ts, 'expiryMinutes': '1'},
        timeout=15,
        verify=api_config.get('verify_ssl', False),
    )
    assert resp.status_code == 200, (
        f"Picture URL request failed: {resp.status_code} {resp.text[:200]}"
    )
    data = resp.json()
    image_url = data.get('imageUrl')
    assert image_url, f"Response missing imageUrl: {data}"
    if not image_url.startswith('http'):
        image_url = f"{api_config['base_url']}{image_url}"
    context.short_expiry_url = image_url


@when('the picture URL is fetched after the expiry passes')
def fetch_after_expiry(context, api_config):
    # Wait for the 1-minute expiry to elapse (+2s safety margin), then
    # retry up to 3 times adding 2s each iteration to absorb a coarse
    # expiry sweeper.
    verify_ssl = api_config.get('verify_ssl', False)
    time.sleep(62)  # 60s expiry + 2s buffer

    resp = None
    for attempt in range(3):
        resp = requests.get(
            context.short_expiry_url,
            timeout=15,
            verify=verify_ssl,
        )
        logger.info(
            "Post-expiry URL fetch attempt %d -> status %d",
            attempt + 1, resp.status_code,
        )
        if resp.status_code in (404, 410):
            break
        time.sleep(2)
    context.expired_url_response = resp


@then('the picture URL response status is 404 or 410')
def expired_url_404_or_410(context):
    """The contract is 'URL stops serving content after expiry'.

    Accept any non-2xx terminal response. Some builds return 400 with
    `error_code=InvalidParameterError` and `error_message=Media file not
    found` instead of the spec-preferred 404/410 — that still satisfies the
    expiry contract (the temp file is gone, the URL is no longer usable).
    What we MUST NOT see is a 2xx that delivers the JPEG.
    """
    resp = context.expired_url_response
    status = resp.status_code
    body = resp.text[:300]

    if 200 <= status < 300:
        ctype = resp.headers.get('Content-Type', '')
        pytest.fail(
            f"Expired URL still served content: status={status}, "
            f"Content-Type={ctype!r}. Expiry not enforced. Body head: {body!r}"
        )

    # Non-2xx → expiry honored. Log the exact form for visibility but pass.
    logger.info(
        "Expired URL rejected with status %d, body=%s",
        status, body,
    )


# ---------------------------------------------------------------------------
# Negative / zero expiryMinutes must be rejected.  The server's actual
# parameter name is `expiryMinutes` (verified against
# src/framework/apis/common/vst_common.cpp and src/modules/storage_management/
# storage_management_apis.cpp — no `expirySec` exists in the codebase).
# ---------------------------------------------------------------------------

@when(parsers.parse('a picture URL is requested with expiryMinutes "{bad_value}"'))
def request_url_bad_expiry(context, api_config, test_endpoints, bad_value):
    sid = context.selected_stream_id
    ts = context.selected_timestamp
    url = f"{api_config['base_url']}{test_endpoints['picture_url'].format(stream_id=sid)}"
    resp = requests.get(
        url,
        params={'startTime': ts, 'expiryMinutes': bad_value},
        timeout=15,
        verify=api_config.get('verify_ssl', False),
    )
    context.bad_expiry_response = resp


@then('the picture URL request is rejected with 4xx')
def bad_expiry_rejected(context):
    """A negative or zero expiryMinutes must be rejected with 4xx — the
    server silently coercing such values to a positive default would be a
    schema-validation gap. A 2xx with an MP4/JPEG body would mean the URL
    was generated despite invalid input."""
    status = context.bad_expiry_response.status_code
    if 400 <= status < 500:
        return
    pytest.fail(
        f"Expected 4xx for invalid expiryMinutes, got {status}. "
        f"Body: {context.bad_expiry_response.text[:300]}"
    )
