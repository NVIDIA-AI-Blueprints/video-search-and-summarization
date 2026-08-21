# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import importlib.util
import io
import json
from pathlib import Path

import pytest
import torch
from PIL import Image

from common.chunk_info import ChunkInfo
from tests.tests_common import TempEnv


def _load_ce1_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "src/models/custom/samples/ce1-nim/inference.py"
    spec = importlib.util.spec_from_file_location("ce1_nim_inference", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, embeddings):
        self._body = json.dumps(
            {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": idx, "embedding": embedding}
                    for idx, embedding in enumerate(embeddings)
                ],
                "model": "nvidia/cosmos-embed1",
            }
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


@pytest.fixture()
def ce1_module():
    return _load_ce1_module()


def test_ce1_nim_text_embeddings_payload(monkeypatch, ce1_module):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout, json.loads(request.data.decode("utf-8"))))
        return _FakeResponse([[0.1, 0.2, 0.3]])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    with TempEnv(
        {
            "REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000",
            "REMOTE_EMBED_ENDPOINT_MODEL_NAME": "nvidia/cosmos-embed1-test",
            "REMOTE_EMBED_ENDPOINT_API_KEY": "secret",
            "REMOTE_EMBED_ENDPOINT_TIMEOUT_SEC": "12",
        }
    ):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunk = ChunkInfo()
        chunk.chunk_type = "text"
        chunk.text_input = "a forklift moving pallets"

        outputs = model.generate("dummy", [chunk])

    assert outputs[0].embeddings == [0.1, 0.2, 0.3]
    request, timeout, payload = requests[0]
    assert request.full_url == "http://ce1-nim:8000/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer secret"
    assert timeout == 12
    assert payload == {
        "input": "a forklift moving pallets",
        "request_type": "query",
        "encoding_format": "float",
        "model": "nvidia/cosmos-embed1-test",
    }


def test_ce1_nim_text_embeddings_bulk_batches(monkeypatch, ce1_module):
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return _FakeResponse([[float(idx)] for idx, _ in enumerate(payload["input"])])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    with TempEnv({"REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000", "REMOTE_EMBED_ENDPOINT_BATCH_SIZE": "2"}):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunks = []
        for text in ["one", "two", "three"]:
            chunk = ChunkInfo()
            chunk.chunk_type = "text"
            chunk.text_input = text
            chunks.append(chunk)

        outputs = model.generate("dummy", chunks)

    assert [output.embeddings for output in outputs] == [[0.0], [1.0], [0.0]]
    assert [payload["request_type"] for payload in payloads] == ["bulk_text", "bulk_text"]
    assert [payload["input"] for payload in payloads] == [["one", "two"], ["three"]]


def test_ce1_nim_video_embeddings_use_eight_jpeg_frames(monkeypatch, ce1_module):
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return _FakeResponse([[0.4, 0.5]])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    with TempEnv({"REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000"}):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunk = ChunkInfo()
        chunk.chunk_type = "video"
        frames = torch.zeros((3, 4, 4, 3), dtype=torch.uint8)

        outputs = model.generate("dummy", [chunk], video_frames=[frames])

    assert outputs[0].embeddings == [0.4, 0.5]
    payload = payloads[0]
    assert payload["request_type"] == "query"
    assert payload["model"] == "nvidia/cosmos-embed1"
    nim_input = payload["input"]
    assert nim_input.startswith("data:video_frames/jpg;base64,{")
    frame_payloads = nim_input.removeprefix("data:video_frames/jpg;base64,{").removesuffix("}")
    assert len(frame_payloads.split(",")) == 8


def test_ce1_nim_video_embeddings_send_frame_chunks_as_queries(monkeypatch, ce1_module):
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return _FakeResponse([[float(len(payloads))]])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    with TempEnv({"REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000"}):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunks = []
        for _ in range(2):
            chunk = ChunkInfo()
            chunk.chunk_type = "video"
            chunks.append(chunk)
        frames = [
            torch.zeros((8, 4, 4, 3), dtype=torch.uint8),
            torch.ones((8, 4, 4, 3), dtype=torch.uint8),
        ]

        outputs = model.generate("dummy", chunks, video_frames=frames)

    assert [output.embeddings for output in outputs] == [[1.0], [2.0]]
    assert [payload["request_type"] for payload in payloads] == ["query", "query"]
    assert all(isinstance(payload["input"], str) for payload in payloads)
    assert all(
        payload["input"].startswith("data:video_frames/jpg;base64,{") for payload in payloads
    )


def test_ce1_nim_video_embeddings_accept_preencoded_jpeg_frames(monkeypatch, ce1_module):
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return _FakeResponse([[0.9]])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    jpeg_frames = []
    for idx in range(3):
        image = Image.new("RGB", (4, 4), color=(idx * 40, 80, 160))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        jpeg_frames.append(buffer.getvalue())

    with TempEnv({"REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000"}):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunk = ChunkInfo()
        chunk.chunk_type = "video"

        outputs = model.generate("dummy", [chunk], video_frames=[jpeg_frames])

    assert outputs[0].embeddings == [0.9]
    payload = payloads[0]
    assert payload["request_type"] == "query"
    nim_input = payload["input"]
    assert nim_input.startswith("data:video_frames/jpg;base64,{")
    frame_payloads = nim_input.removeprefix("data:video_frames/jpg;base64,{").removesuffix("}")
    assert len(frame_payloads.split(",")) == 8


def test_ce1_nim_video_embeddings_accept_encoded_jpeg_byte_tensors(monkeypatch, ce1_module):
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return _FakeResponse([[0.7]])

    monkeypatch.setattr(ce1_module.urllib.request, "urlopen", fake_urlopen)

    jpeg_frames = []
    for idx in range(3):
        image = Image.new("RGB", (4, 4), color=(idx * 40, 80, 160))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        jpeg_frames.append(torch.tensor(list(buffer.getvalue()), dtype=torch.uint8))

    with TempEnv({"REMOTE_EMBED_ENDPOINT": "http://ce1-nim:8000"}):
        model = ce1_module.CE1NimModel("nvidia/cosmos-embed1")
        chunk = ChunkInfo()
        chunk.chunk_type = "video"

        outputs = model.generate("dummy", [chunk], video_frames=[jpeg_frames])

    assert outputs[0].embeddings == [0.7]
    payload = payloads[0]
    assert payload["request_type"] == "query"
    nim_input = payload["input"]
    assert nim_input.startswith("data:video_frames/jpg;base64,{")
    frame_payloads = nim_input.removeprefix("data:video_frames/jpg;base64,{").removesuffix("}")
    assert len(frame_payloads.split(",")) == 8
