# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""
BDD test for overlay-aware picture URL cache partitioning.

The /picture/url endpoint must include a hash of caller-supplied
overlay/width/height/debug params in its cache key. This mirrors the
video URL partitioning so the same regression (bbox configuration
ignored on repeated requests) can never happen on the picture API
either.
"""

import copy
import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import pytest
import requests
from pytest_bdd import given, parsers, scenarios, then, when

from ..test_utils import assert_with_detailed_failure
from .url_caching_test_utils import (
    CachingTestContext,
    extract_filename_from_url,
    select_replay_timestamp,
)

logger = logging.getLogger(__name__)

scenarios('../../features/url_optimization/picture_overlay_cache_partition.feature')


# ---------------------------------------------------------------------------
# Canonical baseline + named configs
# ---------------------------------------------------------------------------

BASELINE_PICTURE_OVERLAY: Dict[str, Any] = {
    "bbox": {
        "showAll": False,
        "showObjId": True,
        "objectId": ["1001", "1002"],
        "classType": ["Vehicle"],
        "objIdPosition": 0,
        "objIdTextColor": "white",
        "objIdTextBGColor": "red",
    },
    "color": "red",
    "thickness": 5,
    "opacity": 254,
    "debug": False,
}

PICTURE_OVERLAY_A: Dict[str, Any] = {
    "bbox": {
        "showAll": False,
        "showObjId": True,
        "objectId": ["1001", "1002"],
    },
    "color": "red",
    "thickness": 5,
    "opacity": 254,
}

PICTURE_OVERLAY_B: Dict[str, Any] = {
    "bbox": {
        "showAll": False,
        "showObjId": True,
        "objectId": ["2001", "2002"],
    },
    "color": "blue",
    "thickness": 3,
    "opacity": 200,
}


def _matrix_configs() -> List[Dict[str, Any]]:
    """Six mutually-distinct picture overlays. Picked smaller than the video
    matrix because each picture-URL request hits the live media pipeline
    and takes ~1s even on cache miss; six provides healthy distinctness
    coverage without running 30 seconds of pipeline work per scenario."""
    out: List[Dict[str, Any]] = []
    out.append(copy.deepcopy(BASELINE_PICTURE_OVERLAY))
    out.append(_clone_with(BASELINE_PICTURE_OVERLAY, color="green"))
    out.append(_clone_with(BASELINE_PICTURE_OVERLAY, thickness=8))
    out.append(_clone_with(BASELINE_PICTURE_OVERLAY, opacity=64))
    out.append(_clone_with(BASELINE_PICTURE_OVERLAY, bbox={"showAll": True}))
    out.append(_clone_with(BASELINE_PICTURE_OVERLAY, bbox={"objectId": ["9999"]}))
    return out


def _clone_with(base: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """Deep-copy `base` and override either top-level overlay keys or nested
    bbox keys. Pass `bbox={...}` to merge into the bbox dict; any other kwarg
    is set at the top level.
    """
    cfg = copy.deepcopy(base)
    bbox_overrides = overrides.pop("bbox", None)
    for key, value in overrides.items():
        cfg[key] = value
    if bbox_overrides:
        cfg.setdefault("bbox", {}).update(bbox_overrides)
    return cfg


PICTURE_COLOR_CODE_RED = _clone_with(
    BASELINE_PICTURE_OVERLAY,
    overlayColorCode=[{"Vehicle": [255, 0, 0, 255]}],
)
PICTURE_COLOR_CODE_BLUE = _clone_with(
    BASELINE_PICTURE_OVERLAY,
    overlayColorCode=[{"Vehicle": [0, 0, 255, 255]}],
)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _encode_overlay(cfg: Optional[Dict[str, Any]], *,
                    serialize: Optional[str] = None) -> str:
    """URL-encode an overlay dict. `serialize` lets a caller inject a
    pre-built JSON string (used by the whitespace/key-reorder scenarios)
    so the bytes on the wire diverge from what `json.dumps` would emit.
    """
    if serialize is not None:
        return quote(serialize, safe='')
    compact = json.dumps(cfg, separators=(',', ':'), sort_keys=True)
    return quote(compact, safe='')


def _request_picture_url(api_config: dict,
                         test_endpoints: dict,
                         test_params: dict,
                         stream_id: str,
                         timestamp: str,
                         expiry_minutes: int,
                         *,
                         overlay: Optional[Dict[str, Any]] = None,
                         overlay_raw_json: Optional[str] = None,
                         width: Optional[int] = None,
                         height: Optional[int] = None,
                         debug: Optional[bool] = None) -> Dict[str, Any]:
    """Issue a picture URL request with optional overlay/resize/debug params.

    The picture API takes `overlay` (JSON), `width`, `height`, `debug` as
    individual query params - it does NOT use the wrapping `configuration`
    object that the video URL flow uses.
    """
    base = f"{api_config['base_url']}{test_endpoints['picture_url'].format(stream_id=stream_id)}"
    parts = [
        f"startTime={quote(timestamp, safe='')}",
        f"expiryMinutes={expiry_minutes}",
    ]
    if overlay is not None or overlay_raw_json is not None:
        parts.append(f"overlay={_encode_overlay(overlay, serialize=overlay_raw_json)}")
    if width is not None:
        parts.append(f"width={width}")
    if height is not None:
        parts.append(f"height={height}")
    if debug is not None:
        parts.append(f"debug={'true' if debug else 'false'}")
    url = f"{base}?{'&'.join(parts)}"

    start = time.monotonic()
    resp = requests.get(
        url,
        timeout=test_params.get('url_request_timeout', 60),
        verify=api_config.get('verify_ssl', False),
    )
    elapsed = time.monotonic() - start

    if resp.status_code != 200:
        pytest.fail(
            f"Picture URL request failed: status={resp.status_code} "
            f"body={resp.text[:300]} url={url}"
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        pytest.fail(f"Picture URL response is not JSON: {exc}. Body: {resp.text[:200]}")
    if not data.get('imageUrl'):
        pytest.fail(f"Picture URL response missing imageUrl. Body: {data}")

    filename = extract_filename_from_url(data['imageUrl'])
    logger.info("picture URL: file=%s elapsed=%.1fms", filename, elapsed * 1000)
    return {'data': data, 'elapsed': elapsed, 'filename': filename, 'url': url}


# ---------------------------------------------------------------------------
# Background steps
# ---------------------------------------------------------------------------

@given('the VST API is configured for picture overlay cache test')
def vst_api_configured(api_config: Dict[str, Any], test_endpoints: Dict[str, str]) -> None:
    """the VST API is configured for picture overlay cache test"""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['picture_url'], "Picture URL endpoint must be configured"


@when('the list of available replay streams is fetched for picture overlay test')
def fetch_streams(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the list of available replay streams is fetched for picture overlay test"""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    resp = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    resp.raise_for_status()
    context.streams = resp.json()
    assert context.streams, "No replay streams found"


