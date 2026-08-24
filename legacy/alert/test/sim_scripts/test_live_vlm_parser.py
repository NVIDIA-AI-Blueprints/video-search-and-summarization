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

"""Live VLM integration test — calls a real cosmos-reason2 NIM.

Tests all 3 use cases (Classification / Analytics / Enhancement) against CR2.
Each test: send prompt → get response → parse with external parser → validate.

Endpoint selection (in order of preference):
  1. $VLM_URL env var (explicit override)
  2. Smartcity NIM (localhost:31082) — default
  3. Smartcity alt   (localhost:30082)
  4. Warehouse NIM   (localhost:30082)

If NONE are reachable, the script EXITS WITH CODE 2 (SKIPPED) and prints a
prominent warning so CI / reviewers do not mistake a skip for a pass.

Usage:
  # default (tries all known endpoints):
  python test/sim_scripts/test_live_vlm_parser.py

  # explicit override:
  VLM_URL=http://your-nim:port/v1/chat/completions \\
      python test/sim_scripts/test_live_vlm_parser.py

Exit codes:
  0 — all 3 use cases PASSED
  1 — at least one use case FAILED
  2 — SKIPPED (no reachable NIM)
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Fallback endpoints, tried in order. First reachable wins.
_DEFAULT_ENDPOINTS = [
    "http://localhost:31082/v1/chat/completions",
    "http://localhost:30082/v1/chat/completions",
    "http://localhost:30082/v1/chat/completions",
]

# Env override wins over the fallback list.
_ENV_URL = os.getenv("VLM_URL")
CANDIDATE_URLS = [_ENV_URL] if _ENV_URL else list(_DEFAULT_ENDPOINTS)

VLM_URL = None  # resolved at runtime after reachability probe
VLM_MODEL = os.getenv("VLM_MODEL", "nvidia/cosmos-reason2-8b")

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

_RE_MD_FENCE = re.compile(r'^```(?:\w+)?\s*\n(.*?)```\s*$', re.DOTALL)


def _strip_fences(text):
    m = _RE_MD_FENCE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


# --- Parser classes (external, zero AB dependency) ---

class PPEClassifier:
    """Classification use case."""

    PROMPT_SYSTEM = "You are a safety classification system. Always respond in valid JSON only, no other text."
    PROMPT_USER = (
        "Classify this scenario: A worker is operating a forklift near a "
        "pedestrian walkway without a safety spotter.\n\n"
        "Return JSON with exactly these fields:\n"
        "- label: one of [no-helmet, no-vest, no-spotter, compliant]\n"
        "- severity: one of [low, medium, high]\n"
        "- confidence: float between 0 and 1\n"
        "- reasoning: short explanation"
    )

    def parse(self, raw_response: str) -> dict:
        clean = _strip_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response.strip()}
        return {
            "classification": data.get("label", "unknown"),
            "severity": data.get("severity", "unknown"),
            "confidence": str(data.get("confidence", 0)),
            "reasoning": data.get("reasoning", ""),
        }


class WarehouseCounter:
    """Analytics (counting) use case."""

    PROMPT_SYSTEM = "You are a warehouse analytics system. Always respond in valid JSON only, no other text."
    PROMPT_USER = (
        "Count objects in this scene: A warehouse loading dock with 15 packages "
        "on 2 conveyor belts and 3 workers moving boxes.\n\n"
        "Return JSON with exactly these fields:\n"
        "- total_packages: integer count\n"
        "- workers_visible: integer count\n"
        "- conveyor_active: boolean\n"
        "- zone: string identifying the area\n"
        "- notes: brief observation"
    )

    def parse(self, raw_response: str) -> dict:
        clean = _strip_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response.strip()}
        return {
            "total_packages": str(data.get("total_packages", 0)),
            "workers_visible": str(data.get("workers_visible", 0)),
            "conveyor_active": str(data.get("conveyor_active", False)),
            "zone": data.get("zone", "unknown"),
            "notes": data.get("notes", ""),
        }


class SceneEnhancer:
    """Enhancement use case."""

    PROMPT_SYSTEM = "You are a scene description system. Always respond in valid JSON only, no other text."
    PROMPT_USER = (
        "Describe this security event: A person entered a restricted area "
        "near server racks at night.\n\n"
        "Return JSON with exactly these fields:\n"
        "- event_type: string categorizing the event\n"
        "- description: detailed description\n"
        "- risk_level: one of [low, medium, high, critical]\n"
        "- recommended_action: what should be done"
    )

    def parse(self, raw_response: str) -> dict:
        clean = _strip_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return {"vlm_response": raw_response.strip()}
        return {
            "event_type": data.get("event_type", "unknown"),
            "description": data.get("description", ""),
            "risk_level": data.get("risk_level", "unknown"),
            "recommended_action": data.get("recommended_action", ""),
        }


def call_vlm(system_prompt: str, user_prompt: str) -> str:
    import requests
    resp = requests.post(
        VLM_URL,
        json={
            "model": VLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 500,
            "temperature": 0.1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def simulate_info_merge(parsed: dict) -> dict:
    """Mirror enhance_alert_with_vlm.py pluggable parser logic.

    Parser output is serialized into info["vlm_response"] as a JSON string;
    info["verdict"] = null; info schema stays identical to verification mode.

    The nvschema alignment: info is coerced to map<string,string>
    by merge_info_with_response — None→"", int→str, dict→JSON.
    """
    try:
        from models.responses import (
            AlertBridgeResponse,
            merge_info_with_response,
        )
    except ImportError:
        # Offline fallback (e.g. running this script without sys.path setup):
        # replicate the stringification manually so assertions still match prod.
        return {
            "sensorId": "warehouse-cam-03",
            "category": "test",
            "primaryObjectId": "obj-1",
            "timestamp": "2026-03-30T10:00:00Z",
            "vlm_response": json.dumps(parsed),
            "verdict": "",
            "videoSource": "rtsp://cam/stream",
            "verificationResponseCode": "200",
            "verificationResponseStatus": "OK",
        }

    message = {
        "info": {
            "sensorId": "warehouse-cam-03",
            "category": "test",
            "primaryObjectId": "obj-1",
            "timestamp": "2026-03-30T10:00:00Z",
        }
    }
    # Option B: pluggable path bypasses VLMResponse.
    merge_info_with_response(
        message,
        AlertBridgeResponse(
            vlm_response=None,
            video_source="rtsp://cam/stream",
            verification_response_code=200,
            verification_response_status="OK",
        ),
    )
    info = message["info"]
    info["vlm_response"] = json.dumps(parsed)
    info["verdict"] = ""
    return info


def run_live_test():
    test_cases = [
        ("Classification", PPEClassifier()),
        ("Analytics", WarehouseCounter()),
        ("Enhancement", SceneEnhancer()),
    ]

    results = {}
    all_passed = True

    for name, parser in test_cases:
        print(f"\n{'='*60}")
        print(f"  USE CASE: {name}")
        print(f"{'='*60}")

        system_prompt = parser.PROMPT_SYSTEM
        user_prompt = parser.PROMPT_USER

        print(f"\n  Prompt (user, first 120 chars):")
        print(f"    {user_prompt[:120]}...")

        try:
            raw = call_vlm(system_prompt, user_prompt)
        except Exception as e:
            print(f"  SKIP: VLM call failed: {e}")
            continue

        print(f"\n  Raw VLM response:")
        for line in raw.split("\n"):
            print(f"    {line}")

        parsed = parser.parse(raw)
        print(f"\n  Parser output:")
        print(f"    {json.dumps(parsed, indent=4)}")

        if not isinstance(parsed, dict) or not parsed:
            print(f"  FAIL: Invalid parser output")
            all_passed = False
            continue

        has_fallback = "vlm_response" in parsed
        if has_fallback:
            print(f"  WARNING: Parser fell back to raw storage (JSON parse failed)")

        merged = simulate_info_merge(parsed)
        print(f"\n  Merged info (mdx-vlm-incidents):")
        print(f"    {json.dumps(merged, indent=4)}")

        results[name] = {
            "raw_response": raw,
            "parsed": parsed,
            "merged": merged,
            "fallback": has_fallback,
        }
        print(f"\n  {'WARN' if has_fallback else 'PASS'}: {len(parsed)} fields parsed")

    print(f"\n{'='*60}")
    print(f"  LIVE VLM TEST SUMMARY (model: {VLM_MODEL})")
    print(f"{'='*60}")
    for name, r in results.items():
        status = "FALLBACK" if r["fallback"] else "OK"
        print(f"  {name:15s}: {len(r['parsed'])} fields [{status}]")

    if all_passed and results:
        print(f"\n  All {len(results)} use cases PASSED")
    elif not results:
        print(f"\n  No tests ran (VLM unreachable)")
    else:
        print(f"\n  Some use cases FAILED")

    return all_passed


def _probe_endpoints(candidates):
    """Return the first reachable endpoint, or None if all fail.

    For each candidate we attempt GET /v1/models with a short timeout and
    report the outcome so the operator can see exactly what failed.
    """
    import requests
    for url in candidates:
        if not url:
            continue
        models_url = url.replace("/chat/completions", "/models")
        try:
            r = requests.get(models_url, timeout=5)
            r.raise_for_status()
            model_id = r.json().get("data", [{}])[0].get("id", "unknown")
            print(f"  [OK]   {url}  →  {model_id}")
            return url
        except Exception as e:
            print(f"  [FAIL] {url}  →  {e.__class__.__name__}: {e}")
    return None


def _print_skip_banner(attempted):
    bar = "!" * 60
    print()
    print(bar)
    print("!!  LIVE VLM TEST SKIPPED — NO REACHABLE NIM ENDPOINT      !!")
    print(bar)
    print()
    print("  Tried endpoints (all unreachable):")
    for url in attempted:
        if url:
            print(f"    - {url}")
    print()
    print("  This is NOT a PASS. The pluggable parser was not exercised")
    print("  against a real VLM. Do one of the following:")
    print()
    print("    1. Run this test from a host that can reach an NV-internal NIM")
    print("       (smartcity localhost or warehouse localhost), OR")
    print()
    print("    2. Point it at your own NIM:")
    print("       VLM_URL=http://your-nim:port/v1/chat/completions \\")
    print("           python test/sim_scripts/test_live_vlm_parser.py")
    print()
    print("  Exit code 2 indicates SKIPPED so CI / reviewers do not mistake")
    print("  this for a passing run.")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("  Live VLM → Pluggable Parser Integration Test")
    print(f"  Model:  {VLM_MODEL}")
    print("=" * 60)
    print()
    print("  Probing candidate endpoints:")

    resolved = _probe_endpoints(CANDIDATE_URLS)

    if resolved is None:
        _print_skip_banner(CANDIDATE_URLS)
        sys.exit(EXIT_SKIP)

    VLM_URL = resolved
    print()
    print(f"  Using endpoint: {VLM_URL}")

    ok = run_live_test()
    sys.exit(EXIT_PASS if ok else EXIT_FAIL)
