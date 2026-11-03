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

"""Experimental captions (blocker X16): an experiment runs production's caption pass when it asks for it.

``experiment_captions`` picks where the windows go: ``inline`` (base64 data URLs, nothing stored) or ``r2`` (the
owner-approved ``INCIDENT_EXPERIMENT_SCRATCH_PREFIX``, each window deleted after its call); ``max_caption_calls`` caps
the pass's HTTP attempts. Everything runs against fakes: a fake model endpoint (``httpx.MockTransport``), no download
or ffmpeg, and a fake R2 client behind the agent's own R2 helpers. Nothing reaches a model or R2.
"""

from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest

from vss_agents import incident_contract
from vss_agents.tools import incident_experiments
from vss_agents.tools.incident_report_gen import CAPTION_PROMPT_IDS
from vss_agents.tools.incident_report_gen import EXPERIMENT_SCRATCH_PREFIX_ENV
from vss_agents.tools.incident_report_gen import ExperimentRefusedError
from vss_agents.tools.incident_report_gen import IncidentReportGenInput
from vss_agents.tools.incident_report_gen import experiment_scratch_store
from vss_agents.tools.incident_report_gen import experiment_settings
from vss_agents.tools.incident_report_gen import incident_report_gen
from vss_agents.tools.incident_report_gen import p1_request_body
from vss_agents.utils import chunk_captions

from ..incident_contract.test_parity_golden import golden
from ..utils.test_chunk_captions import FakeR2
from ..utils.test_chunk_captions import _no_wait_retry
from .test_incident_report_gen import MODEL_OUTPUT
from .test_incident_report_gen import VIDEO_URL
from .test_incident_report_gen import _config

if TYPE_CHECKING:
    from pathlib import Path

MODEL = _config().vlm_model_name
# Windows [0, 20], [18, 38] and [36, 47.5] (20 s, 2 s overlap): the timeline of the golden case p1/captions.
DURATION = 47.5
# What the model answers for each window, in window seconds; offset to video seconds, that case's timeline.
WINDOW_CAPTIONS = {
    0: "[0s-6s] A man in a grey hoodie walks to the shop counter.\n"
    "[6s-20s] He takes a bottle from the shelf and hides it inside his jacket.",
    1: "[0s-20s] The aisle stays empty; nothing happens.",
    2: "[0s-5s] The man walks out through the front door without paying.",
}
CAPTION_USAGE = {"prompt_tokens": 8516, "completion_tokens": 117, "total_tokens": 8633}
CHUNKING_DEFAULTS = {
    "window_seconds": 20.0,
    "overlap_seconds": 2.0,
    "min_clip_seconds": 30.0,
    "num_frames": 32,
    "concurrency": 4,
}
R2_ENV = {"R2_ACCOUNT_ID": "fake", "R2_ACCESS_KEY": "fake", "R2_SECRET_KEY": "fake", "R2_BUCKET": "bucket"}
AGENT_SETTINGS = (
    "INCIDENT_CHUNKING_ENABLED",
    "INCIDENT_CHUNK_WINDOW_SECONDS",
    "INCIDENT_CHUNK_OVERLAP_SECONDS",
    "INCIDENT_CHUNK_MIN_CLIP_SECONDS",
    "INCIDENT_CHUNK_NUM_FRAMES",
    "INCIDENT_CHUNK_CONCURRENCY",
    "INCIDENT_TRACKING_ENABLED",
    "INCIDENT_OBJECT_TRACKING_ENABLED",
    "INCIDENT_SOM_ENABLED",
    "INCIDENT_ZOOM_NAMES_ENABLED",
    EXPERIMENT_SCRATCH_PREFIX_ENV,
    *R2_ENV,
)


@pytest.fixture
def experiment_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local experiment agent's environment: experiments on and nothing else (no R2, no caption flag)."""
    for name in AGENT_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)


