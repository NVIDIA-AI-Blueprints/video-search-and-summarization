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

"""Gated experiment settings of ``incident_report_gen`` (dev-profile-incident PR 6B).

The point of these tests: an experimental setting can never change a production request.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest

from vss_agents import incident_contract
from vss_agents.incident_contract import IncidentExtractionError
from vss_agents.tools import incident_experiments
from vss_agents.tools.incident_report_gen import ExperimentRefusedError
from vss_agents.tools.incident_report_gen import IncidentReportGenInput
from vss_agents.tools.incident_report_gen import experiment_settings
from vss_agents.tools.incident_report_gen import p1_request_body

from . import test_incident_report_gen as report_gen_tests
from .test_incident_report_gen import MODEL_OUTPUT
from .test_incident_report_gen import VIDEO_URL

if TYPE_CHECKING:
    from pathlib import Path

EXPERIMENT_ID = "p1-v4-e01-test"
EXPERIMENT_TEXT = "An experimental extraction prompt.\n"


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "p1").mkdir()
    base = incident_contract.extraction_prompt()
    (tmp_path / "p1" / "p1-v4-base.txt").write_text(base)
    (tmp_path / "p1" / f"{EXPERIMENT_ID}.txt").write_text(EXPERIMENT_TEXT)
    entries = [
        {
            "id": "p1-v4-base",
            "file": "p1/p1-v4-base.txt",
            "sha256": hashlib.sha256(base.encode()).hexdigest(),
            "contract_version": incident_contract.contract_version(),
        },
        {
            "id": EXPERIMENT_ID,
            "file": f"p1/{EXPERIMENT_ID}.txt",
            "sha256": hashlib.sha256(EXPERIMENT_TEXT.encode()).hexdigest(),
            "contract_version": incident_contract.contract_version(),
        },
        {
            "id": "p1-v4-e02-old",
            "file": f"p1/{EXPERIMENT_ID}.txt",
            "sha256": hashlib.sha256(EXPERIMENT_TEXT.encode()).hexdigest(),
            "contract_version": "incident-contract-v3",
        },
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"prompts": entries}))
    monkeypatch.setenv(incident_experiments.REGISTRY_ENV, str(path))
    return path


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")


def _input(**overrides: object) -> IncidentReportGenInput:
    return IncidentReportGenInput(incident_id="v1", video_url=VIDEO_URL, model_run_id="run1", **overrides)


class TestGate:
    def test_a_production_request_has_no_experiment_settings_even_when_enabled(self, enabled: None):
        assert experiment_settings(_input(), None) is None

    @pytest.mark.parametrize("overrides", [{"experiment_prompt_id": EXPERIMENT_ID}, {"skip_crops": True}])
    def test_every_experimental_setting_is_refused_without_the_flag(
        self, monkeypatch: pytest.MonkeyPatch, registry: Path, overrides: dict
    ):
        monkeypatch.delenv(incident_experiments.EXPERIMENTS_FLAG, raising=False)
        with pytest.raises(incident_experiments.ExperimentsDisabledError):
            experiment_settings(_input(**overrides), None)

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_only_the_exact_flag_value_enables_experiments(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, value)
        assert incident_experiments.experiments_enabled() is False

    def test_a_registry_prompt_is_served_with_its_hash(self, enabled: None, registry: Path):
        settings = experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID, skip_crops=True), None)
        assert settings is not None
        assert settings["_prompt_text"] == EXPERIMENT_TEXT
        assert settings["prompt_sha256"] == hashlib.sha256(EXPERIMENT_TEXT.encode()).hexdigest()
        assert settings["persisted"] is False and settings["skip_crops"] is True

    @pytest.mark.parametrize(
        "prompt_id, match",
        [("p1-v4-e99-missing", "not in the experiment"), ("p1-v4-e02-old", "incident-contract-v3"), ("x", "not a")],
    )
    def test_unknown_foreign_or_malformed_prompts_are_refused(
        self, enabled: None, registry: Path, prompt_id: str, match: str
    ):
        with pytest.raises(incident_experiments.ExperimentPromptError, match=match):
            experiment_settings(_input(experiment_prompt_id=prompt_id, skip_crops=True), None)

    def test_an_edited_prompt_file_is_refused(self, enabled: None, registry: Path):
        (registry.parent / "p1" / f"{EXPERIMENT_ID}.txt").write_text("edited")
        with pytest.raises(incident_experiments.ExperimentPromptError, match="SHA-256"):
            experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID, skip_crops=True), None)

    def test_a_reviewer_prompt_cannot_ride_along(self, enabled: None, registry: Path):
        with pytest.raises(ValueError, match="reviewer prompt"):
            experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID, skip_crops=True), "look at the door")

    def test_cropping_that_would_upload_to_r2_is_refused(self, enabled: None, registry: Path):
        with patch("vss_agents.tools.incident_report_gen.is_r2_configured", return_value=True):
            with pytest.raises(ExperimentRefusedError, match="R2"):
                experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID, max_crop_calls=10), None)
            assert experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID, skip_crops=True), None)

    def test_experimental_cropping_needs_a_crop_call_cap(self, enabled: None, registry: Path):
        with patch("vss_agents.tools.incident_report_gen.is_r2_configured", return_value=False):
            with pytest.raises(ValueError, match="max_crop_calls"):
                experiment_settings(_input(experiment_prompt_id=EXPERIMENT_ID), None)
            with pytest.raises(ValueError, match="skip_crops"):
                experiment_settings(_input(skip_crops=True, max_crop_calls=10), None)
            # the cap alone makes a base-prompt crops-on request experimental (the end-to-end profile's C2)
            settings = experiment_settings(_input(max_crop_calls=10), None)
        assert settings is not None and settings["prompt_id"] is None and settings["max_crop_calls"] == 10

    def test_a_crop_call_cap_is_refused_without_the_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(incident_experiments.EXPERIMENTS_FLAG, raising=False)
        with pytest.raises(incident_experiments.ExperimentsDisabledError):
            experiment_settings(_input(max_crop_calls=10), None)

    def test_the_committed_registry_baseline_is_the_vendored_production_prompt(self, enabled: None, monkeypatch):
        monkeypatch.delenv(incident_experiments.REGISTRY_ENV, raising=False)
        if not incident_experiments.DEFAULT_REGISTRY.exists():
            pytest.skip("not a full checkout")
        text, digest = incident_experiments.resolve_prompt("p1-v4-base")
        assert text == incident_contract.extraction_prompt()
        assert digest == incident_experiments.production_prompt_sha256()


class TestRequestParity:
    def test_the_baseline_prompt_gives_the_production_p1_body_byte_for_byte(self, enabled: None, registry: Path):
        text, _ = incident_experiments.resolve_prompt("p1-v4-base")
        production = p1_request_body("vlm", VIDEO_URL)
        assert json.dumps(p1_request_body("vlm", VIDEO_URL, None, text)) == json.dumps(production)

    def test_an_experiment_prompt_changes_only_the_prompt_text(self, enabled: None, registry: Path):
        production = p1_request_body("vlm", VIDEO_URL)
        experiment = p1_request_body("vlm", VIDEO_URL, None, EXPERIMENT_TEXT)
        assert experiment["messages"][0]["content"][1]["text"] == EXPERIMENT_TEXT
        experiment["messages"][0]["content"][1]["text"] = production["messages"][0]["content"][1]["text"]
        assert experiment == production


def _handler(requests: list[dict], content: str | None = None, usage: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content or json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}],
                "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    return handler


class TestToolRuns:
    _run = report_gen_tests.TestEndToEnd._run  # not imported by name, so pytest does not collect it twice

    @pytest.mark.asyncio
    async def test_an_experimental_run_reports_its_prompt_and_timing_and_writes_nothing(
        self, enabled: None, registry: Path
    ):
        requests: list[dict] = []
        with patch("vss_agents.tools.incident_report_gen.collect_evidence", new_callable=AsyncMock) as crops:
            result, persist = await self._run(
                _handler(requests),
                tool_input=_input(
                    experiment_prompt_id=EXPERIMENT_ID, skip_crops=True, skip_rp1=True, video_duration_seconds=30.0
                ),
            )
        assert requests[0]["messages"][0]["content"][1]["text"] == EXPERIMENT_TEXT
        assert result.experiment == {
            "prompt_id": EXPERIMENT_ID,
            "prompt_sha256": hashlib.sha256(EXPERIMENT_TEXT.encode()).hexdigest(),
            "skip_crops": True,
            "max_crop_calls": None,
            "max_zoom_calls": None,
            "object_tracking": False,
            "captions": None,  # no caption pass asked for
            "max_caption_calls": None,
            "persisted": False,
            "p1_report": result.report,  # the validated P1 report before any zoom correction
        }
        assert result.p1_elapsed_ms is not None and result.usage == {"prompt_tokens": 100, "completion_tokens": 50}
        assert result.revision_id is None
        persist.assert_not_awaited()
        crops.assert_not_awaited()
        assert all(entry["image"] is None and "skip_crops" in entry["image_gap"] for entry in result.evidence)
        # No crop, no located item, so the zoomed second look is off too.
        assert all(entry["zoom_checked"] is False and "skip_crops" in entry["zoom_gap"] for entry in result.evidence)

    @pytest.mark.asyncio
    async def test_extraction_is_identical_with_and_without_crops(self, enabled: None, registry: Path):
        outputs = []
        for skip in (True, False):
            with patch("vss_agents.tools.incident_report_gen.is_r2_configured", return_value=False):
                result, _ = await self._run(
                    _handler([]),
                    tool_input=_input(
                        experiment_prompt_id=EXPERIMENT_ID,
                        skip_crops=skip,
                        max_crop_calls=None if skip else 10,
                        skip_rp1=True,
                        video_duration_seconds=30.0,
                    ),
                )
            outputs.append(result)
        assert outputs[0].report == outputs[1].report and outputs[0].raw_output == outputs[1].raw_output

    @pytest.mark.asyncio
    async def test_a_production_request_is_unchanged_with_experiments_enabled(self, enabled: None, registry: Path):
        requests: list[dict] = []
        result, persist = await self._run(
            _handler(requests), tool_input=_input(skip_rp1=True, video_duration_seconds=30.0)
        )
        assert requests[0] == p1_request_body("nvidia/cosmos-3-nano-reasoner", VIDEO_URL) | {"stream": False}
        assert result.experiment is None and result.p1_elapsed_ms is None and result.usage is None
        assert result.revision_id == 7
        persist.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_rejected_experimental_output_still_names_its_prompt(self, enabled: None, registry: Path):
        with pytest.raises(IncidentExtractionError) as excinfo:
            await self._run(
                _handler([], content="{not json"),
                tool_input=_input(
                    experiment_prompt_id=EXPERIMENT_ID, skip_crops=True, skip_rp1=True, video_duration_seconds=30.0
                ),
            )
        assert excinfo.value.experiment["prompt_id"] == EXPERIMENT_ID  # type: ignore[attr-defined]
        assert excinfo.value.telemetry["p1_elapsed_ms"] is not None  # type: ignore[attr-defined]


class _RecordingService:
    """Stands in for the crop service and records the store each call used."""

    def __init__(self) -> None:
        self.stores: list = []
        self.detector = MagicMock()

    async def process_incident_report(self, *, video_ref, video_id, report, store=None, **_kwargs):
        self.stores.append(store)
        return []


async def _run_with_service(handler, tool_input, service):
    from vss_agents.tools.incident_report_gen import IncidentReportGenConfig
    from vss_agents.tools.incident_report_gen import incident_report_gen
    from vss_agents.utils import crop_evidence

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    config = IncidentReportGenConfig(
        vlm_base_url="https://switchyard.example",
        vlm_model_name="nvidia/cosmos-3-nano-reasoner",
        rp1_base_url="https://switchyard.example/",
    )
    with (
        patch("vss_agents.tools.incident_report_gen.httpx.AsyncClient", side_effect=client_factory),
        patch.object(crop_evidence, "get_crop_evidence_service", return_value=service),
        patch(
            "vss_agents.tools.incident_report_gen.persist_revision", new_callable=AsyncMock, return_value=7
        ) as persist,
        patch("vss_agents.tools.incident_report_gen.is_r2_configured", return_value=False),
        patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}),
    ):
        async with incident_report_gen(config, MagicMock()) as function_info:
            result = await function_info.single_fn(tool_input)
    return result, persist


class TestTemporaryEvidenceStore:
    """Crop option (a): an experimental crops-on run uses an isolated, temporary in-memory store."""

    @pytest.mark.asyncio
    async def test_experimental_crops_use_a_fresh_in_memory_store_per_request(self, enabled: None, registry: Path):
        from vss_agents.tools.incident_report_gen import EXPERIMENT_EVIDENCE_STORE
        from vss_agents.utils.crop_evidence import InMemoryEvidenceStore

        service = _RecordingService()
        for _ in range(2):
            result, persist = await _run_with_service(
                _handler([]),
                _input(
                    experiment_prompt_id=EXPERIMENT_ID, max_crop_calls=10, skip_rp1=True, video_duration_seconds=30.0
                ),
                service,
            )
            assert result.experiment["evidence_store"] == EXPERIMENT_EVIDENCE_STORE
            assert result.revision_id is None
            persist.assert_not_awaited()
        assert all(isinstance(store, InMemoryEvidenceStore) for store in service.stores)
        assert service.stores[0] is not service.stores[1]  # isolated: never shared between requests

    @pytest.mark.asyncio
    async def test_a_production_request_keeps_the_services_own_store(self, enabled: None, registry: Path):
        service = _RecordingService()
        result, _persist = await _run_with_service(
            _handler([]), _input(skip_rp1=True, video_duration_seconds=30.0), service
        )
        assert service.stores == [None]  # the service's own store (R2 in production), as before
        assert result.experiment is None and result.revision_id == 7

    @pytest.mark.asyncio
    async def test_the_temporary_store_receives_the_crops_and_r2_is_never_touched(self):
        from vss_agents.tools.incident_report_gen import collect_evidence
        from vss_agents.utils import crop_evidence

        class Crop:
            image_bytes = b"jpeg"
            normalized_box = (0, 0, 500, 500)
            frame_bytes = b"frame"
            box_seconds = 0
            located = True

        class Service(crop_evidence.CropEvidenceService):
            def crop_item(self, *args, **kwargs):
                return Crop()

        r2 = AsyncMock()
        service = Service(store=r2)
        service.is_detector_available = lambda: True  # type: ignore[method-assign]
        store = crop_evidence.InMemoryEvidenceStore()
        report = incident_contract.parse_report(json.dumps(MODEL_OUTPUT))
        with patch.object(crop_evidence, "get_crop_evidence_service", return_value=service):
            entries = await collect_evidence(video_url=VIDEO_URL, incident_id="v1", report=report, store=store)
        assert len(store.images) == 2 * len(entries) == 6 and all(e["image"] in store.images for e in entries)
        r2.put_image.assert_not_awaited()


class TestCropCallCap:
    """The end-to-end profile's per-request crop-call cap: enforced before each grounding attempt, and reported."""

    @pytest.mark.asyncio
    async def test_a_crops_on_experiment_reports_its_crop_stage_and_stops_at_the_cap(
        self, enabled: None, registry: Path
    ):
        import numpy as np

        from vss_agents.utils import crop_evidence

        calls: list[str] = []

        class Detector:
            def is_available(self) -> bool:
                return True

            def detect(self, frame, query):
                calls.append(query)
                return [crop_evidence.Detection(box=[0, 0, 500, 500], score=0.9, label=query)]

        class Service(crop_evidence.CropEvidenceService):
            def crop_item(self, video_ref, timestamp_seconds, label, detector=None):
                frame = np.zeros((40, 40, 3), dtype=np.uint8)  # no video read: the frame is synthetic
                return crop_evidence.crop_evidence_item(frame, timestamp_seconds, label, detector)

        service = Service(detector=Detector())
        result, persist = await _run_with_service(
            _handler([]),
            _input(max_crop_calls=2, skip_rp1=True, video_duration_seconds=30.0),
            service,
        )
        crop = result.experiment["crop"]
        assert len(calls) == 2  # three items, but only two grounding calls were allowed
        assert crop["cap"] == 2 and crop["calls"] == 2 and crop["refused_by_cap"] == 1 and crop["cap_reached"]
        assert crop["items"] == 3 and crop["images"] == 2 and crop["gaps"] == {"crop-call cap reached": 1}
        assert crop["elapsed_ms"] >= 0 and result.experiment["prompt_id"] is None
        assert result.revision_id is None
        persist.assert_not_awaited()
        assert service.detector.__class__ is Detector  # the shared detector is never wrapped in place
