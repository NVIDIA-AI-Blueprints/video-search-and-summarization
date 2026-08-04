# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""
BDD test for overlay-aware video URL cache partitioning.

The /url endpoint must include a hash of caller-supplied configuration in
its cache key so that:
  - Distinct configurations produce distinct cached files.
  - Identical configurations hit the cache.
  - Non-meaningful differences (key order, list order, JSON whitespace) do
    NOT fragment the cache.

This file drives all scenarios in overlay_cache_partition.feature. The
matrix scenarios exercise color, thickness, opacity, debug, pose, bbox
showAll/showObjId/objectId/classType/objIdPosition/objIdTextColor/
objIdTextBGColor, plus the disableAudio cross-axis.
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
    envoy_streamid_route_key,
    extract_filename_from_url,
    select_full_file_time_range,
    select_video_time_range,
)

logger = logging.getLogger(__name__)

scenarios('../../features/url_optimization/overlay_cache_partition.feature')


# ---------------------------------------------------------------------------
# Canonical baseline + named configs
# ---------------------------------------------------------------------------

# Baseline used by per-field permutation scenarios. Picked so every overlay
# field that contributes to the C++ hash has an explicit, non-default value.
BASELINE_OVERLAY: Dict[str, Any] = {
    "overlay": {
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
        "pose": False,
    }
}

# Configs A and B for the headline scenarios. Kept simple so the cache
# partitioning is unambiguous when only the bbox.objectId differs.
OVERLAY_CONFIG_A: Dict[str, Any] = {
    "overlay": {
        "bbox": {
            "showAll": False,
            "showObjId": True,
            "objectId": ["1001", "1002"],
        },
        "color": "red",
        "thickness": 5,
        "opacity": 254,
        "debug": False,
    }
}

OVERLAY_CONFIG_B: Dict[str, Any] = {
    "overlay": {
        "bbox": {
            "showAll": False,
            "showObjId": True,
            "objectId": ["2001", "2002"],
        },
        "color": "blue",
        "thickness": 3,
        "opacity": 200,
        "debug": False,
    }
}


def _matrix_configs() -> List[Dict[str, Any]]:
    """Ten mutually-distinct overlay configurations covering every hashed field.

    Each config differs from every other on at least one field that the
    server hashes. We construct them by starting from the baseline and
    nudging a different field in each variant.
    """
    out: List[Dict[str, Any]] = []
    out.append(copy.deepcopy(BASELINE_OVERLAY))
    out.append(_clone_with(BASELINE_OVERLAY, color="green"))
    out.append(_clone_with(BASELINE_OVERLAY, thickness=8))
    out.append(_clone_with(BASELINE_OVERLAY, opacity=64))
    out.append(_clone_with(BASELINE_OVERLAY, debug=True))
    out.append(_clone_with(BASELINE_OVERLAY, pose=True))
    out.append(_clone_with(BASELINE_OVERLAY, bbox={"showAll": True}))
    out.append(_clone_with(BASELINE_OVERLAY, bbox={"showObjId": False}))
    out.append(_clone_with(BASELINE_OVERLAY, bbox={"objectId": ["9999"]}))
    out.append(_clone_with(BASELINE_OVERLAY, bbox={"classType": ["Person"]}))
    return out


