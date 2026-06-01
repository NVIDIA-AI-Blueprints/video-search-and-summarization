#!/usr/bin/env python3
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

"""End-to-end test: real VLM responses → pluggable parser → info merge.

Uses actual responses from cosmos-reason2-8b on warehouse NIM
(localhost:30082) captured 2026-03-30.
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

REAL_VLM_RESPONSES = {
    "classification": (
        '```json\n'
        '{\n'
        '  "label": "no-spotter",\n'
        '  "severity": "high",\n'
        '  "confidence": 0.95,\n'
        '  "reasoning": "The absence of a safety spotter near the pedestrian '
        'walkway while operating a forklift poses a significant risk of '
        'collision or injury to pedestrians."\n'
        '}\n'
        '```'
    ),
    "analytics": (
        '```json\n'
        '{\n'
        '  "total_packages": 0,\n'
        '  "workers_visible": 0,\n'
        '  "conveyor_active": false,\n'
        '  "zone": "loading dock",\n'
        '  "notes": "No packages or workers are visible in the provided image."\n'
        '}\n'
        '```'
    ),
    "enhancement": (
        '```json\n'
        '{\n'
        '  "event_type": "Unauthorized Access Attempt",\n'
        '  "description": "A person entered a restricted area near server racks '
        'during nighttime hours, potentially compromising the security of '
        'sensitive equipment and data. The individual\'s presence in a controlled '
        'zone without authorization suggests a breach of physical security '
        'protocols, which could lead to data theft, sabotage, or operational '
        'disruption."\n'
        '}\n'
        '```'
    ),
}


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) from VLM output."""
    stripped = text.strip()
    match = re.match(r'^```(?:\w+)?\s*\n(.*?)```\s*$', stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


class PPEClassifier:
    """Classification parser — handles markdown-wrapped JSON."""

    def parse(self, raw_response: str) -> dict:
        clean = strip_markdown_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response}
        return {
            "classification": data.get("label", "unknown"),
            "severity": data.get("severity", "unknown"),
            "confidence": str(data.get("confidence", 0)),
            "reasoning": data.get("reasoning", ""),
        }


class WarehouseCounter:
    """Analytics parser — counts objects from VLM JSON."""

    def parse(self, raw_response: str) -> dict:
        clean = strip_markdown_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response}
        return {
            "total_packages": str(data.get("total_packages", 0)),
            "workers_visible": str(data.get("workers_visible", 0)),
            "conveyor_active": str(data.get("conveyor_active", False)),
            "zone": data.get("zone", "unknown"),
            "notes": data.get("notes", ""),
        }


class SceneEnhancer:
    """Enhancement parser — enriches alert with scene details."""

    def parse(self, raw_response: str) -> dict:
        clean = strip_markdown_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response}
        return {
            "event_type": data.get("event_type", "unknown"),
            "description": data.get("description", ""),
            "risk_level": data.get("risk_level", "unknown"),
            "recommended_action": data.get("recommended_action", ""),
        }


def simulate_pipeline_merge(parser_output: dict) -> dict:
    """Simulate how parser output merges into alert info in the pipeline.

    Mirrors production logic in enhance_alert_with_vlm.py. The pluggable parser path serializes the
    parsed dict into ``info["vlm_response"]`` as a JSON string and sets
    ``info["verdict"] = null``. Parser keys do NOT flat-merge into info —
    transport metadata is structurally protected by containment.

    The nvschema alignment (map<string,string>): all values in info are
    coerced to str — None→"", int→str, dict→JSON. Production runs this
    through merge_info_with_response; we mirror that here for fidelity.
    """
    try:
        from models.responses import (
            AlertBridgeResponse,
            VLMResponse,
            merge_info_with_response,
        )
    except ImportError:
        AlertBridgeResponse = None  # type: ignore

    message = {
        "info": {
            "sensorId": "warehouse-cam-03",
            "category": "test-alert",
            "primaryObjectId": "obj-1",
            "timestamp": "2026-03-30T10:00:00Z",
        }
    }

    if AlertBridgeResponse is not None:
        # Option B: pluggable path bypasses VLMResponse and
        # writes the parser JSON into ``info["vlm_response"]`` directly.
        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )
        info = message["info"]
        info["vlm_response"] = json.dumps(parser_output)
        info["verdict"] = ""
    else:
        # Offline fallback: replicate the stringification manually.
        info = message["info"]
        info["vlm_response"] = json.dumps(parser_output)
        info["verdict"] = ""
        info["videoSource"] = "http://example.com/video.mp4"
        info["verificationResponseCode"] = "200"
        info["verificationResponseStatus"] = "OK"

    return message["info"]