@pytest.fixture
def no_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2 must never be reached: asking for a client or a signed URL fails the test."""

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("R2 was reached")

    monkeypatch.setattr(chunk_captions, "get_r2_client", refuse)
    monkeypatch.setattr(chunk_captions, "get_r2_presigned_url", refuse)


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> FakeR2:
    """A fake bucket behind the agent's own R2 helpers, with fake credentials (so R2 counts as configured)."""
    r2 = FakeR2()
    for name, value in R2_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(chunk_captions, "get_r2_client", lambda: r2)
    monkeypatch.setattr(
        chunk_captions, "get_r2_presigned_url", lambda key, _expires_in: f"https://signed.example/{key}?sig=1"
    )
    return r2


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """No download and no ffmpeg: each window file says which window it is (``window <index>``), padded to
    ``sizes[index]`` bytes when a test sets one; ``downloads`` lists the downloads."""
    state = SimpleNamespace(sizes={}, downloads=[])

    async def download(video_url: str, target: Path) -> None:
        assert video_url == VIDEO_URL
        state.downloads.append(video_url)
        target.write_bytes(b"source")

    async def cut(ffmpeg: str, source: Path, window: chunk_captions.Window, target: Path) -> None:
        target.write_bytes(f"window {window.index}".encode().ljust(state.sizes.get(window.index, 0), b" "))

    monkeypatch.setattr(chunk_captions, "download_video", download)
    monkeypatch.setattr(chunk_captions, "cut_window", cut)
    monkeypatch.setattr(chunk_captions, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    return state


class Gateway:
    """The model endpoint: a caption call is answered for its window, P1 with the contract report.

    ``statuses`` gives, per window, HTTP statuses to answer before the caption. With ``r2`` an R2 window must exist
    when the model fetches it, and each fetch is logged in ``r2.events`` as ``("call", key)``.
    """

    def __init__(self, r2: FakeR2 | None = None, statuses: dict[int, list[int]] | None = None) -> None:
        self.r2 = r2
        self.statuses = statuses or {}
        self.captions: list[tuple[int, str]] = []
        self.p1: list[dict[str, Any]] = []

    def _window(self, url: str) -> int:
        if url.startswith("data:video/mp4;base64,"):
            return int(base64.b64decode(url.split(",", 1)[1]).split()[1])
        key = url.split("signed.example/")[1].split("?")[0]
        assert self.r2 is not None and key in self.r2.objects, f"the model fetched {key}, which does not exist"
        self.r2.events.append(("call", key))
        return int(key.rsplit("/", 1)[1].split(".")[0])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "response_format" in body:
            self.p1.append(body)
            reply = {"choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}]}
            return httpx.Response(200, json={**reply, "usage": {"total_tokens": 7000}})
        index = self._window(body["messages"][1]["content"][0]["video_url"]["url"])
        self.captions.append((index, body["messages"][1]["content"][0]["video_url"]["url"]))
        if self.statuses.get(index):
            return httpx.Response(self.statuses[index].pop(0), text="throttling_error")
        reply = {"choices": [{"message": {"content": WINDOW_CAPTIONS[index]}, "finish_reason": "stop"}]}
        return httpx.Response(200, json={**reply, "usage": CAPTION_USAGE})


async def _analyze(gateway: Gateway, **fields: Any) -> tuple[Any, AsyncMock]:
    """One ``incident_report_gen`` run against ``gateway`` (crops and the revision write faked)."""
    inputs: dict[str, Any] = {
        "incident_id": "v1",
        "video_url": VIDEO_URL,
        "video_key": "v1",
        "skip_rp1": True,
        "video_duration_seconds": DURATION,
        **fields,
    }
    transport = httpx.MockTransport(gateway)
    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with (
        patch("vss_agents.tools.incident_report_gen.httpx.AsyncClient", side_effect=client_factory),
        patch("vss_agents.tools.incident_report_gen.collect_evidence", new_callable=AsyncMock, return_value=[]),
        patch(
            "vss_agents.tools.incident_report_gen.persist_revision", new_callable=AsyncMock, return_value=7
        ) as persist,
        patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}),
    ):
        async with incident_report_gen(_config(), MagicMock()) as function_info:
            result = await function_info.single_fn(IncidentReportGenInput(**inputs))
    return result, persist