@when('the recording timelines are fetched for picture overlay test')
def fetch_timelines(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the recording timelines are fetched for picture overlay test"""
    url = f"{api_config['base_url']}{test_endpoints['storage_size']}"
    resp = requests.get(
        url,
        params={'timelines': 'true'},
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    resp.raise_for_status()
    context.timelines = resp.json()
    assert context.timelines, "No timeline data found"


@when('a valid picture timestamp is selected for picture overlay test')
def select_picture_timestamp(context: CachingTestContext) -> None:
    """a valid picture timestamp is selected for picture overlay test"""
    result = select_replay_timestamp(context.streams, context.timelines)
    assert result is not None, "No suitable timeline for picture overlay test"
    context.selected_stream_id = result['stream_id']
    context.selected_timestamp = result['timestamp']
    logger.info("Selected stream=%s timestamp=%s",
                result['stream_id'], result['timestamp'])


# ---------------------------------------------------------------------------
# Headline scenarios
# ---------------------------------------------------------------------------

@then('a picture URL is requested with overlay configuration A')
def picture_request_a(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with overlay configuration A"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_A,
    )
    context.first_filename = r['filename']
    context.response_a_first = r['data']


@then('a picture URL is requested with overlay configuration B for the same timestamp')
def picture_request_b(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with overlay configuration B for the same timestamp"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_B,
    )
    context.second_filename = r['filename']


@then('the two picture responses return distinct cached files')
def verify_pic_distinct(context: CachingTestContext) -> None:
    """the two picture responses return distinct cached files"""
    assert_with_detailed_failure(
        context.first_filename != context.second_filename,
        "Picture overlay cache partitioning",
        f"Distinct overlays produced distinct files "
        f"(A={context.first_filename}, B={context.second_filename})",
        f"Server returned the SAME picture ({context.first_filename}) for two different "
        "overlay configs. Picture API has the same regression as the video URL flow - "
        "tryReuseCachedPictureUrl must filter by config_hash.",
    )


@then('a picture URL is requested with a red positional color code')
def picture_request_red_color_code(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request an overlay with positional RGBA values."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_COLOR_CODE_RED,
    )
    context.first_filename = result['filename']


@then('a picture URL is requested with a blue positional color code for the same timestamp')
def picture_request_blue_color_code(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request a positional RGBA permutation that must not canonicalize to red."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_COLOR_CODE_BLUE,
    )
    context.second_filename = result['filename']


@then('the same picture URL with overlay configuration A is requested again')
def picture_request_a_again(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the same picture URL with overlay configuration A is requested again"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_A,
    )
    context.second_filename = r['filename']


@then('the two overlay-A picture responses reuse the same cached file')
def verify_pic_cache_hit(context: CachingTestContext) -> None:
    """the two overlay-A picture responses reuse the same cached file"""
    assert_with_detailed_failure(
        context.first_filename == context.second_filename,
        "Same overlay reuses cached picture",
        f"Same overlay reused cached picture: {context.first_filename}",
        f"Same overlay produced different files "
        f"(first={context.first_filename}, second={context.second_filename}). "
        "Picture config hash is non-deterministic.",
    )


@then('a picture URL is requested with no overlay')
def picture_request_no_overlay(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with no overlay"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=None,
    )
    context.first_filename = r['filename']


@then('a picture URL is requested with overlay configuration A for the same timestamp')
def picture_request_a_after_no_overlay(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with overlay configuration A for the same timestamp"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_A,
    )
    context.second_filename = r['filename']


@then('the no-overlay picture and overlay-A picture are distinct cached files')
def verify_no_overlay_vs_a(context: CachingTestContext) -> None:
    """the no-overlay picture and overlay-A picture are distinct cached files"""
    assert_with_detailed_failure(
        context.first_filename != context.second_filename,
        "Picture no-overlay vs overlay-A partitioning",
        f"no-overlay ({context.first_filename}) vs overlay-A "
        f"({context.second_filename}) distinct",
        f"Server reused the no-overlay cached picture ({context.first_filename}) for an "
        "overlay request. Empty-hash rows must not match a non-empty configHash.",
    )


@then('the same picture URL with no overlay is requested again')
def picture_request_no_overlay_again(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the same picture URL with no overlay is requested again"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=None,
    )
    context.second_filename = r['filename']


@then('the two no-overlay picture responses reuse the same cached file')
def verify_no_overlay_cache_hit(context: CachingTestContext) -> None:
    """the two no-overlay picture responses reuse the same cached file"""
    assert_with_detailed_failure(
        context.first_filename == context.second_filename,
        "No-overlay picture reuses cached file",
        f"No-overlay request reused cached picture: {context.first_filename}",
        f"No-overlay request produced different files "
        f"(first={context.first_filename}, second={context.second_filename}). "
        "Legacy empty-hash caching path is broken.",
    )


# ---------------------------------------------------------------------------
# Per-field permutations
# ---------------------------------------------------------------------------

def _apply_picture_variant(field: str, variant_str: str) -> Dict[str, Any]:
    """Like _apply_variant in the video test, but operates on the picture
    overlay (no outer "overlay" key wrapper)."""
    cfg = copy.deepcopy(BASELINE_PICTURE_OVERLAY)
    target = cfg
    parts = field.split(".")
    for piece in parts[:-1]:
        target = target.setdefault(piece, {})
    leaf = parts[-1]

    value: Any = variant_str
    raw = variant_str.strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                value = raw
    target[leaf] = value
    return cfg


@then('a picture URL is requested with the baseline picture overlay')
def picture_request_baseline(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with the baseline picture overlay"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=BASELINE_PICTURE_OVERLAY,
    )
    context.baseline_filename = r['filename']


@then(parsers.parse(
    'a picture URL is requested with the baseline overlay but {field} = {variant}'
))
def picture_request_variant(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any], field: str, variant: str) -> None:
    """Request a picture URL with the baseline overlay mutated on one field
    (parsed from the Examples table). Stores the returned filename and
    config on `context` for the downstream verify steps.
    """
    expiry = test_params.get('expiry_minutes', 5)
    variant_cfg = _apply_picture_variant(field, variant)
    logger.info("Picture variant config for field=%s value=%s -> %s",
                field, variant, json.dumps(variant_cfg))
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=variant_cfg,
    )
    context.variant_filename = r['filename']
    context.variant_config = variant_cfg


@then('the picture variant response is a different cached file from the baseline')
def verify_pic_variant_distinct(context: CachingTestContext) -> None:
    """the picture variant response is a different cached file from the baseline"""
    assert_with_detailed_failure(
        context.baseline_filename != context.variant_filename,
        "Picture per-field cache partitioning",
        f"baseline={context.baseline_filename} variant={context.variant_filename} (distinct)",
        f"Picture variant produced the SAME cached file as baseline "
        f"({context.baseline_filename}). The varied field is not contributing to the "
        "picture configuration hash.",
    )


@then('the picture variant cache is reused on a repeat request')
def verify_pic_variant_self_cache(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the picture variant cache is reused on a repeat request"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=context.variant_config,
    )
    assert_with_detailed_failure(
        r['filename'] == context.variant_filename,
        "Picture variant config caches on repeat",
        f"Picture variant repeat hit cache: {r['filename']}",
        f"Picture variant repeat returned a different file "
        f"(first={context.variant_filename}, repeat={r['filename']}). "
        "Picture hash is non-deterministic for this field.",
    )


# ---------------------------------------------------------------------------
# Cross-axis: resize hints
# ---------------------------------------------------------------------------

@then(parsers.parse('a picture URL is requested with overlay configuration A and width {width:d}'))
def picture_request_a_width(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any], width: int) -> None:
    """a picture URL is requested with overlay configuration A and width {width:d}"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_A,
        width=width,
    )
    if not hasattr(context, "first_filename"):
        context.first_filename = r['filename']
    else:
        context.second_filename = r['filename']