def _clone_with(base: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """Deep-copy `base` and override either top-level `overlay` keys or
    nested `bbox` keys. Pass `bbox={...}` to merge into the bbox dict.
    """
    cfg = copy.deepcopy(base)
    bbox_overrides = overrides.pop("bbox", None)
    for key, value in overrides.items():
        cfg["overlay"][key] = value
    if bbox_overrides:
        cfg["overlay"].setdefault("bbox", {}).update(bbox_overrides)
    return cfg


DEBUG_FONT_CONFIG_SMALL = _clone_with(BASELINE_OVERLAY, debug=True, debugFontSize=8)
DEBUG_FONT_CONFIG_LARGE = _clone_with(BASELINE_OVERLAY, debug=True, debugFontSize=24)
PROXIMITY_CONFIG_LOW = _clone_with(BASELINE_OVERLAY, proximityAreaFactor=1.0001)
PROXIMITY_CONFIG_HIGH = _clone_with(BASELINE_OVERLAY, proximityAreaFactor=1.0004)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _encode_configuration(cfg: Optional[Dict[str, Any]], *,
                          serialize: Optional[str] = None) -> str:
    """URL-encode a configuration dict. `serialize` lets a caller inject a
    pre-built JSON string (used by the whitespace/key-reorder scenarios)."""
    if serialize is not None:
        return quote(serialize, safe='')
    compact = json.dumps(cfg, separators=(',', ':'), sort_keys=True)
    return quote(compact, safe='')


def _request_video_url(api_config: dict,
                       test_endpoints: dict,
                       test_params: dict,
                       stream_id: str,
                       start_time: str,
                       end_time: str,
                       expiry_minutes: int,
                       *,
                       configuration: Optional[Dict[str, Any]] = None,
                       configuration_raw_json: Optional[str] = None,
                       disable_audio: bool = False,
                       container: str = "mp4") -> Dict[str, Any]:
    """Issue a blocking video URL request.

    `configuration_raw_json` overrides JSON serialization for whitespace /
    key-order scenarios. When `configuration` and `configuration_raw_json`
    are both None, the configuration query parameter is omitted entirely.
    """
    base = f"{api_config['base_url']}{test_endpoints['video_url'].format(stream_id=stream_id)}"
    parts = [
        f"startTime={quote(start_time, safe='')}",
        f"endTime={quote(end_time, safe='')}",
        f"expiryMinutes={expiry_minutes}",
        "blocking=true",
        f"container={container}",
        f"disableAudio={'true' if disable_audio else 'false'}",
    ]
    if configuration is not None or configuration_raw_json is not None:
        parts.append(
            f"configuration={_encode_configuration(configuration, serialize=configuration_raw_json)}"
        )
    url = f"{base}?{'&'.join(parts)}"
    headers = {"streamid": envoy_streamid_route_key(stream_id)}

    start = time.monotonic()
    resp = requests.get(
        url,
        headers=headers,
        timeout=test_params.get('url_request_timeout', 300),
        verify=api_config.get('verify_ssl', False),
    )
    elapsed = time.monotonic() - start

    if resp.status_code != 200:
        pytest.fail(
            f"Video URL request failed: status={resp.status_code} "
            f"body={resp.text[:300]} url={url}"
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        pytest.fail(f"Video URL response is not JSON: {exc}. Body: {resp.text[:200]}")
    if not data.get('videoUrl'):
        pytest.fail(f"Video URL response missing videoUrl. Body: {data}")

    filename = extract_filename_from_url(data['videoUrl'])
    logger.info("video URL: file=%s elapsed=%.1fms", filename, elapsed * 1000)
    return {'data': data, 'elapsed': elapsed, 'filename': filename, 'url': url}


# ---------------------------------------------------------------------------
# Background steps (Given/When in feature file)
# ---------------------------------------------------------------------------

@given('the VST API is configured for overlay cache partition test')
def vst_api_configured(api_config: Dict[str, Any], test_endpoints: Dict[str, str]) -> None:
    """the VST API is configured for overlay cache partition test"""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['video_url'], "Video URL endpoint must be configured"


@when('the list of available replay streams is fetched for overlay test')
def fetch_streams(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the list of available replay streams is fetched for overlay test"""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    resp = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    resp.raise_for_status()
    context.streams = resp.json()
    assert context.streams, "No replay streams found"


@when('the recording timelines are fetched for overlay test')
def fetch_timelines(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the recording timelines are fetched for overlay test"""
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


@when('a valid video time range is selected for overlay test')
def select_video_range(context: CachingTestContext, test_params: Dict[str, Any]) -> None:
    """a valid video time range is selected for overlay test"""
    duration = test_params.get('video_duration_seconds', 5)
    selection = select_video_time_range(context.streams, context.timelines, duration)
    assert selection is not None, "No suitable timeline for overlay cache partition test"
    context.selected_stream_id = selection['stream_id']
    context.selected_start_time = selection['start_time']
    context.selected_end_time = selection['end_time']
    logger.info("Selected stream=%s start=%s end=%s",
                selection['stream_id'], selection['start_time'], selection['end_time'])


# ---------------------------------------------------------------------------
# Headline scenarios: A/B distinction, repeat = cache hit, no-config vs A
# ---------------------------------------------------------------------------

@then('a blocking video URL is requested with overlay configuration A')
def request_with_config_a(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with overlay configuration A"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_A,
    )
    context.response_config_a_first = result['data']
    context.first_filename = result['filename']


@then('a blocking video URL is requested with overlay configuration B for the same time range')
def request_with_config_b(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with overlay configuration B for the same time range"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_B,
    )
    context.response_config_b = result['data']
    context.second_filename = result['filename']


@then('the two responses return distinct cached files')
def verify_distinct_files(context: CachingTestContext) -> None:
    """the two responses return distinct cached files"""
    first = context.first_filename
    second = context.second_filename
    assert_with_detailed_failure(
        first != second,
        "Overlay-aware cache partitioning",
        f"Distinct configs produced distinct files (A={first}, B={second})",
        f"Server returned the SAME file ({first}) for two different overlay configs. "
        "Regression of nvbugs/6222683 - findTempFileByStreamAndTime must filter by config_hash.",
    )


@then('a blocking video URL is requested with debug font size 8')
def request_with_small_debug_font(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request a debug overlay whose font size must participate in the cache key."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
        configuration=DEBUG_FONT_CONFIG_SMALL,
    )
    context.first_filename = result['filename']


@then('a blocking video URL is requested with debug font size 24 for the same time range')
def request_with_large_debug_font(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request the same range with a different rendered debug font size."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
        configuration=DEBUG_FONT_CONFIG_LARGE,
    )
    context.second_filename = result['filename']


@then('a blocking video URL is requested with proximity area factor 1.0001')
def request_with_low_proximity_factor(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request a factor that previously collided after three-decimal rounding."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
        configuration=PROXIMITY_CONFIG_LOW,
    )
    context.first_filename = result['filename']


@then('a blocking video URL is requested with proximity area factor 1.0004 for the same time range')
def request_with_high_proximity_factor(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Request a distinct factor that rounded to the same legacy cache value."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
        configuration=PROXIMITY_CONFIG_HIGH,
    )
    context.second_filename = result['filename']


@then('the same blocking video URL with overlay configuration A is requested again')
def request_with_config_a_again(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the same blocking video URL with overlay configuration A is requested again"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_A,
    )
    context.response_config_a_second = result['data']
    context.second_filename = result['filename']


@then('the two overlay-A responses reuse the same cached file')
def verify_same_config_cache_hit(context: CachingTestContext) -> None:
    """the two overlay-A responses reuse the same cached file"""
    assert_with_detailed_failure(
        context.first_filename == context.second_filename,
        "Same overlay config reuses cached file",
        f"Same overlay config reused cached file: {context.first_filename}",
        f"Same overlay config produced different files "
        f"(first={context.first_filename}, second={context.second_filename}). "
        "Hash is non-deterministic - check sort order of overlay lists / map keys.",
    )


@then('a blocking video URL is requested with no configuration')
def request_with_no_config(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with no configuration"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=None,
    )
    context.response_no_config = result['data']
    context.first_filename = result['filename']


@then('a blocking video URL is requested with overlay configuration A for the same time range')
def request_with_config_a_after_no_config(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with overlay configuration A for the same time range"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_A,
    )
    context.response_config_a_first = result['data']
    context.second_filename = result['filename']


@then('the no-config response and the overlay-A response are distinct cached files')
def verify_no_config_vs_overlay_a(context: CachingTestContext) -> None:
    """the no-config response and the overlay-A response are distinct cached files"""
    assert_with_detailed_failure(
        context.first_filename != context.second_filename,
        "No-config vs overlay-A partitioning",
        f"No-config ({context.first_filename}) and overlay-A ({context.second_filename}) "
        "produced different files",
        f"Server reused the no-config cached file ({context.first_filename}) for an "
        "overlay request. Empty-hash rows must not match a non-empty configHash.",
    )


# ---------------------------------------------------------------------------
# Per-field permutations: vary one field at a time on top of the baseline
# ---------------------------------------------------------------------------

def _apply_variant(field: str, variant_str: str) -> Dict[str, Any]:
    """Build a variant of BASELINE_OVERLAY with `field` set to `variant_str`.

    `field` is a dotted path: `color`, `bbox.objectId`, `bbox.objIdPosition`,
    etc. `variant_str` is the Examples-table value, parsed from a Gherkin
    cell - we coerce it to int/bool/list as appropriate.
    """
    cfg = copy.deepcopy(BASELINE_OVERLAY)
    target = cfg["overlay"]
    parts = field.split(".")
    for piece in parts[:-1]:
        target = target.setdefault(piece, {})
    leaf = parts[-1]

    value: Any = variant_str
    raw = variant_str.strip()
    # Parse the Examples-table value: JSON literal first, fall back to str.
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                value = raw  # plain string

    target[leaf] = value
    return cfg


@then('a blocking video URL is requested with the baseline overlay configuration')
def request_baseline(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with the baseline overlay configuration"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=BASELINE_OVERLAY,
    )
    context.baseline_filename = result['filename']


@then(parsers.parse(
    'a blocking video URL is requested with the baseline configuration but {field} = {variant}'
))
def request_variant(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any], field: str, variant: str) -> None:
    """Request a blocking video URL with the baseline overlay mutated on
    one field (parsed from the Examples table). Stores the returned
    filename and config on `context` for the downstream verify steps.
    """
    expiry = test_params.get('expiry_minutes', 5)
    variant_cfg = _apply_variant(field, variant)
    logger.info("Variant config for field=%s value=%s -> %s",
                field, variant, json.dumps(variant_cfg))
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=variant_cfg,
    )
    context.variant_filename = result['filename']
    context.variant_config = variant_cfg


@then('the variant response is a different cached file from the baseline')
def verify_variant_distinct(context: CachingTestContext) -> None:
    """the variant response is a different cached file from the baseline"""
    assert_with_detailed_failure(
        context.baseline_filename != context.variant_filename,
        "Per-field cache partitioning",
        f"baseline={context.baseline_filename} variant={context.variant_filename} (distinct)",
        f"Variant config produced the SAME cached file as baseline "
        f"({context.baseline_filename}). The varied field is not contributing to the "
        "configuration hash - check computeConfigHash() in storage_management_apis.cpp.",
    )


@then('the variant cache is reused on a repeat request')
def verify_variant_self_cache(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """the variant cache is reused on a repeat request"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=context.variant_config,
    )
    assert_with_detailed_failure(
        result['filename'] == context.variant_filename,
        "Variant config caches on repeat",
        f"Variant repeat hit cache: {result['filename']}",
        f"Variant repeat returned a different file "
        f"(first={context.variant_filename}, repeat={result['filename']}). "
        "Hash is non-deterministic for this field.",
    )


# ---------------------------------------------------------------------------
# Negative scenarios: differences that must NOT fragment the cache
# ---------------------------------------------------------------------------

@then('a blocking video URL is requested with the baseline configuration but bbox.objectId reordered')
def request_reordered_objectid(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with the baseline configuration but bbox.objectId reordered"""
    expiry = test_params.get('expiry_minutes', 5)
    cfg = copy.deepcopy(BASELINE_OVERLAY)
    cfg["overlay"]["bbox"]["objectId"] = list(reversed(BASELINE_OVERLAY["overlay"]["bbox"]["objectId"]))
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=cfg,
    )
    context.variant_filename = result['filename']


@then('the reordered response reuses the baseline cached file')
def verify_reordered_cache_hit(context: CachingTestContext) -> None:
    """the reordered response reuses the baseline cached file"""
    assert_with_detailed_failure(
        context.baseline_filename == context.variant_filename,
        "objectId order independence",
        f"Reordered objectId reused cached file: {context.baseline_filename}",
        f"Reordered objectId produced a different file "
        f"(baseline={context.baseline_filename}, reordered={context.variant_filename}). "
        "computeConfigHash() must sort overlay ID lists before hashing.",
    )


@then('a blocking video URL is requested with the baseline overlay configuration with keys reordered')
def request_reordered_keys(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """Hand-build a JSON string with the same data but a deliberately
    different key order to confirm the C++ hash is order-independent at
    the JSON level too (we serialize with sort_keys=True on the way in)."""
    expiry = test_params.get('expiry_minutes', 5)
    # Hand-craft a JSON string with unsorted keys - the cache key is
    # computed server-side from the *parsed* values, so this should still
    # land on the same hash even though the bytes on the wire differ.
    raw = (
        '{"overlay":{'
        '"opacity":254,'
        '"thickness":5,'
        '"pose":false,'
        '"debug":false,'
        '"color":"red",'
        '"bbox":{'
        '"objIdTextColor":"white",'
        '"objIdPosition":0,'
        '"objIdTextBGColor":"red",'
        '"classType":["Vehicle"],'
        '"objectId":["1001","1002"],'
        '"showObjId":true,'
        '"showAll":false'
        '}'
        '}}'
    )
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration_raw_json=raw,
    )
    context.variant_filename = result['filename']


@then('the key-reordered response reuses the baseline cached file')
def verify_reordered_keys_cache_hit(context: CachingTestContext) -> None:
    """the key-reordered response reuses the baseline cached file"""
    assert_with_detailed_failure(
        context.baseline_filename == context.variant_filename,
        "JSON key order independence",
        f"Key-reordered JSON reused cached file: {context.baseline_filename}",
        f"Key reordering produced a different file "
        f"(baseline={context.baseline_filename}, reordered={context.variant_filename}). "
        "Server hash should be based on the parsed value tree, not the raw bytes.",
    )


@then('a blocking video URL is requested with whitespace-padded JSON of the baseline configuration')
def request_whitespace_padded(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with whitespace-padded JSON of the baseline configuration"""
    expiry = test_params.get('expiry_minutes', 5)
    # Pretty-printed with newlines and spaces - same semantic content.
    raw = json.dumps(BASELINE_OVERLAY, indent=2, sort_keys=True)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration_raw_json=raw,
    )
    context.variant_filename = result['filename']


@then('the whitespace-padded response reuses the baseline cached file')
def verify_whitespace_cache_hit(context: CachingTestContext) -> None:
    """the whitespace-padded response reuses the baseline cached file"""
    assert_with_detailed_failure(
        context.baseline_filename == context.variant_filename,
        "JSON whitespace independence",
        f"Whitespace-padded JSON reused cached file: {context.baseline_filename}",
        f"Whitespace produced a different file "
        f"(baseline={context.baseline_filename}, padded={context.variant_filename}). "
        "Hash must be based on parsed JSON, not raw bytes.",
    )


# ---------------------------------------------------------------------------
# Matrix: 10 distinct configurations - all distinct, each caches on repeat
# ---------------------------------------------------------------------------

@then('ten distinct overlay configurations are each requested as blocking video URLs')
def request_matrix(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """ten distinct overlay configurations are each requested as blocking video URLs"""
    expiry = test_params.get('expiry_minutes', 5)
    configs = _matrix_configs()
    filenames: List[str] = []
    for idx, cfg in enumerate(configs):
        result = _request_video_url(
            api_config, test_endpoints, test_params,
            context.selected_stream_id,
            context.selected_start_time,
            context.selected_end_time,
            expiry,
            configuration=cfg,
        )
        filenames.append(result['filename'])
        logger.info("Matrix [%d]: cfg=%s file=%s", idx, json.dumps(cfg), result['filename'])
    context.matrix_configs = configs
    context.matrix_filenames = filenames


@then('all ten responses return mutually-distinct cached files')
def verify_matrix_distinct(context: CachingTestContext) -> None:
    """all ten responses return mutually-distinct cached files"""
    filenames = context.matrix_filenames
    duplicates = [
        (i, j, filenames[i])
        for i in range(len(filenames))
        for j in range(i + 1, len(filenames))
        if filenames[i] == filenames[j]
    ]
    assert_with_detailed_failure(
        not duplicates,
        "Matrix of 10 configs produces 10 distinct files",
        f"All {len(filenames)} files distinct: {filenames}",
        f"Found {len(duplicates)} collision(s): {duplicates}. "
        "Configurations colliding on the same cached file means computeConfigHash() is "
        "missing one of the varied fields.",
    )


@then('each of the ten configurations reuses its cached file on a repeat request')
def verify_matrix_repeat_cache(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """each of the ten configurations reuses its cached file on a repeat request"""
    expiry = test_params.get('expiry_minutes', 5)
    mismatches: List[Dict[str, Any]] = []
    for idx, (cfg, expected_filename) in enumerate(
        zip(context.matrix_configs, context.matrix_filenames, strict=True)
    ):
        result = _request_video_url(
            api_config, test_endpoints, test_params,
            context.selected_stream_id,
            context.selected_start_time,
            context.selected_end_time,
            expiry,
            configuration=cfg,
        )
        if result['filename'] != expected_filename:
            mismatches.append({
                'index': idx,
                'expected': expected_filename,
                'actual': result['filename'],
                'config': cfg,
            })
    assert_with_detailed_failure(
        not mismatches,
        "Each matrix config caches on repeat",
        f"All {len(context.matrix_configs)} configs hit cache on repeat",
        f"{len(mismatches)} config(s) did NOT cache on repeat",
        failed_items=mismatches,
    )


# ---------------------------------------------------------------------------
# Cross-axis: disableAudio is hashed alongside the overlay block
# ---------------------------------------------------------------------------

@then('a blocking video URL is requested with overlay configuration A and disableAudio=false')
def request_audio_enabled(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with overlay configuration A and disableAudio=false"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_A,
        disable_audio=False,
    )
    context.first_filename = result['filename']
    context.first_video_url = result['data'].get('videoUrl', '')


@then('a blocking video URL is requested with overlay configuration A and disableAudio=true')
def request_audio_disabled(context: CachingTestContext, api_config: Dict[str, Any], test_endpoints: Dict[str, str], test_params: Dict[str, Any]) -> None:
    """a blocking video URL is requested with overlay configuration A and disableAudio=true"""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id,
        context.selected_start_time,
        context.selected_end_time,
        expiry,
        configuration=OVERLAY_CONFIG_A,
        disable_audio=True,
    )
    context.second_filename = result['filename']
    context.second_video_url = result['data'].get('videoUrl', '')


def _generated_video_has_audio_track(api_config: Dict[str, Any], video_url: str) -> Optional[bool]:
    """Stream the head of an MP4 and look for an audio-track marker. Returns
    True if an AAC audio atom ('mp4a') or audio media-type atom ('soun') is
    found, False if neither appears in the first ~512KB, None on fetch
    failure. Used to disambiguate a disableAudio filename collision: if the
    generated file has no audio, the server coerced disableAudio server-side
    (no-AAC source) and the collision is benign; if it HAS audio while the
    caller passed disableAudio=true, that is the regression we want to catch.
    """
    if not video_url:
        return None
    try:
        resp = requests.get(
            video_url, stream=True, timeout=30,
            verify=api_config.get('verify_ssl', False),
        )
        resp.raise_for_status()
        head = b''
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                break
            head += chunk
            if len(head) >= 512 * 1024:
                break
        resp.close()
        if not head:
            return None
        # 'mp4a' is the AAC codec atom in MP4; 'soun' is the audio media
        # type. The moov box lives near the start of a remuxed mp4 so a
        # 512KB head is sufficient. Conservative byte search beats pulling
        # in a media-parsing dependency.
        return b'mp4a' in head or b'soun' in head
    except Exception as exc:
        logger.warning("Audio-track probe failed for %s: %s", video_url, exc)
        return None


@then('the disableAudio variants return distinct cached files')
def verify_disable_audio_partition(context: CachingTestContext, api_config: Dict[str, Any]) -> None:
    """the disableAudio variants return distinct cached files"""
    if context.first_filename != context.second_filename:
        # Happy path - audio is partitioning correctly.
        return

    # Filenames collided. The reviewer's concern: an unconditional skip
    # would mask a real regression where disableAudio is missing from
    # the configHash. Distinguish by probing whether the GENERATED file
    # actually has an audio track:
    #   - No audio track -> the server coerced disableAudio=true server-
    #     side (source has no AAC / non-AAC audio in mp4 container, see
    #     storage_management_apis.cpp:1207-1211). Both requests legit-
    #     imately produced the same hash. Skip.
    #   - Has audio track -> the caller's disableAudio=true was ignored
    #     while the cached file still has audio, OR disableAudio is not
    #     contributing to the hash. Either way, that is a regression.
    has_audio = _generated_video_has_audio_track(api_config, context.first_video_url)

    if has_audio is False:
        logger.warning(
            "disableAudio variants collapsed onto %s and the generated file "
            "has no audio track - confirming server-side coercion of "
            "disableAudio (source likely lacks AAC). Skipping is correct.",
            context.first_filename,
        )
        pytest.skip(
            "Source stream lacks AAC audio - confirmed by probing the "
            "generated file (no 'mp4a'/'soun' atom in the first 512KB)"
        )
    elif has_audio is True:
        pytest.fail(
            f"disableAudio variants returned the same file "
            f"({context.first_filename}) AND that file contains an audio "
            "track. This indicates either (a) disableAudio is missing from "
            "the configHash so disableAudio=true reused the disableAudio="
            "false cache entry, or (b) the server is not honouring "
            "disableAudio=true in the encode path. Both are regressions."
        )
    else:
        pytest.fail(
            f"disableAudio variants returned the same file "
            f"({context.first_filename}) and the audio-track probe could "
            "not determine whether the generated file has audio "
            f"(url={context.first_video_url}). Failing conservatively "
            "rather than skip, since we cannot confirm server-side "
            "coercion. Inspect the file manually or re-run with a stream "
            "known to have AAC audio."
        )


# ---------------------------------------------------------------------------
# Exact file-bounds: the request boundaries would engage the full-file
# fast path if no transformations were asked for. With overlay set, the
# gate in tryFindFullFileMatch must fall through to the standard remux
# path so overlay variants partition the cache instead of collapsing
# onto the raw recording's symlink.
# ---------------------------------------------------------------------------

@then("the selected time range is replaced with the recording's exact file boundaries")
def select_exact_file_bounds(context: CachingTestContext) -> None:
    """the selected time range is replaced with the recording"""
    selection = select_full_file_time_range(context.streams, context.timelines)
    assert selection is not None, (
        "No suitable timeline found for exact-bounds test. The test stream "
        "needs at least one recording/timeline whose startTime and endTime "
        "we can request verbatim - that is the boundary the full-file fast "
        "path matches on."
    )
    context.selected_stream_id = selection['stream_id']
    context.selected_start_time = selection['start_time']
    context.selected_end_time = selection['end_time']
    logger.info(
        "Exact-bounds selection: stream=%s start=%s end=%s "
        "(this range matches the recording boundaries within one frame "
        "interval and would engage the full-file fast path if no overlay "
        "were requested)",
        selection['stream_id'], selection['start_time'], selection['end_time'],
    )
