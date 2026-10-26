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

"""Opt-in caption HTTP transport swap inside an otherwise stock VSS frontend.

This benchmark adapter uses the pinned application's request builder and raw
response schema. It exercises the library transport, not its ingestion queue,
storage adapters or summary algorithm. Never install it in the retained pilot.
"""

import asyncio
from contextlib import aclosing

import httpx

from vss.ingestion.rtvi import _RTVITransport, _RTVITransportError


def arguments(payload):
    """Preserve the stock file-ID request exactly; reject unsupported URL mode."""
    if payload.get("url") is not None or payload.get("stream") is not True:
        raise ValueError("normal transport experiment requires streaming file IDs")
    rest = dict(payload)
    result = {
        "source_id": rest.pop("id"),
        "prompt": rest.pop("prompt"),
        "model": rest.pop("model"),
        "chunk_duration": rest.pop("chunk_duration"),
        "chunk_overlap_duration": rest.pop("chunk_overlap_duration"),
    }
    if not isinstance(result["source_id"], str) or not result["source_id"]:
        raise ValueError("normal transport experiment requires a file ID")
    result["generation_config"] = rest
    return result


def client_class(stock_class, error_class):
    """Keep all stock behavior except the finite-file caption streaming method."""

    class LibraryTransportClient(stock_class):
        def generate_captions_stream(self, **kwargs):
            payload = self._build_generate_captions_request(**kwargs).model_dump(
                exclude_none=True
            )
            request = arguments(payload)

            async def responses():
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=600,
                    trust_env=False,
                    follow_redirects=False,
                ) as http:
                    transport = _RTVITransport(client=http)
                    async with aclosing(
                        transport.generate_captions_stream(**request)
                    ) as stream:
                        async for body in stream:
                            yield body

            with asyncio.Runner() as runner:
                stream = responses()
                try:
                    while True:
                        try:
                            yield runner.run(anext(stream))
                        except StopAsyncIteration:
                            return
                except _RTVITransportError as error:
                    cause = error.__cause__
                    if isinstance(cause, httpx.HTTPStatusError):
                        self._raise_rtvi_error("generate_captions", cause.response)
                    raise error_class(
                        502, "LibraryTransportError", str(error)
                    ) from error
                finally:
                    runner.run(stream.aclose())

    return LibraryTransportClient
