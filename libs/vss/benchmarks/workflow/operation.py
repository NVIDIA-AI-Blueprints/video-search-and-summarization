# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""One actual workflow, retaining evidence without asserting equal work."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

import httpx
from openai import AsyncOpenAI

from benchmarks.e2e.config import endpoint, fingerprint, key
from benchmarks.e2e.transport import ObservingTransport, ProviderObserver
from benchmarks.e2e.witness import CaptionWitness
from benchmarks.e2e.worker import caption_identity
from benchmarks.ingestion import caption_result
from benchmarks.workflow.file_witness import FinalFileCaptionWitness
from benchmarks.workflow.modules import (
    FrameObserver,
    bounded_captions,
    capture,
    local_captions,
    replay,
    storage_identity,
)
from benchmarks.workflow.quality import capture_output
from benchmarks.workflow.relay import completion_valid
from benchmarks.workflow.sampling import prepare as prepare_sampling
from vss.ingestion import RequestSource, RTVIBackend, ingest, register_backend
from vss.ingestion.rtvi import _RTVITransport
from vss.storage import (
    ElasticsearchConfig,
    ElasticsearchStorageAdapter,
    StorageConfig,
    create_storage,
)
from vss.summarization import OpenAISummaryGenerator, summarize_video
from vss.summarization.storage import AdapterChunkStore


def absent(response, asset):
    if response.status_code != 400:
        return False
    body = response.json()
    return (
        body.get("code") == "BadParameter"
        and body.get("message") == "No such resource " + asset
    )


def final_output(body):
    choice = body["choices"][0]
    if len(body["choices"]) != 1 or choice.get("finish_reason") != "stop":
        raise ValueError("outer_completion")
    payload = json.loads(choice["message"]["content"])
    summary, events = payload["video_summary"], payload["events"]
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary.encode()) > 65536
        or not isinstance(events, list)
        or not 1 <= len(events) <= 10000
        or any(
            not isinstance(e, dict)
            or not isinstance(e.get("description"), str)
            or not e["description"].strip()
            for e in events
        )
    ):
        raise ValueError("final_output_shape")
    # Aggregated events are not caption windows and need not equal chunk count.
    return {
        "summary_sha256": fingerprint(summary),
        "events_sha256": fingerprint(events),
        "event_count": len(events),
        "semantic_quality": "not_reviewed",
    }


async def library_summary(
    profile, asset, rtvi, llm, store, result, state, expected, witness
):
    tick = time.perf_counter()
    if profile.captioning == "replay":
        chunks = state["replay_chunks"]
    elif profile.captioning in {"cpu_vlm", "gpu_vlm"}:
        chunks = await local_captions(
            profile, asset, state["frame_http"], state["frame_observer"], state
        )
        state["frame_observer"].validate(expected)
    else:
        chunks = await rtvi_captions(profile, asset, rtvi, state)
    if profile.captioning != "replay":
        result["stages_s"]["captions"] = time.perf_counter() - tick
    bounded_captions(chunks, profile, expected)
    if profile.captioning == "rtvi" and isinstance(witness, FinalFileCaptionWitness):
        witness.validate_normalized(chunks)
    if profile.caption_capture is not None:
        tick = time.perf_counter()
        result["private_caption_capture"] = capture(chunks, profile, expected)
        result["stages_s"]["caption_capture"] = time.perf_counter() - tick
    return await stored_summary(profile, asset, llm, store, result, chunks)


async def rtvi_captions(profile, asset, rtvi, state):
    transport = _RTVITransport(client=rtvi, model=profile.vlm_model)

    class ConfiguredRTVI(RTVIBackend):
        def __init__(self):
            super().__init__(transport=transport)

    register_backend("workflow_rtvi_diagnostic", ConfiguredRTVI)
    state["job"] = ingest(
        RequestSource(source_id=asset, camera_id=""),
        backends=["workflow_rtvi_diagnostic"],
        model=profile.vlm_model,
        prompt=profile.caption_prompt,
        chunk_duration=profile.chunk_duration_s,
        chunk_overlap_duration=0,
        generation_config=profile.caption_settings(),
    )
    chunks = caption_result(await state["job"], "workflow_rtvi_diagnostic")
    await state["job"].wait_closed()
    return chunks


