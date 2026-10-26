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

"""Actual sibling RTVI client and actual library adapters, worker-owned only."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import httpx

from benchmarks.ingestion import caption_result
from vss.ingestion import IngestionQueue, RequestSource, ingest
from vss.ingestion.backend import _BACKEND_REGISTRY, RTVIBackend
from vss.ingestion.rtvi import _RTVITransport

from .media import file_hash
from .normal_fixture import Fixture, fingerprint, settings
from .normal_results import NormalConfiguration, NormalPeriod, SourceIdentity

SOURCE_FILES = (
    "rtvi_vlm_client.py",
    "chunk_info.py",
    "vlm_pipeline/__init__.py",
    "vlm_pipeline/vlm_types.py",
    "via_logger.py",
)


def source_identity(checkout: Path) -> SourceIdentity:
    checkout = checkout.resolve()
    root = checkout / "services/video-summarization/src"
    if not root.resolve().is_relative_to(checkout):
        raise ValueError("upstream source root escapes checkout")
    files = {}
    for name in SOURCE_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
            raise ValueError("upstream source admission")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("upstream source escapes checkout")
        files[name] = file_hash(path)
    revision = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            capture_output=True,
            check=True,
            timeout=5,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
        .stdout.decode()
        .strip()
    )
    return SourceIdentity(revision=revision, files=files)


@contextmanager
def legacy_client(checkout: Path, url: str):
    import requests

    root = (checkout / "services/video-summarization/src").resolve()
    names = [
        "chunk_info",
        "vlm_pipeline",
        "vlm_pipeline.vlm_types",
        "via_logger",
        "m8_normal_rtvi_client",
    ]
    saved = {n: sys.modules.get(n) for n in names}
    path = list(sys.path)
    env_names = ["RTVI_VLM_URL", "RTVI_HEALTH_RETRIES", "RTVI_HEALTH_RETRY_INTERVAL"]
    env = {n: os.environ.get(n) for n in env_names}
    client = None
    sessions = []
    original_factory = requests.Session

    def factory():
        session = original_factory()
        session.trust_env = False
        sessions.append(session)
        return session

    try:
        for name in names:
            sys.modules.pop(name, None)
        logger = logging.Logger("m8-null", level=logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        stub = types.ModuleType("via_logger")
        stub.logger = logger
        sys.modules["via_logger"] = stub
        sys.path.insert(0, str(root))
        for name in ("chunk_info", "vlm_pipeline", "vlm_pipeline.vlm_types"):
            module = importlib.import_module(name)
            if not Path(module.__file__).resolve().is_relative_to(root):
                raise ValueError("actual upstream import escaped checkout")
        spec = importlib.util.spec_from_file_location(
            "m8_normal_rtvi_client", root / "rtvi_vlm_client.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        os.environ.update(
            RTVI_VLM_URL=url, RTVI_HEALTH_RETRIES="1", RTVI_HEALTH_RETRY_INTERVAL="0"
        )
        module.RTVI_HEALTH_RETRIES = 1
        module.RTVI_HEALTH_TIMEOUT = 5
        # Real requests objects, configured before the constructor's first GET.
        requests.Session = factory
        try:
            client = module.RtviVlmClient(None)
        finally:
            requests.Session = original_factory
        client._m8_sessions_closed = False
        yield client
    finally:
        requests.Session = original_factory
        close_error = None
        for session in sessions:
            try:
                session.close()
            except BaseException as error:
                close_error = error
        sys.path[:] = path
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        for name, value in env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if client is not None:
            client._m8_sessions_closed = close_error is None
        if close_error is not None:
            raise close_error


def normal_arm(
    client, source: Path, config: NormalConfiguration, fixture: Fixture
) -> NormalPeriod:
    stages, raw, asset = {}, [], None
    failure = None
    cpu = os.times()
    started = time.perf_counter()
    try:
        tick = time.perf_counter()
        uploaded = client.upload_file(str(source))
        stages["upload"] = time.perf_counter() - tick
        asset = uploaded["id"]
        tick = time.perf_counter()
        for body in client.generate_captions_stream(
            file_id=asset,
            **{k: v for k, v in settings(config).items() if k != "stream"},
        ):
            raw.extend(body.get("chunk_responses", []))
            if len(raw) > 64:
                raise ValueError("response count limit")
        stages["captions"] = time.perf_counter() - tick
    except Exception:
        failure = "execution"
    finally:
        if asset is not None:
            tick = time.perf_counter()
            try:
                client.delete_file(asset)
            except Exception:
                failure = "cleanup"
            stages["delete"] = time.perf_counter() - tick
    wall = time.perf_counter() - started
    end = os.times()
    wire = fixture.witness(asset) if asset else None
    chunks = ()
    try:
        chunks = tuple(fingerprint(p) for p in raw)
        if chunks != fixture.expected:
            failure = failure or "correctness"
    except Exception:
        failure = failure or "correctness"
    if wire is None or not wire.valid or not wire.done or not wire.sent_complete:
        failure = failure or "protocol"
    return NormalPeriod(
        arm="normal_vss",
        status="error" if failure else "ok",
        failure=failure,
        wall_s=wall,
        cpu_s=max(0, end.user + end.system - cpu.user - cpu.system),
        stages=stages,
        chunks=chunks,
        wire=wire,
    )


class AuditedTransport(_RTVITransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stages = {}
        self.asset = None

    async def upload_video(self, path):
        tick = time.perf_counter()
        try:
            source = await super().upload_video(path)
            self.asset = source.source_id
            return source
        finally:
            self.stages["upload"] = time.perf_counter() - tick

    async def generate_captions_stream(self, **kwargs):
        tick = time.perf_counter()
        try:
            async for body in super().generate_captions_stream(**kwargs):
                yield body
        finally:
            self.stages["captions"] = time.perf_counter() - tick

    async def delete_video(self, source, **kwargs):
        tick = time.perf_counter()
        try:
            return await super().delete_video(source, **kwargs)
        finally:
            self.stages["delete"] = time.perf_counter() - tick


async def library_arm(
    transport: AuditedTransport,
    source: Path,
    config: NormalConfiguration,
    fixture: Fixture,
) -> NormalPeriod:
    transport.stages = {}
    transport.asset = None
    queue = IngestionQueue(max_concurrency=1, max_pending=1)
    old = _BACKEND_REGISTRY.get("m8_normal_comparison")

    class Backend(RTVIBackend):
        def __init__(self):
            super().__init__(transport=transport)

    _BACKEND_REGISTRY["m8_normal_comparison"] = Backend
    failure, responses, job = None, [], None
    cpu, started = os.times(), time.perf_counter()
    try:
        options = {
            k: v
            for k, v in settings(config).items()
            if k
            not in {
                "prompt",
                "model",
                "stream",
                "chunk_duration",
                "chunk_overlap_duration",
            }
        }
        job = ingest(
            RequestSource.for_file(str(source)),
            backends=["m8_normal_comparison"],
            queue=queue,
            prompt=config.prompt,
            model=config.model,
            chunk_duration=30,
            chunk_overlap_duration=0,
            generation_config=options,
        )
        responses = caption_result(await job, "m8_normal_comparison")
    except Exception:
        failure = "execution"
    finally:
        try:
            if job is not None:
                await job.wait_closed()
        finally:
            if old is None:
                _BACKEND_REGISTRY.pop("m8_normal_comparison", None)
            else:
                _BACKEND_REGISTRY["m8_normal_comparison"] = old
    wall = time.perf_counter() - started
    end = os.times()
    wire = fixture.witness(transport.asset) if transport.asset else None
    chunks = ()
    try:
        chunks = tuple(
            fingerprint(
                {
                    "chunk_id": r.chunk.chunkIdx,
                    "start_time": str(r.chunk.start_pts / 1e9),
                    "end_time": str(r.chunk.end_pts / 1e9),
                    "content": r.vlm_response,
                    "frame_count": r.rtvi_frame_count,
                    **r.vlm_stats,
                }
            )
            for r in responses
        )
        if chunks != fixture.expected:
            failure = failure or "correctness"
    except Exception:
        failure = failure or "correctness"
    if wire is None or not wire.valid or not wire.done or not wire.sent_complete:
        failure = failure or "protocol"
    if wire and not wire.deleted:
        failure = "cleanup"
    if queue.running_count or queue.pending_count:
        failure = "cleanup"
    return NormalPeriod(
        arm="library",
        status="error" if failure else "ok",
        failure=failure,
        wall_s=wall,
        cpu_s=max(0, end.user + end.system - cpu.user - cpu.system),
        stages=transport.stages,
        chunks=chunks,
        wire=wire,
    )


async def http_client(url: str) -> tuple[httpx.AsyncClient, AuditedTransport]:
    client = httpx.AsyncClient(base_url=url, trust_env=False, timeout=5)
    transport = AuditedTransport(
        client=client,
        api_key="",
        model="m8-controlled-fixture",
        environment_prefix="M8_UNUSED",
    )
    try:
        if (
            not await transport.check_health()
            or (await transport.get_model_info()).id != "m8-controlled-fixture"
        ):
            raise ValueError("library preflight")
    except BaseException:
        await client.aclose()
        raise
    return client, transport