@then(parsers.parse('a picture URL is requested with overlay configuration A and height {height:d}'))
def picture_request_a_height(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any], height: int) -> None:
    """a picture URL is requested with overlay configuration A and height {height:d}"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=PICTURE_OVERLAY_A,
        height=height,
    )
    if not hasattr(context, "first_filename"):
        context.first_filename = r['filename']
    else:
        context.second_filename = r['filename']


@then('the two width-varied picture responses are distinct cached files')
def verify_width_partition(context: CachingTestContext) -> None:
    """the two width-varied picture responses are distinct cached files"""
    assert_with_detailed_failure(
        context.first_filename != context.second_filename,
        "Picture width partitioning",
        f"Different widths produced different cached files "
        f"({context.first_filename} vs {context.second_filename})",
        f"Same width-keyed cache miss expected; got same file {context.first_filename}.",
    )


@then('the two height-varied picture responses are distinct cached files')
def verify_height_partition(context: CachingTestContext) -> None:
    """the two height-varied picture responses are distinct cached files"""
    assert_with_detailed_failure(
        context.first_filename != context.second_filename,
        "Picture height partitioning",
        f"Different heights produced different cached files "
        f"({context.first_filename} vs {context.second_filename})",
        f"Same height-keyed cache miss expected; got same file {context.first_filename}.",
    )


# ---------------------------------------------------------------------------
# Top-level debug query param: distinct from overlay.debug inside the JSON.
# The picture flow's computePictureConfigHash reads `?debug=` directly from
# the query string (see vst_common.cpp computePictureConfigHash) so it must
# contribute to the cache key independently of the overlay JSON.
# ---------------------------------------------------------------------------

@then('a picture URL is requested with the baseline overlay and top-level debug=true')
def picture_request_top_level_debug(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with the baseline overlay and top-level debug=true"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=BASELINE_PICTURE_OVERLAY,
        debug=True,
    )
    context.variant_filename = r['filename']
    # Save the exact request shape so the cache-reuse check below can replay
    # it bit-for-bit (overlay + top-level debug=true together).
    context.variant_overlay = BASELINE_PICTURE_OVERLAY
    context.variant_debug = True


@then('the top-level-debug picture response is a different cached file from the baseline')
def verify_top_level_debug_distinct(context: CachingTestContext) -> None:
    """the top-level-debug picture response is a different cached file from the baseline"""
    assert_with_detailed_failure(
        context.baseline_filename != context.variant_filename,
        "Picture top-level debug param partitioning",
        f"baseline={context.baseline_filename} variant={context.variant_filename} (distinct)",
        f"Top-level ?debug=true did not produce a distinct cached file from baseline "
        f"({context.baseline_filename}). The debug query param must be picked up by "
        "computePictureConfigHash and included in the hash root - check that "
        "CivetServer::getParam(queryString, \"debug\", debugStr) is wired in.",
    )


@then('the top-level-debug picture cache is reused on a repeat request')
def verify_top_level_debug_self_cache(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the top-level-debug picture cache is reused on a repeat request"""
    expiry = test_params.get('expiry_minutes', 5)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=context.variant_overlay,
        debug=context.variant_debug,
    )
    assert_with_detailed_failure(
        r['filename'] == context.variant_filename,
        "Top-level debug variant caches on repeat",
        f"Top-level debug variant repeat hit cache: {r['filename']}",
        f"Top-level debug variant repeat returned a different file "
        f"(first={context.variant_filename}, repeat={r['filename']}). "
        "Hash is non-deterministic when top-level debug is set.",
    )