def _golden_problem(body: dict[str, Any]) -> str | None:
    """Why the P1 body differs from the golden p1/captions (with the per-call values as its placeholders)."""
    body = json.loads(json.dumps(body))
    assert body["model"] == MODEL and body["messages"][0]["content"][0]["video_url"]["url"] == VIDEO_URL
    body["model"] = "<model>"
    body["messages"][0]["content"][0]["video_url"]["url"] = "<video-url>"
    problem: str | None = golden.mismatch("p1", "captions", body, "agent")
    return problem


def _input(**fields: Any) -> IncidentReportGenInput:
    return IncidentReportGenInput(incident_id="v1", video_url=VIDEO_URL, model_run_id="run1", **fields)


INLINE = {"experiment_captions": "inline", "max_caption_calls": 9, "skip_crops": True}


class TestGate:
    """The two settings come together, need experiments enabled, and R2 windows need the approved prefix."""

    def test_captions_make_a_request_experimental_and_need_the_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(incident_experiments.EXPERIMENTS_FLAG, raising=False)
        for fields in (INLINE, {"max_caption_calls": 5, "skip_crops": True}):
            with pytest.raises(incident_experiments.ExperimentsDisabledError):
                experiment_settings(_input(**fields), None)

    def test_captions_and_their_cap_come_together(self, experiment_agent: None):
        with pytest.raises(ValueError, match="needs max_caption_calls"):
            experiment_settings(_input(experiment_captions="inline", skip_crops=True), None)
        with pytest.raises(ValueError, match="only with experiment_captions"):
            experiment_settings(_input(max_caption_calls=5, skip_crops=True), None)

    def test_inline_needs_no_storage_settings(self, experiment_agent: None):
        settings = experiment_settings(
            _input(experiment_captions="inline", max_caption_calls=5, max_crop_calls=10), None
        )
        assert settings is not None and settings["captions"] == "inline" and settings["max_caption_calls"] == 5
        assert "_scratch_root" not in settings

    def test_r2_is_refused_without_the_approved_prefix(self, experiment_agent: None, monkeypatch: pytest.MonkeyPatch):
        for name, value in R2_ENV.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ExperimentRefusedError, match=EXPERIMENT_SCRATCH_PREFIX_ENV):
            experiment_settings(_input(experiment_captions="r2", max_caption_calls=5, skip_crops=True), None)

    @pytest.mark.parametrize(
        "prefix",
        [
            "scratch/analyze",
            "/scratch/analyze/",
            "scratch/analyze/eval",
            "scratch",
            "uploads",
            "evidence/eval",
            "scratch/../uploads",
            "scratch/./eval",
            "scratch//eval",
        ],
    )
    def test_r2_refuses_a_root_that_is_not_a_dedicated_scratch_folder(
        self, experiment_agent: None, monkeypatch: pytest.MonkeyPatch, prefix: str
    ):
        for name, value in R2_ENV.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, prefix)
        with pytest.raises(ExperimentRefusedError, match="dedicated folder under scratch/"):
            experiment_settings(_input(experiment_captions="r2", max_caption_calls=5, skip_crops=True), None)

    def test_r2_needs_r2_settings(self, experiment_agent: None, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, "scratch/eval")
        with pytest.raises(ExperimentRefusedError, match="needs R2 settings"):
            experiment_settings(_input(experiment_captions="r2", max_caption_calls=5, skip_crops=True), None)

    def test_r2_with_cropping_on_is_refused(self, experiment_agent: None, monkeypatch: pytest.MonkeyPatch):
        """Experimental crops still refuse an agent holding R2 settings; the scratch prefix is the only exception."""
        for name, value in R2_ENV.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, "scratch/eval")
        with pytest.raises(ExperimentRefusedError, match="set skip_crops"):
            experiment_settings(_input(experiment_captions="r2", max_caption_calls=5, max_crop_calls=10), None)

    def test_r2_with_an_approved_prefix_carries_its_root(self, experiment_agent: None, monkeypatch: pytest.MonkeyPatch):
        for name, value in R2_ENV.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, " /scratch/eval/ ")
        settings = experiment_settings(_input(experiment_captions="r2", max_caption_calls=5, skip_crops=True), None)
        assert settings is not None and settings["captions"] == "r2" and settings["_scratch_root"] == "scratch/eval"

    def test_an_experiment_store_is_never_productions(self):
        assert isinstance(experiment_scratch_store("inline", "scratch/eval"), chunk_captions.InlineScratchStore)
        store = experiment_scratch_store("r2", "scratch/eval")
        assert isinstance(store, chunk_captions.ScratchStore)
        assert store.root == "scratch/eval" and store.delete_after_use is True
        with pytest.raises(ExperimentRefusedError):
            experiment_scratch_store("r2", None)


