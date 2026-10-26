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

"""One isolated Q0 operation, with transient ingestion and explicit caption storage."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
import uuid
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from benchmarks.ingestion import caption_result
from vss.ingestion import RequestSource, ingest
from vss.ingestion.vlm import OpenAICompatibleVLMBackend, register_vlm_backend
from vss.observability import collect_vss_metrics
from vss.storage import ElasticsearchConfig, StorageConfig, create_storage
from vss.summarization import OpenAISummaryGenerator, summarize_video
from vss.summarization.storage import AdapterChunkStore

from ..artifacts import aggregate
from ..media import file_hash
from ..resources import ResourceSampler
from .config import Arm, Recipe, endpoint, fingerprint, key
from .fixture import Providers
from .transport import ObservingTransport, ProviderObserver


class QualityError(ValueError):
    pass


def caption_identity(chunks):
    return [
        {
            "index": c.chunk.chunkIdx,
            "start": c.chunk.start_pts,
            "end": c.chunk.end_pts,
            "content": c.vlm_response,
        }
        for c in chunks
    ]


def validate_windows(chunks, recipe, expected_chunks=None):
    expected_chunks = expected_chunks or math.ceil(
        recipe.duration_s / recipe.chunk_duration_s
    )
    if len(chunks) != expected_chunks:
        raise QualityError("caption count")
    for index, caption in enumerate(chunks):
        c = caption.chunk
        expected_start = index * recipe.chunk_duration_s
        expected_end = min(expected_start + recipe.chunk_duration_s, recipe.duration_s)
        if (
            caption.error
            or c is None
            or c.chunkIdx != index
            or not caption.vlm_response
            or not caption.vlm_response.strip()
            or abs(c.start_pts / 1e9 - expected_start) > 0.1
            or abs(c.end_pts / 1e9 - expected_end) > 0.1
            or c.end_pts <= c.start_pts
        ):
            raise QualityError("incomplete caption window")


async def operation(
    recipe: Recipe, arm: Arm, root: Path, fixture, expected_chunks=None
):
    if arm.captioning == "rtvi":
        return {"status": "failed", "failure": "unsupported_contract"}
    # Explicit factory injection in this owned worker avoids ambient provider defaults.
    vlm_url = fixture.base + "/vlm" if fixture else endpoint(arm.vlm.base_url_env)
    llm_url = fixture.base + "/llm" if fixture else endpoint(arm.llm.base_url_env)
    vlm_key = "controlled" if fixture else key(arm.vlm.api_key_env)
    llm_key = "controlled" if fixture else key(arm.llm.api_key_env)
    es_url = endpoint(arm.es_url_env) if arm.es_url_env else None
    es_key = key(arm.es_api_key_env) if arm.es_api_key_env else None
    for name in tuple(os.environ):
        if name.startswith("VSS_STORAGE_"):
            del os.environ[name]
    vlm_observer, llm_observer = (
        ProviderObserver(arm.vlm.model),
        ProviderObserver(arm.llm.model),
    )
    stack = AsyncExitStack()
    storage = None
    es = None
    job = None
    started = None
    result = {"status": "failed", "failure": "setup", "stages_s": {}}
    try:
        vlm_http = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=vlm_url,
                transport=ObservingTransport(vlm_observer),
                timeout=recipe.request_timeout_s,
                trust_env=False,
            )
        )
        llm_http = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=ObservingTransport(llm_observer),
                timeout=recipe.request_timeout_s,
                trust_env=False,
            )
        )
        sdk = await stack.enter_async_context(
            AsyncOpenAI(
                api_key=llm_key,
                base_url=llm_url,
                http_client=llm_http,
                max_retries=0,
                timeout=recipe.request_timeout_s,
            )
        )
        generator = OpenAISummaryGenerator(
            client=sdk,
            model=arm.llm.model,
            api_type="chat_completions",
            max_output_tokens=recipe.summary_max_tokens,
        )

        class ConfiguredVLM(OpenAICompatibleVLMBackend):
            def __init__(self):
                super().__init__(
                    base_url=vlm_url,
                    model=arm.vlm.model,
                    api_key=vlm_key,
                    client=vlm_http,
                )

        register_vlm_backend("vlm", ConfiguredVLM)
        if arm.store == "elasticsearch":
            if es_key:
                os.environ["VSS_STORAGE_ES_API_KEY"] = es_key
            es = ElasticsearchConfig(
                url=es_url,
                index=arm.es_index,
                request_timeout=recipe.request_timeout_s,
            )
        config = StorageConfig(
            backend=arm.store,
            root=root / "store" if arm.store == "file" else None,
            elasticsearch=es,
            max_records=1000,
            max_snapshot_bytes=1024**2,
            lock_wait_timeout_s=min(recipe.request_timeout_s, 30),
        )
        storage = create_storage(config)
        stages = result["stages_s"]
        started = time.perf_counter()
        async with asyncio.timeout(recipe.work_timeout_s):
            tick = time.perf_counter()
            source = RequestSource.for_file(str(recipe.video))
            job = ingest(
                source,
                backends=[arm.captioning],
                model=arm.vlm.model,
                prompt=recipe.caption_prompt,
                chunk_duration=recipe.chunk_duration_s,
                generation_config={
                    "max_tokens": recipe.caption_max_tokens,
                    "temperature": 0,
                },
            )
            chunks = caption_result(await job, arm.captioning)
            await job.wait_closed()
            stages["captions"] = time.perf_counter() - tick
            if isinstance(chunks, list) and len(chunks) <= 64:
                result["caption_windows"] = [
                    {
                        "index": c.chunk.chunkIdx,
                        "start_ns": c.chunk.start_pts,
                        "end_ns": c.chunk.end_pts,
                        "frames": c.rtvi_frame_count,
                        "error": bool(c.error),
                    }
                    for c in chunks
                    if c.chunk is not None
                ]
            if not isinstance(chunks, list) or not 1 <= len(chunks) <= 64:
                raise QualityError("caption count")
            validate_windows(chunks, recipe, expected_chunks)
            before = fingerprint(caption_identity(chunks))
            store = AdapterChunkStore(storage)
            identity = str(uuid.uuid4())
            tick = time.perf_counter()
            await store.store_chunks(identity, chunks)
            stages["store_write"] = time.perf_counter() - tick
            tick = time.perf_counter()
            if arm.store == "file":
                # Fresh-adapter readback does not establish restart durability.
                await storage.aclose()
                storage = create_storage(config)
                store = AdapterChunkStore(storage)
            readback = await store.get_chunks(identity)
            stages["store_read"] = time.perf_counter() - tick
            if fingerprint(caption_identity(readback)) != before:
                raise QualityError("storage round trip")
            tick = time.perf_counter()
            response = await summarize_video(
                readback,
                generator=generator,
                model=arm.llm.model,
                prompt=recipe.summary_prompt,
            )
            stages["aggregate_and_summary"] = time.perf_counter() - tick
            result["result_ready_s"] = time.perf_counter() - started
            payload = json.loads(response.choices[0].message.content)
            summary = payload["video_summary"]
            if (
                response.usage.summary_requests != 1
                or payload["total_events"] < 1
                or not summary
                or not all(
                    t.casefold() in summary.casefold()
                    for t in recipe.required_summary_terms
                )
            ):
                raise QualityError("summary adequacy")
            completions = vlm_observer.completions + llm_observer.completions
            expected = len(chunks) + 1
            if len(completions) != expected or any(
                c["http_status"] != 200
                or not c["model_matches"]
                or c["finish_reason"] != "stop"
                or not c["usage_available"]
                for c in completions
            ):
                raise QualityError("provider completion/usage")
            if any(not 1 <= c["image_count"] <= 8 for c in vlm_observer.inputs):
                raise QualityError("frame input count")
            if any(
                c["output_tokens"] > cap
                for observer, cap in (
                    (vlm_observer, recipe.caption_max_tokens),
                    (llm_observer, recipe.summary_max_tokens),
                )
                for c in observer.completions
            ):
                raise QualityError("provider output budget")
            result.update(
                status="ok",
                failure=None,
                captions_sha256=before,
                summary_sha256=fingerprint(summary),
                chunks=len(chunks),
                frames=sum(c.rtvi_frame_count for c in chunks),
                events=payload["total_events"],
            )
    except TimeoutError:
        result.update(status="failed", failure="work_timeout")
    except QualityError:
        result.update(status="failed", failure="quality")
    except asyncio.CancelledError:
        result.update(status="failed", failure="cancelled")
    except Exception:
        result.update(status="failed", failure="provider_or_storage")
    finally:
        tick = time.perf_counter()
        try:
            async with asyncio.timeout(recipe.cleanup_timeout_s):
                try:
                    if job is not None:
                        if not job.done():
                            job.cancel()
                        await job.wait_closed()
                    if storage is not None:
                        await storage.aclose()
                finally:
                    await stack.aclose()
        except BaseException:
            result.update(status="failed", failure="cleanup")
        result["stages_s"]["cleanup"] = time.perf_counter() - tick
        if started is not None:
            result["lifecycle_s"] = time.perf_counter() - started
        result["providers"] = {
            "vlm": vlm_observer.completions,
            "llm": llm_observer.completions,
        }
        result["provider_inputs"] = {
            "vlm": vlm_observer.inputs,
            "llm": llm_observer.inputs,
        }
        result["resolved_endpoint_sha256"] = {
            "vlm": fingerprint(vlm_url)
            if not fixture
            else fingerprint("owned-loopback.v1/vlm"),
            "llm": fingerprint(llm_url)
            if not fixture
            else fingerprint("owned-loopback.v1/llm"),
            "es": fingerprint([es.url, es.index]) if es else None,
        }
    return result


def run_worker(recipe: Recipe, arm: Arm, expected_chunks=None) -> dict:
    if file_hash(recipe.video) != recipe.video_sha256:
        return {"status": "failed", "failure": "source_changed"}
    fixture_context = (
        Providers()
        if recipe.evidence_kind == "controlled_wiring"
        else nullcontext(None)
    )
    with (
        tempfile.TemporaryDirectory(prefix="m8-q0-") as directory,
        fixture_context as fixture,
    ):
        with (
            ResourceSampler(0.05) as sampler,
            collect_vss_metrics(max_events=100000) as collector,
        ):
            result = asyncio.run(
                operation(recipe, arm, Path(directory), fixture, expected_chunks)
            )
        result["resources"] = sampler.trace.model_dump(mode="json")
        result["metrics"] = {
            k: v.model_dump() for k, v in aggregate(collector.observations).items()
        }
        result["collection_complete"] = collector.complete
        result["collection_dropped_count"] = collector.dropped_count
        if not collector.complete or not sampler.trace.complete:
            if result["status"] == "ok":
                result.update(status="failed", failure="incomplete_observation")
    if file_hash(recipe.video) != recipe.video_sha256:
        result.update(status="failed", failure="source_changed")
    return result