async def stored_summary(profile, asset, llm, store, result, chunks):
    result["caption_count"] = len(chunks)
    identity = fingerprint(caption_identity(chunks))
    full_identity = fingerprint(storage_identity(chunks))
    tick = time.perf_counter()
    await store.store_chunks(asset, chunks)
    result["stages_s"]["store_write"] = time.perf_counter() - tick
    tick = time.perf_counter()
    readback = await store.get_chunks(asset)
    result["stages_s"]["store_read"] = time.perf_counter() - tick
    if fingerprint(storage_identity(readback)) != full_identity:
        raise ValueError("store_round_trip")
    result["caption_sha256"] = identity
    result["stored_input_sha256"] = full_identity
    sdk = AsyncOpenAI(
        base_url=llm.base_url,
        api_key=key(profile.llm_key_env),
        http_client=llm,
        max_retries=0,
        timeout=profile.work_timeout_s,
    )
    generator = OpenAISummaryGenerator(
        client=sdk,
        model=profile.llm_model,
        api_type="chat_completions",
        max_output_tokens=profile.summary_max_tokens,
    )
    tick = time.perf_counter()
    try:
        response = await summarize_video(
            readback,
            generator=generator,
            model=profile.llm_model,
            prompt=profile.summary_prompt,
        )
    finally:
        # The SDK uses a caller-owned HTTP client; closing here is safe after work.
        await sdk.close()
    result["stages_s"]["aggregate_and_summary"] = time.perf_counter() - tick
    if response.usage.summary_requests != 1:
        raise ValueError("library_summary_multiplicity")
    return response.model_dump(mode="json")


class SummaryObserver(ProviderObserver):
    def request(self, request):
        super().request(request)
        body = json.loads(request.content)
        self.inputs[-1]["generation"] = {
            k: v for k, v in body.items() if k not in {"messages", "model"}
        }


class WorkflowTransport(ObservingTransport):
    def __init__(self, *args, generation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.generation = generation

    async def handle_async_request(self, request):
        if self.generation is not None and request.url.path.endswith(
            "/chat/completions"
        ):
            body = json.loads(request.content)
            body.update(self.generation)
            raw = json.dumps(body, allow_nan=False).encode()
            headers = dict(request.headers)
            headers["content-length"] = str(len(raw))
            request = httpx.Request(
                request.method,
                request.url,
                headers=headers,
                content=raw,
                extensions=request.extensions,
            )

        if request.url.path.endswith("/generate_captions") and isinstance(
            self.witness, FinalFileCaptionWitness
        ):
            self.witness.bind_request(request)
        return await super().handle_async_request(request)


def client(
    url,
    timeout,
    *,
    witness=None,
    model="unused",
    observer=None,
    api_key=None,
    generation=None,
):
    return httpx.AsyncClient(
        base_url=url,
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + key(api_key)} if api_key else {},
        transport=WorkflowTransport(
            observer or ProviderObserver(model), witness, generation=generation
        ),
    )