# ---------------------------------------------------------------------------
# Negative scenarios
# ---------------------------------------------------------------------------

@then('a picture URL is requested with the baseline overlay but bbox.objectId reordered')
def picture_request_reordered_ids(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with the baseline overlay but bbox.objectId reordered"""
    expiry = test_params.get('expiry_minutes', 5)
    cfg = copy.deepcopy(BASELINE_PICTURE_OVERLAY)
    cfg["bbox"]["objectId"] = list(reversed(BASELINE_PICTURE_OVERLAY["bbox"]["objectId"]))
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay=cfg,
    )
    context.variant_filename = r['filename']


@then('the reordered picture response reuses the baseline cached file')
def verify_pic_reordered_cache_hit(context: CachingTestContext) -> None:
    """the reordered picture response reuses the baseline cached file"""
    assert_with_detailed_failure(
        context.baseline_filename == context.variant_filename,
        "Picture objectId order independence",
        f"Reordered objectId reused cached picture: {context.baseline_filename}",
        f"Reordered objectId produced a different picture "
        f"(baseline={context.baseline_filename}, reordered={context.variant_filename}). "
        "computePictureConfigHash() must sort overlay ID lists before hashing.",
    )


@then('a picture URL is requested with whitespace-padded baseline overlay JSON')
def picture_request_whitespace_padded(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a picture URL is requested with whitespace-padded baseline overlay JSON"""
    expiry = test_params.get('expiry_minutes', 5)
    raw = json.dumps(BASELINE_PICTURE_OVERLAY, indent=2, sort_keys=True)
    r = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
        overlay_raw_json=raw,
    )
    context.variant_filename = r['filename']


