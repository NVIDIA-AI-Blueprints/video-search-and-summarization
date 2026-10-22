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

"""Native frames, HTTP boundary, checkpoint restart and deferred SDK summary."""

from __future__ import annotations

import base64
import json
import os
import re

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1",
    reason="set RUN_VSS_CPU_TESTS=1 for caller-installed OpenCV acceptance",
)


@pytest.mark.parametrize("duration", [1, 31])
async def test_real_decoding_persistence_resume_and_summary(
    tmp_path, monkeypatch, duration
):
    import cv2
    import numpy as np
    from openai import AsyncOpenAI

    import vss.ingestion.vlm as vlm
    from vss.ingestion import RequestSource, VLMBackendError, ingest
    from vss.storage import FileStorageAdapter
    from vss.summarization import (
        AdapterChunkStore,
        OpenAISummaryGenerator,
        summarize_from_store,
    )

    path = tmp_path / "native.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 5, (160, 90))
    assert writer.isOpened()
    try:
        for index in range(duration * 5):
            rgb = np.zeros((90, 160, 3), dtype=np.uint8)
            rgb[:, :, index % 3] = 255
            writer.write(rgb)
    finally:
        writer.release()

    calls = []
    should_fail = duration > 1

    def caption_http(request):
        nonlocal should_fail
        body = json.loads(request.content)
        assert body["model"] == "swappable-vision-model"
        content = body["messages"][-1]["content"]
        window = re.search(r"Chunk (\d+) spans ([0-9.]+) to", content[0]["text"])
        assert window is not None
        index, start = int(window[1]), float(window[2])
        images = [item for item in content if item["type"] == "image_url"]
        assert images and len(images) <= 8
        for image in images:
            encoded = image["image_url"]["url"].split(",", 1)[1]
            pixels = cv2.imdecode(
                np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            assert pixels.shape == (90, 160, 3)
        calls.append(index)
        if index == 1 and should_fail:
            should_fail = False
            return httpx.Response(503, json={"error": "temporary failure"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "events": [
                                        {
                                            "start_time": start,
                                            "end_time": start + 0.5,
                                            "type": "color",
                                            "description": f"Window {index} changes",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    class Backend(vlm.OpenAICompatibleVLMBackend):
        def __init__(self, **kwargs):
            super().__init__(
                client=httpx.AsyncClient(transport=httpx.MockTransport(caption_http)),
                **kwargs,
            )

        async def aclose(self):
            await self._client.aclose()

    monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "vlm", Backend)
    monkeypatch.setenv("VLM_BASE_URL", "https://swappable.test/v1")
    monkeypatch.delenv("VSS_STORAGE_BACKEND", raising=False)
    source = RequestSource(url=str(path), source_id="native-original")
    root = tmp_path / "storage"
    options = dict(
        backends=["vlm"],
        decoder="opencv",
        model="swappable-vision-model",
        pipeline_revision="opencv-native-test-v1",
        chunk_duration=30,
    )
    async with FileStorageAdapter(root=root) as storage:
        job = ingest(source, storage=storage, **options)
        if duration > 1:
            with pytest.raises(VLMBackendError):
                await job
        else:
            assert len((await job)["vlm"]) == 1
        await job.wait_closed()
    async with FileStorageAdapter(root=root) as storage:
        resumed = ingest(source, storage=storage, job_id=job.id, resume=True, **options)
        chunks = (await resumed)["vlm"]
        await resumed.wait_closed()
        assert calls == ([0] if duration == 1 else [0, 1, 1])
        assert [item.chunk.chunkIdx for item in chunks] == list(range(len(chunks)))
        assert chunks[-1].chunk.is_last
        assert await AdapterChunkStore(storage).get_chunks(str(job.id)) == chunks

    path.unlink()  # Deferred summarization must need neither video nor decode.
    summary_calls = []

    def summary_http(request):
        body = json.loads(request.content)
        assert body["model"] == "swappable-summary-model"
        summary_calls.append(body)
        return httpx.Response(
            200,
            json={
                "id": "completion",
                "object": "chat.completion",
                "created": 1,
                "model": "swappable-summary-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "The colors change.",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 6,
                    "total_tokens": 11,
                },
            },
        )

    async with AsyncOpenAI(
        base_url="https://summary-swap.test/v1",
        api_key="fixture",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(summary_http)),
        max_retries=0,
    ) as sdk:
        generator = OpenAISummaryGenerator(
            client=sdk, model="swappable-summary-model", api_type="chat_completions"
        )
        async with FileStorageAdapter(root=root) as storage:
            result = await summarize_from_store(
                str(job.id), storage=storage, generator=generator
            )
    assert json.loads(result.choices[0].message.content)["video_summary"] == (
        "The colors change."
    )
    assert len(summary_calls) == 1