async def operation(profile, expected_chunks, *, store_factory=None, admission=None):
    # This CLI runs in a dedicated worker. No checkpoint selection is inherited.
    for name in tuple(os.environ):
        if name.startswith("VSS_STORAGE_"):
            del os.environ[name]
    required = {"llm_url"} if profile.arm == "library" else {"normal_url"}
    if profile.captioning == "rtvi":
        required.add("rtvi_url")
    if profile.arm == "library" and profile.store_backend == "elasticsearch":
        required.add("es_url")
    if profile.captioning in {"cpu_vlm", "gpu_vlm"}:
        required.add("frame_url" if profile.frame_url_env else "rtvi_url")
    if profile.relay_control_env:
        required.update({"relay_control", "llm_url"})
    urls = {name: endpoint(getattr(profile, name + "_env")) for name in required}
    # Unused clients are never contacted; keep legacy fixture client topology.
    unused = "http://127.0.0.1:1"
    for name in ("normal_url", "rtvi_url", "llm_url", "es_url"):
        urls.setdefault(name, unused)
    asset, index = str(uuid4()), "m8-workflow-" + uuid4().hex
    witness = (
        FinalFileCaptionWitness(profile, expected_chunks, asset)
        if profile.caption_contract == "final_file_chunk_set.v1"
        else CaptionWitness(profile.vlm_model, profile.duration_s)
    )
    state = {"job": None, "upload_intent": False, "index_owned": False}
    frame_observer = FrameObserver(profile)
    summary_observer = SummaryObserver(profile.llm_model)
    result = {
        "status": "failed",
        "arm": profile.arm,
        "requested_caption_contract": profile.caption_contract,
        "modules": {"captioning": profile.captioning, "storage": profile.store_backend},
        "timing_boundary": "upload_to_result"
        if profile.captioning == "rtvi"
        else "store_to_summary"
        if profile.captioning == "replay"
        else "local_video_to_result",
        "native_caption_completion": "unqualified",
        "stages_s": {},
        "failure": None,
        "qualified": False,
        "comparative_statistics": None,
        "raw_normal_caption_coverage": "unavailable",
        "execution_mode": "external_one_shot_witness"
        if profile.relay_control_env
        else "direct",
        "summary_control_intervention": "benchmark_transport_overlay"
        if profile.summary_generation is not None
        else "native_adapter_defaults",
        "normal_effective_model_controls": "unverified_preconfigured_service",
        "operational_settlement": "not_started",
        "rtvi_query_asset_binding": "unqualified",
        "remote_inference_settlement": "not_proven",
        "normal_result_retention": "frontend_ES_state_retained_after_asset_cleanup",
        "resolved_endpoint_sha256": {k: fingerprint(v) for k, v in urls.items()},
        "cleanup": {},
    }
    http_cleanup_deadline = None

    @asynccontextmanager
    async def managed_http_client(*args, **kwargs):
        context = client(*args, **kwargs)
        value = await context.__aenter__()
        try:
            yield value
        finally:
            # AsyncExitStack continues after a cancelled exit. Give every exit
            # its own timeout, all sharing the same aggregate cleanup deadline.
            remaining = (
                profile.cleanup_timeout_s
                if http_cleanup_deadline is None
                else max(0, http_cleanup_deadline - time.monotonic())
            )
            async with asyncio.timeout(remaining):
                await context.__aexit__(None, None, None)

    async with AsyncExitStack() as stack:
        es, storage, file_root = None, None, None
        control = None
        started = None
        phase = "client_setup"
        try:
            rtvi = await stack.enter_async_context(
                managed_http_client(
                    urls["rtvi_url"],
                    profile.work_timeout_s,
                    witness=witness,
                    api_key=profile.rtvi_key_env
                    if profile.captioning == "rtvi"
                    else None,
                )
            )
            state["frame_http"] = await stack.enter_async_context(
                managed_http_client(
                    urls.get("frame_url", urls["rtvi_url"]),
                    profile.work_timeout_s,
                    observer=frame_observer,
                    api_key=profile.frame_key_env
                    if profile.captioning in {"cpu_vlm", "gpu_vlm"}
                    else None,
                )
            )
            state["frame_observer"] = frame_observer
            normal = await stack.enter_async_context(
                managed_http_client(
                    urls["normal_url"],
                    profile.work_timeout_s,
                    api_key=profile.normal_key_env if profile.arm == "normal" else None,
                )
            )
            llm = await stack.enter_async_context(
                managed_http_client(
                    urls["llm_url"],
                    profile.work_timeout_s,
                    model=profile.llm_model,
                    observer=summary_observer,
                    api_key=profile.llm_key_env if profile.arm == "library" else None,
                    generation=profile.summary_generation,
                )
            )
            if profile.relay_control_env:
                control = await stack.enter_async_context(
                    managed_http_client(
                        urls["relay_control"], profile.cleanup_timeout_s
                    )
                )
            phase = "sampling_admission"
            state["local_pipeline"], result["sampling"] = prepare_sampling(
                profile, admission
            )
            phase = "relay_admission"
            if control is not None:
                initial = await control.get("")
                initial.raise_for_status()
                snapshot = initial.json()
                if (
                    snapshot["sealed"]
                    or snapshot["admitted"]
                    or snapshot["active"]
                    or snapshot["rejected"]
                    or snapshot["dropped"]
                    or not all(
                        completion_valid(record) for record in snapshot["records"]
                    )
                ):
                    raise ValueError("relay_not_fresh")
                # Deployment must bind this one-shot route to a fresh owned frontend.
                token = str(llm.base_url).rstrip("/").split("/")[-2]
                import hashlib

                if (
                    hashlib.sha256(token.encode()).hexdigest()
                    != snapshot["route_sha256"]
                ):
                    raise ValueError("relay_route_binding")
            if profile.captioning == "rtvi" and not absent(
                await rtvi.get("/v1/files/" + asset), asset
            ):
                raise ValueError("asset_not_absent")
            if profile.captioning == "replay":
                phase = "replay_admission"
                state["replay_chunks"] = replay(profile, expected_chunks)
                result["caption_input_sha256"] = profile.caption_input_sha256
            if profile.arm == "library":
                setup = time.perf_counter()
                if store_factory:
                    storage = store_factory()
                elif profile.store_backend in {"memory", "file"}:
                    if profile.store_backend == "file":
                        file_root = tempfile.mkdtemp(prefix="m8-owned-store-")
                    storage = create_storage(
                        StorageConfig(backend=profile.store_backend, root=file_root)
                    )
                else:
                    from elasticsearch import AsyncElasticsearch

                    es = AsyncElasticsearch(
                        urls["es_url"],
                        api_key=key(profile.es_key_env) if profile.es_key_env else None,
                        max_retries=0,
                        request_timeout=profile.cleanup_timeout_s,
                    )
                    if await es.indices.exists(index=index):
                        raise ValueError("index_not_owned")
                    state["index_owned"] = True
                    storage = ElasticsearchStorageAdapter(
                        config=ElasticsearchConfig(
                            url=urls["es_url"],
                            index=index,
                            claim_root="/tmp/m8-workflow-claims",
                        ),
                        client=es,
                    )
                result["stages_s"]["storage_setup_untimed"] = (
                    time.perf_counter() - setup
                )
            if control is not None:
                admitted = await control.post(
                    str(control.base_url).rstrip("/") + "/arm"
                )
                admitted.raise_for_status()
                if not admitted.json()["admitted"]:
                    raise ValueError("relay_admission")
            from benchmarks.workflow.barrier import wait as wait_for_batch

            phase = "batch_barrier"
            result["batch_barrier"] = await wait_for_batch(profile)
            started = time.perf_counter()
            result["operation_started_monotonic_s"] = time.monotonic()
            async with asyncio.timeout(profile.work_timeout_s):
                if profile.captioning == "rtvi":
                    phase = "upload"
                    state["upload_intent"] = True
                    with profile.video.open("rb") as stream:
                        response = await rtvi.post(
                            "/v1/files",
                            data={
                                "id": asset,
                                "purpose": "vision",
                                "media_type": "video",
                            },
                            files={"file": (asset + ".mp4", stream, "video/mp4")},
                        )
                    response.raise_for_status()
                    if response.json().get("id") != asset:
                        raise ValueError("upload_identity")
                    metadata = await rtvi.get("/v1/files/" + asset)
                    metadata.raise_for_status()
                    if metadata.json().get("id") != asset:
                        raise ValueError("lookup_identity")
                    result["upload_identity_verified"] = True
                    result["stages_s"]["upload_and_lookup"] = (
                        time.perf_counter() - started
                    )
                if profile.arm == "normal":
                    phase = "normal_service"
                    tick = time.perf_counter()
                    request = {
                        "id": asset,
                        "model": profile.vlm_model,
                        "prompt": profile.caption_prompt,
                        "scenario": profile.scenario,
                        "events": profile.events,
                        "stream": False,
                        "chunk_duration": profile.chunk_duration_s,
                        "chunk_overlap_duration": 0,
                        "enable_qa": False,
                        **profile.caption_settings(),
                    }
                    response = await normal.post("/v1/summarize", json=request)
                    result["outer_http_status"] = response.status_code
                    response.raise_for_status()
                    body = response.json()
                    result["stages_s"]["normal_service"] = time.perf_counter() - tick
                    count = body.get("usage", {}).get("total_chunks_processed")
                    result["service_reported_chunk_count"] = count
                    if type(count) is not int or count != expected_chunks:
                        raise ValueError("service_reported_chunk_count")
                else:
                    phase = "library_workflow"
                    body = await library_summary(
                        profile,
                        asset,
                        rtvi,
                        llm,
                        AdapterChunkStore(storage),
                        result,
                        state,
                        expected_chunks,
                        witness,
                    )
                    if profile.captioning == "rtvi":
                        result["caption_witness"] = witness.finish()
                    if len(summary_observer.completions) != 1 or any(
                        c.get("http_status") != 200
                        or not c.get("model_matches")
                        or c.get("finish_reason") != "stop"
                        or not c.get("usage_available")
                        or c["output_tokens"] > profile.summary_max_tokens
                        for c in summary_observer.completions
                    ):
                        raise ValueError("summary_provider_completion")
                result["workflow_to_result_s"] = time.perf_counter() - started
                result["result_monotonic_s"] = time.monotonic()
                if profile.captioning == "rtvi":
                    result["upload_to_result_s"] = result["workflow_to_result_s"]
                phase = "final_output"
                result.update(final_output(body))
                if profile.output_capture is not None:
                    phase = "private_output_capture"
                    result["private_output_capture"] = capture_output(body, profile)
                result["outer_response_completed"] = True
                result["status"] = "completed_diagnostic"
                result["operational_settlement"] = "outer_completed_cleanup_pending"
        except (Exception, asyncio.CancelledError) as error:
            result["operational_settlement"] = "interrupted_remote_work_unknown"
            result["failure"] = type(error).__name__
            result["failure_stage"] = phase
        finally:
            cleanup_started = time.perf_counter()

            async def clean(name, action):
                try:
                    async with asyncio.timeout(profile.cleanup_timeout_s):
                        await action()
                    result["cleanup"][name] = True
                except (Exception, asyncio.CancelledError) as error:
                    result["cleanup"][name] = False
                    result.setdefault("cleanup_failures", {})[name] = type(
                        error
                    ).__name__

            if state["job"] is not None:
                state["job"].cancel()
                await clean("local_job_closed", state["job"].wait_closed)
            if storage:
                await clean("store_closed", storage.aclose)
            if file_root:

                async def delete_root():
                    if not result["cleanup"].get("store_closed"):
                        raise ValueError("file_store_not_drained")
                    shutil.rmtree(file_root)

                await clean("file_root_deleted", delete_root)
            if es:

                async def delete_index():
                    if state["index_owned"] and await es.indices.exists(index=index):
                        await es.indices.delete(index=index)

                await clean("library_index_deleted", delete_index)
                await clean("es_client_closed", es.close)
            if state["upload_intent"]:

                async def delete_asset():
                    for _ in range(10):
                        response = await rtvi.delete("/v1/files/" + asset)
                        if response.status_code in (200, 204) or absent(
                            response, asset
                        ):
                            if not absent(await rtvi.get("/v1/files/" + asset), asset):
                                raise ValueError("asset_absence_unconfirmed")
                            return
                        if response.status_code != 409:
                            raise ValueError("asset_cleanup_http")
                        await asyncio.sleep(0.2)
                    raise ValueError("asset_cleanup_busy")

                await clean("asset_deleted", delete_asset)

            async def seal():
                response = await control.post(
                    str(control.base_url).rstrip("/") + "/seal"
                )
                response.raise_for_status()
                result["llm_relay"] = response.json()
                if not result["llm_relay"]["complete"]:
                    raise ValueError("relay_incomplete")
                for record in result["llm_relay"]["records"]:
                    cap = (
                        profile.summary_max_tokens
                        if record["phase"] == "after_client_admission"
                        else None
                    )
                    if not completion_valid(record, cap):
                        raise ValueError("provider_completion_or_cap")

            if control is not None:
                await clean("relay_sealed_complete", seal)
            http_cleanup_deadline = time.monotonic() + profile.cleanup_timeout_s
            await clean("http_clients_closed", stack.aclose)
            if profile.arm == "library":
                result["llm_provider_observation"] = {
                    "inputs": summary_observer.inputs,
                    "completions": summary_observer.completions,
                    "scope": "direct_library_requests_only",
                }
            if result["status"] == "completed_diagnostic" and all(
                result["cleanup"].values()
            ):
                result["operational_settlement"] = (
                    "outer_completed_owned_cleanup_complete"
                )
            result["stages_s"]["cleanup"] = time.perf_counter() - cleanup_started
            if started is not None:
                result["lifecycle_s"] = time.perf_counter() - started
            if profile.arm == "library" and profile.captioning == "rtvi":
                result["caption_stream_observation"] = (
                    witness.observation()
                    if isinstance(witness, FinalFileCaptionWitness)
                    else {
                        "wire_sha256": witness.digest.hexdigest(),
                        "wire_bytes": witness.bytes,
                        "terminal_observed": witness.done,
                        "accepted_chunks": len(witness.chunks),
                    }
                )
            if profile.captioning in {"cpu_vlm", "gpu_vlm"}:
                result["frame_provider_observation"] = {
                    "selected": frame_observer.selected,
                    "inputs": frame_observer.inputs,
                    "completions": frame_observer.completions,
                    "finish_scope": "compatibility_outer_not_native",
                }
            if not all(result["cleanup"].values()):
                result["status"] = "failed"
                result["operational_settlement"] = "cleanup_unsettled"
                result["failure"] = result["failure"] or "cleanup_or_observation"
    return result
