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

"""Eval's P1 and RP1 requests equal the shared request goldens in ``contracts/golden/`` (the console and the
agent check the same files), and every golden is well formed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import eval_run_lib  # noqa: E402
import eval_vlm_client  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "contracts" / "golden"
_spec = importlib.util.spec_from_file_location("load_golden", GOLDEN_DIR / "load_golden.py")
assert _spec is not None and _spec.loader is not None
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

TESTED = ["chart/request", "p1/base", "rp1/report"]


def _cases(producer: str | None = None) -> list[tuple[str, str]]:
    out = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        for name, entry in json.loads(path.read_text(encoding="utf-8"))["cases"].items():
            if producer is None or producer in entry["producers"]:
                out.append((path.stem, name))
    return out


def _assert_golden(name: str, case: str, produced: dict) -> None:
    problem = golden.mismatch(name, case, produced, "eval")
    assert problem is None, problem


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The bodies eval's client posts, with no network."""
    bodies: list[dict] = []

    def fake_post(url, *, headers, json, timeout):  # noqa: ARG001
        bodies.append(json)
        return _FakeResponse()

    monkeypatch.setattr(eval_vlm_client, "gateway_credentials", lambda: ("https://gw.example/v1", "key"))
    monkeypatch.setattr(eval_vlm_client.requests, "post", fake_post)
    return bodies


def test_every_eval_case_is_checked_here():
    assert sorted(f"{name}/{case}" for name, case in _cases("eval")) == TESTED


def test_p1_payload_and_sent_body_equal_the_golden(sent):
    inputs = golden.case("p1", "base")["inputs"]
    _assert_golden("p1", "base", eval_vlm_client.p1_request_payload(inputs["model"], inputs["video_url"]))
    eval_vlm_client.analyze_video_with_p1(inputs["model"], inputs["video_url"])
    assert len(sent) == 1
    _assert_golden("p1", "base", sent[0])


def test_rp1_sent_body_equals_the_golden(sent):
    inputs = golden.case("rp1", "report")["inputs"]
    eval_vlm_client.generate_report_with_rp1(
        eval_run_lib.RP1_MODEL, inputs["report"], inference_config=eval_run_lib.RP1_INFERENCE_CONFIG
    )
    assert len(sent) == 1
    _assert_golden("rp1", "report", sent[0])


def test_chart_request_equals_the_golden_byte_for_byte():
    """The C1c chart body equals the console's: as a JSON value, and as the bytes httpx posts upstream.

    ``vlm-gateway`` re-posts the console's parsed body with httpx (``json=body``), and the eval adapter posts its body
    the same way, so equal key order and values give identical upstream bytes."""
    import httpx

    from evalv2.requests.chart import build_chart_request

    inputs = golden.case("chart", "request")["inputs"]
    built = build_chart_request(inputs["model"], inputs["request"])
    _assert_golden("chart", "request", built)
    on_file = json.loads((GOLDEN_DIR / "chart.json").read_text(encoding="utf-8"))["cases"]["request"]["body"]

    def wire(body: dict) -> bytes:
        return httpx.Request("POST", "https://upstream.example/v1/chat/completions", json=body).content

    assert wire(built) == wire(on_file)
    assert json.dumps(built, separators=(",", ":"), ensure_ascii=False) == json.dumps(
        on_file, separators=(",", ":"), ensure_ascii=False), "JSON.stringify order and text, as the console sends"


@pytest.mark.parametrize(("name", "case"), _cases())
def test_golden_case_is_well_formed(name, case):
    document = golden.load(name)
    assert document["golden"] == name and document["description"]
    entry = golden.case(name, case)  # expands every contract reference and checks its sha256
    assert entry["description"] and entry["producers"] and set(entry["producers"]) <= golden.PRODUCERS
    assert isinstance(entry["inputs"], dict) and entry["body"]["messages"]
    assert golden.normalize(entry["body"]) == entry["body"], "inline images are stored as the placeholder"
    assert not set(entry["transport"]) & set(entry["body"]), "a transport key is never also in the body"


def test_a_changed_contract_file_fails_with_its_hash(monkeypatch, tmp_path):
    (tmp_path / "golden").mkdir()
    (tmp_path / "incident_extraction_prompt.txt").write_text("edited prompt")
    monkeypatch.setattr(golden, "CONTRACTS_DIR", tmp_path)
    with pytest.raises(golden.GoldenError, match="changed"):
        golden.expand({"$contract_text": "incident_extraction_prompt.txt", "sha256": "0" * 64})


def test_mismatch_reports_a_diff_and_allows_only_the_transport_value():
    body = golden.case("p1", "base")["body"]
    assert golden.mismatch("p1", "base", {**body, "stream": False}, "eval") is None
    assert "transport key 'stream'" in golden.mismatch("p1", "base", {**body, "stream": True}, "eval")
    assert "differs from the golden" in golden.mismatch("p1", "base", {**body, "temperature": 0.2}, "eval")
    assert "not a producer" in golden.mismatch("caption", "window", {}, "eval")