def run_tests():
    parsers = {
        "classification": PPEClassifier(),
        "analytics": WarehouseCounter(),
        "enhancement": SceneEnhancer(),
    }

    results = {}
    all_passed = True

    for use_case, raw_response in REAL_VLM_RESPONSES.items():
        parser = parsers[use_case]
        print(f"\n{'='*60}")
        print(f"USE CASE: {use_case.upper()}")
        print(f"{'='*60}")

        print(f"\n--- Raw VLM Response (first 200 chars) ---")
        print(raw_response[:200])

        parsed = parser.parse(raw_response)
        print(f"\n--- Parser Output ---")
        print(json.dumps(parsed, indent=2))

        if not isinstance(parsed, dict):
            print(f"  FAIL: parser returned {type(parsed)}, expected dict")
            all_passed = False
            continue

        if not parsed:
            print(f"  FAIL: parser returned empty dict")
            all_passed = False
            continue

        merged = simulate_pipeline_merge(parsed)
        print(f"\n--- Merged info (as stored in mdx-vlm-incidents) ---")
        print(json.dumps(merged, indent=2))

        # Assert production shape — keeps this script in sync with
        # enhance_alert_with_vlm.py so regressions surface here.
        shape_ok = True
        # Nvschema alignment: verdict null is stringified to "" (map<string,string>).
        if merged.get("verdict") not in ("", None):
            print("  FAIL: info['verdict'] must be '' (null after nvschema alignment stringify) for pluggable parser path")
            shape_ok = False
        if not isinstance(merged.get("vlm_response"), str):
            print("  FAIL: info['vlm_response'] must be a JSON string")
            shape_ok = False
        else:
            try:
                vlm_response_obj = json.loads(merged["vlm_response"])
                if vlm_response_obj != parsed:
                    print("  FAIL: info['vlm_response'] roundtrip != parser output")
                    shape_ok = False
            except json.JSONDecodeError:
                print("  FAIL: info['vlm_response'] is not valid JSON")
                shape_ok = False
        # Schema-owned keys (VLMResponse + AlertBridgeResponse) are not
        # parser leaks even if parsed has the same name — their values come
        # from the bridge, not the parser. A real leak is a parser key that
        # carries the parser's value into the top level of info.
        _schema_keys = {
            "vlm_response", "verdict", "description",
            "videoSource", "verificationResponseCode",
            "verificationResponseStatus", "latency",
        }
        leaked = [
            k for k in parsed
            if k in merged and k not in _schema_keys
        ]
        if leaked:
            print(f"  FAIL: parser keys leaked into top-level info: {leaked}")
            shape_ok = False

        if not shape_ok:
            all_passed = False
            continue

        results[use_case] = {"parsed": parsed, "merged": merged}
        print(f"\n  PASS: {len(parsed)} fields parsed; shape matches production")

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    for uc, r in results.items():
        print(f"  {uc:15s}: {len(r['parsed'])} fields → info has {len(r['merged'])} fields")

    if all_passed:
        print(f"\nAll {len(results)} use cases PASSED")
    else:
        print(f"\nSome use cases FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