class TestInlineCaptions:
    """``experiment_captions: "inline"``: no storage at all; P1 gets exactly production's context message."""

    @pytest.mark.asyncio
    async def test_each_window_goes_inline_and_p1_carries_the_production_caption_context(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        gateway = Gateway()
        result, persist = await _analyze(gateway, **INLINE)
        assert sorted(index for index, _ in gateway.captions) == [0, 1, 2]
        assert all(url.startswith("data:video/mp4;base64,") for _, url in gateway.captions)
        assert len(gateway.p1) == 1
        problem = _golden_problem(gateway.p1[0])  # production's P1 with the caption context, byte for byte
        assert problem is None, problem
        caption = result.experiment["caption"]
        assert caption == {
            "store": "inline",
            "scratch_root": None,
            "elapsed_ms": caption["elapsed_ms"],
            "windows": 3,
            "context_sent": True,
            "skipped": None,
            "cap": 9,
            "calls": 3,
            "refused_by_cap": 0,
            "cap_reached": False,
            "tokens": 3 * CAPTION_USAGE["total_tokens"],
            "calls_without_usage": 0,
        }
        assert caption["elapsed_ms"] >= 0
        assert result.experiment["captions"] == "inline" and result.experiment["max_caption_calls"] == 9
        assert result.experiment["persisted"] is False and result.revision_id is None
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_flags_and_the_caption_stage_are_recorded_as_in_production(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        result, _ = await _analyze(Gateway(), **INLINE)
        provenance = result.provenance
        assert provenance["flags"] == {
            "chunking": {"enabled": True, "ran": True, "setting_enabled": False, "skipped": None, **CHUNKING_DEFAULTS},
            "tracking": False,
            "object_tracking": False,
            "set_of_marks": False,
            "zoom_names": False,  # skip_crops: no crop, so no zoomed second look
            "experiments": True,
            "experiment": True,
        }
        caption = provenance["stages"]["caption"]
        assert caption["model"] == MODEL
        assert caption["prompts"] == {
            prompt_id: incident_contract.prompt_sha256(prompt_id) for prompt_id in CAPTION_PROMPT_IDS
        }
        assert caption["calls"] == 3 and caption["failed"] == 0 and caption["refused"] == 0
        assert caption["total_tokens"] == 3 * CAPTION_USAGE["total_tokens"]
        assert provenance["stages"]["p1"]["prompts"]["caption/context-preamble"] == incident_contract.prompt_sha256(
            "caption/context-preamble"
        )

    @pytest.mark.asyncio
    async def test_inline_captions_run_with_cropping_on_an_agent_without_r2(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        """Track B's production-like shape: captions, crops (in memory) and the capped zoom look together."""
        gateway = Gateway()
        result, _ = await _analyze(
            gateway, experiment_captions="inline", max_caption_calls=9, max_crop_calls=40, max_zoom_calls=80
        )
        assert result.provenance["flags"] == {
            "chunking": {"enabled": True, "ran": True, "setting_enabled": False, "skipped": None, **CHUNKING_DEFAULTS},
            "tracking": False,
            "object_tracking": False,
            "set_of_marks": False,
            "zoom_names": True,
            "experiments": True,
            "experiment": True,
        }
        assert {"caption", "p1", "grounding", "zoom"} <= set(result.provenance["stages"])
        assert len(gateway.p1[0]["messages"]) == 2 and result.experiment["caption"]["context_sent"] is True

    @pytest.mark.asyncio
    async def test_a_window_over_the_inline_limit_skips_the_pass_and_p1_runs_without_context(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        fake_ffmpeg.sizes[1] = chunk_captions.INLINE_MAX_BYTES + 1
        gateway = Gateway()
        result, _ = await _analyze(gateway, **INLINE)
        assert gateway.captions == [], "no window is sent once one is over the limit"
        assert gateway.p1 == [p1_request_body(MODEL, VIDEO_URL)], "P1 ran without context"
        caption = result.experiment["caption"]
        assert caption["skipped"] is not None and caption["skipped"].endswith(
            f"is {chunk_captions.INLINE_MAX_BYTES + 1} bytes, over the inline limit of {chunk_captions.INLINE_MAX_BYTES}"
        )
        assert caption["calls"] == 0 and caption["windows"] == 0 and caption["context_sent"] is False
        assert result.provenance["flags"]["chunking"]["enabled"] is False
        assert "caption" not in result.provenance["stages"]

    @pytest.mark.asyncio
    async def test_a_clip_with_more_windows_than_the_cap_gets_no_caption_pass(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        gateway = Gateway()
        result, _ = await _analyze(gateway, **{**INLINE, "max_caption_calls": 2})
        assert fake_ffmpeg.downloads == [] and gateway.captions == []
        caption = result.experiment["caption"]
        assert caption["skipped"] == (
            "the clip has 3 windows, more than the caption-call cap of 2 allows (max_caption_calls)"
        )
        assert caption["calls"] == 0 and caption["cap"] == 2
        assert len(gateway.p1[0]["messages"]) == 1 and result.provenance["flags"]["chunking"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_a_retry_beyond_the_cap_is_refused_counted_and_skips_the_pass(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        gateway = Gateway(statuses={0: [429]})
        result, _ = await _analyze(gateway, **{**INLINE, "max_caption_calls": 3})
        assert len(gateway.captions) == 3, "three attempts were sent; the fourth (window 0's retry) was refused"
        caption = result.experiment["caption"]
        assert caption["calls"] == 3 and caption["refused_by_cap"] == 1 and caption["cap_reached"] is True
        assert caption["skipped"].startswith("caption-call cap of 3 reached (max_caption_calls) at window ")
        stage = result.provenance["stages"]["caption"]
        assert stage["calls"] == 3 and stage["failed"] == 1 and stage["refused"] == 1
        assert len(gateway.p1[0]["messages"]) == 1 and result.provenance["flags"]["chunking"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_a_short_clip_gets_no_caption_pass_as_in_production(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, no_r2: None
    ):
        gateway = Gateway()
        result, _ = await _analyze(gateway, **INLINE, video_duration_seconds=30.0)
        assert result.experiment["caption"]["skipped"] == (
            "clip is 30.0 s, at or under INCIDENT_CHUNK_MIN_CLIP_SECONDS (30 s)"
        )
        assert result.provenance["flags"]["chunking"]["enabled"] is False, "the flag says whether the pass ran"
        assert gateway.captions == [] and len(gateway.p1[0]["messages"]) == 1


class TestR2Captions:
    """``experiment_captions: "r2"``: only under the approved prefix, each window deleted right after its call."""

    @pytest.mark.asyncio
    async def test_without_the_approved_prefix_the_request_is_refused_before_any_call(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, fake_r2: FakeR2
    ):
        gateway = Gateway(fake_r2)
        with pytest.raises(ExperimentRefusedError, match=EXPERIMENT_SCRATCH_PREFIX_ENV):
            await _analyze(gateway, experiment_captions="r2", max_caption_calls=9, skip_crops=True)
        assert gateway.p1 == [] and gateway.captions == [] and fake_ffmpeg.downloads == []
        assert fake_r2.events == [], "nothing was written to R2"

    @pytest.mark.asyncio
    async def test_windows_live_only_under_the_approved_prefix_and_each_is_deleted_after_its_call(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, fake_r2: FakeR2, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, "scratch/eval")
        gateway = Gateway(fake_r2)
        result, _ = await _analyze(gateway, experiment_captions="r2", max_caption_calls=9, skip_crops=True)
        puts = [key for event, key in fake_r2.events if event == "put"]
        assert len(puts) == 3
        assert all(re.fullmatch(r"scratch/eval/v1/exp-[0-9a-f]{12}/[0-2]\.mp4", key) for key in puts), puts
        for key in puts:
            assert fake_r2.events.index(("call", key)) < fake_r2.events.index(("delete_one", key))
        assert fake_r2.objects == {} and fake_r2.deleted == [], "nothing was left for the end-of-pass delete"
        assert all(key.startswith("scratch/eval/") for _, key in fake_r2.events)
        problem = _golden_problem(gateway.p1[0])
        assert problem is None, problem
        caption = result.experiment["caption"]
        assert caption["store"] == "r2" and caption["scratch_root"] == "scratch/eval" and caption["skipped"] is None
        assert result.provenance["flags"]["chunking"]["enabled"] is True and result.revision_id is None


class TestProductionUnchanged:
    @pytest.mark.asyncio
    async def test_a_production_request_keeps_its_store_and_sends_the_same_p1_as_the_experiment(
        self, experiment_agent: None, fake_ffmpeg: SimpleNamespace, fake_r2: FakeR2, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("INCIDENT_CHUNKING_ENABLED", "1")
        monkeypatch.setenv(EXPERIMENT_SCRATCH_PREFIX_ENV, "scratch/eval")  # set, and never used by production
        production = Gateway(fake_r2)
        result, persist = await _analyze(production, model_run_id="run1")
        puts = [key for event, key in fake_r2.events if event == "put"]
        # Windows upload concurrently (asyncio.to_thread), so the put order can vary under load; the set is fixed.
        assert sorted(puts) == [f"scratch/analyze/v1/run1/{i}.mp4" for i in range(3)]
        assert all(event != "delete_one" for event, _ in fake_r2.events), "production deletes once, at the end"
        assert sorted(fake_r2.deleted) == puts and fake_r2.objects == {}
        assert result.experiment is None and result.revision_id == 7
        persist.assert_awaited_once()
        flags = result.provenance["flags"]
        assert (
            flags["chunking"]
            == {"enabled": True, "ran": True, "setting_enabled": True, "skipped": None, **CHUNKING_DEFAULTS}
            and flags["experiment"] is False
        )
        assert result.provenance["stages"]["caption"]["prompts"] == {
            prompt_id: incident_contract.prompt_sha256(prompt_id) for prompt_id in CAPTION_PROMPT_IDS
        }
        # The same captions in an inline experiment give the same P1 request, byte for byte.
        experiment = Gateway()
        with patch.object(chunk_captions, "get_r2_client", side_effect=AssertionError("R2 was reached")):
            await _analyze(experiment, **INLINE)
        assert json.dumps(experiment.p1[0]) == json.dumps(production.p1[0])