@then('the whitespace-padded picture response reuses the baseline cached file')
def verify_pic_whitespace_cache_hit(context: CachingTestContext) -> None:
    """the whitespace-padded picture response reuses the baseline cached file"""
    assert_with_detailed_failure(
        context.baseline_filename == context.variant_filename,
        "Picture JSON whitespace independence",
        f"Whitespace-padded JSON reused cached picture: {context.baseline_filename}",
        f"Whitespace produced a different picture "
        f"(baseline={context.baseline_filename}, padded={context.variant_filename}).",
    )


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

@then('six distinct picture overlay configurations are each requested')
def picture_request_matrix(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """six distinct picture overlay configurations are each requested"""
    expiry = test_params.get('expiry_minutes', 5)
    configs = _matrix_configs()
    filenames: List[str] = []
    for idx, cfg in enumerate(configs):
        r = _request_picture_url(
            api_config, test_endpoints, test_params,
            context.selected_stream_id, context.selected_timestamp, expiry,
            overlay=cfg,
        )
        filenames.append(r['filename'])
        logger.info("Picture matrix [%d]: cfg=%s file=%s", idx, json.dumps(cfg), r['filename'])
    context.matrix_configs = configs
    context.matrix_filenames = filenames


@then('all six picture responses return mutually-distinct cached files')
def verify_pic_matrix_distinct(context: CachingTestContext) -> None:
    """all six picture responses return mutually-distinct cached files"""
    filenames = context.matrix_filenames
    duplicates = [
        (i, j, filenames[i])
        for i in range(len(filenames))
        for j in range(i + 1, len(filenames))
        if filenames[i] == filenames[j]
    ]
    assert_with_detailed_failure(
        not duplicates,
        "Six picture configs produce six distinct files",
        f"All {len(filenames)} files distinct: {filenames}",
        f"Found {len(duplicates)} collision(s): {duplicates}.",
    )


@then('each of the six picture configurations reuses its cached file on a repeat request')
def verify_pic_matrix_repeat(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """each of the six picture configurations reuses its cached file on a repeat request"""
    expiry = test_params.get('expiry_minutes', 5)
    mismatches: List[Dict[str, Any]] = []
    for idx, (cfg, expected_filename) in enumerate(
        zip(context.matrix_configs, context.matrix_filenames, strict=True)
    ):
        r = _request_picture_url(
            api_config, test_endpoints, test_params,
            context.selected_stream_id, context.selected_timestamp, expiry,
            overlay=cfg,
        )
        if r['filename'] != expected_filename:
            mismatches.append({
                'index': idx,
                'expected': expected_filename,
                'actual': r['filename'],
                'config': cfg,
            })
    assert_with_detailed_failure(
        not mismatches,
        "Each picture matrix config caches on repeat",
        f"All {len(context.matrix_configs)} configs hit cache on repeat",
        f"{len(mismatches)} config(s) did NOT cache on repeat",
        failed_items=mismatches,
    )
