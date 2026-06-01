# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
import time
import uuid
from datetime import datetime

import pytest
from sseclient import SSEClient

from tests.tests_common import TempEnv, ViaTestServer

VILA_MODEL_PATH = "/opt/models/vila-yi-34b-siglip-stage3_1003_video_v8"

API_PREFIX = "/v1"


def timestamp_validator(v: str):
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.mark.timeout(360)
def test_file_summarization(milvus_server_port, temp_asset_dir):
    with TempEnv({"VIA_DEV_API": "1"}), ViaTestServer(
        f"--model-path {VILA_MODEL_PATH} --vlm-model-type vila-1.5"
        f" --asset-dir {temp_asset_dir}"
        f" --milvus-db-port {milvus_server_port}",
        48000,
    ) as t:
        with open(
            "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4",
            "rb",
        ) as video_file:
            response = t.post(
                f"{API_PREFIX}/files",
                files={
                    "file": video_file,
                    "purpose": (None, "vision"),
                    "media_type": (None, "video"),
                },
            )
        assert response.status_code == 200
        file_id = response.json()["id"]

        model = t.get("/models").json()["data"][0]["id"]
        t1 = time.time()
        time.sleep(1)
        response = t.post(
            f"{API_PREFIX}/generate_captions",
            json={
                "id": file_id,
                "model": model,
                "chunk_duration": 10,
                "chunk_overlap_duration": 2,
                "prompt": "Describe the video in detail",
                "caption_summarization_prompt": "Extract useful information from following text",
                "summary_aggregation_prompt": (
                    "Aggregate all the text together while retaining as much information"
                    " as possible. Mention word aggregate in the response"
                ),
                "stream_options": {"include_usage": True},
                "summarize_batch_size": 4,
                "rag_type": "graph-rag",
                "rag_top_k": 7,
                "rag_batch_size": 3,
                "enable_chat": True,
                "summarize_top_p": 0.5,
                "summarize_temperature": 0.3,
                "summarize_max_tokens": 1000,
                "chat_top_p": 0.4,
                "chat_temperature": 0.2,
                "chat_max_tokens": 2000,
                "notification_top_p": 0.25,
                "notification_temperature": 0.4,
                "notification_max_tokens": 2345,
            },
        )
        time.sleep(1)
        t2 = time.time()
        assert response.status_code == 200

        resp_json = response.json()
        assert uuid.UUID(resp_json.pop("id"))
        assert resp_json["created"] < t2 and resp_json["created"] > t1
        resp_json.pop("created")
        assert resp_json["usage"].pop("query_processing_time") > 0

        keywords = ["car", "aggregat", "pedestrian"]
        content = resp_json["choices"][0]["message"].pop("content").lower()
        for kw in keywords:
            assert kw in content
        assert resp_json == {
            "model": model,
            "object": "summarization.completion",
            "media_info": {
                "type": "offset",
                "start_offset": 0,
                "end_offset": 48,
            },
            "choices": (
                [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {"role": "assistant", "tool_calls": []},
                    }
                ]
            ),
            "usage": {
                "total_chunks_processed": 6,
            },
        }


@pytest.mark.timeout(360)
def test_live_stream_summarization(milvus_server_port, temp_asset_dir):
    with TempEnv({"VIA_DEV_API": "1"}), ViaTestServer(
        f"--model-path {VILA_MODEL_PATH} --vlm-model-type vila-1.5"
        f" --asset-dir {temp_asset_dir}"
        f" --milvus-db-port {milvus_server_port}",
        48000,
    ) as t:

        response = t.post(
            f"{API_PREFIX}/streams/add",
            json={
                "streams": [
                    {
                        "liveStreamUrl": "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/sample_1080p_h264.mp4",
                        "description": "",
                    }
                ]
            },
        )
        assert response.status_code == 200
        result_json = response.json()
        assert "results" in result_json and len(result_json["results"]) > 0
        assert len(result_json.get("errors", [])) == 0
        file_id = result_json["results"][0]["id"]

        model = t.get(f"{API_PREFIX}/models").json()["data"][0]["id"]

        t1 = time.time()
        time.sleep(1)
        response = t.post(
            f"{API_PREFIX}/generate_captions",
            json={
                "id": file_id,
                "model": model,
                "chunk_duration": 10,
                "summary_duration": 60,
                "prompt": "Describe the video in detail",
                "caption_summarization_prompt": "Extract useful information from following text",
                "summary_aggregation_prompt": (
                    "Aggregate all the text together while retaining as much information as"
                    " possible. Mention word aggregate in the response"
                ),
                "stream_options": {"include_usage": True},
                "stream": True,
                "summarize_batch_size": 4,
                "rag_type": "graph-rag",
                "rag_top_k": 7,
                "rag_batch_size": 3,
                "enable_chat": True,
                "summarize_top_p": 0.5,
                "summarize_temperature": 0.3,
                "summarize_max_tokens": 1000,
                "chat_top_p": 0.4,
                "chat_temperature": 0.2,
                "chat_max_tokens": 2000,
                "notification_top_p": 0.25,
                "notification_temperature": 0.4,
                "notification_max_tokens": 2345,
            },
            stream=True,
        )
        time.sleep(1)
        t2 = time.time()
        assert response.status_code == 200

        got_done = False
        got_usage = False
        got_summary = False

        for event in SSEClient(response).events():
            data = event.data.strip()
            if data == "[DONE]":
                assert got_usage
                assert got_summary
                got_done = True
                break

            resp_json = json.loads(data)
            assert uuid.UUID(resp_json.pop("id"))
            assert resp_json["created"] < t2 and resp_json["created"] > t1
            resp_json.pop("created")

            if resp_json["choices"]:
                got_summary = True
                keywords = ["car", "aggregat", "pedestrian"]
                content = resp_json["choices"][0]["message"].pop("content").lower()
                for kw in keywords:
                    assert kw in content

                assert timestamp_validator(
                    resp_json["media_info"].pop("start_timestamp")
                ) < timestamp_validator(resp_json["media_info"].pop("end_timestamp"))

                assert resp_json == {
                    "model": model,
                    "object": "summarization.progressing",
                    "media_info": {
                        "type": "timestamp",
                    },
                    "choices": (
                        [
                            {
                                "finish_reason": "stop",
                                "index": 0,
                                "message": {"role": "assistant"},
                            }
                        ]
                    ),
                    "usage": None,
                }

            if resp_json["usage"]:
                assert got_summary
                got_usage = True

                assert resp_json["usage"].pop("query_processing_time") > 0
                assert resp_json == {
                    "model": model,
                    "object": "summarization.completion",
                    "media_info": None,
                    "choices": [],
                    "usage": {"total_chunks_processed": 5},
                }

        assert got_done
        assert got_usage
        assert got_summary
