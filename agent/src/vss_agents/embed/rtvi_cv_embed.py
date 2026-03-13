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
import logging
from typing import cast

from vss_agents.embed.embed import EmbedClient

import httpx
from typing_extensions import override

logger = logging.getLogger(__name__)


class RTVICVEmbedClient(EmbedClient):
    """RTVI CV embedding client for text embeddings."""

    def __init__(self, endpoint: str):
        """
        Initialize RTVI CV embedding client.

        Args:
            endpoint: RTVI CV base URL
        """
        self.endpoint = endpoint.rstrip("/")
        self.text_embeddings_url = f"{self.endpoint}/api/v1/generate_text_embeddings"

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input using RTVI CV API."""
        payload = {
            "text_input": text,
            "model": "",
        }

        try:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.text_embeddings_url, json=payload)
                response.raise_for_status()
                result = response.json()

            # Extract embedding from response
            # Format 1: {"data": [{"embedding": [...]}]}
            # Format 2: {"data": [[...]]}
            if not result.get("data") or not isinstance(result["data"], list) or len(result["data"]) == 0:
                raise ValueError("RTVI CV response missing or empty 'data' field")

            embedding_data = result["data"][0]

            if isinstance(embedding_data, list):
                return embedding_data
            elif isinstance(embedding_data, dict) and "embedding" in embedding_data:
                return cast("list[float]", embedding_data["embedding"])
            else:
                raise ValueError(f"Unexpected embedding data format: {type(embedding_data).__name__}")

        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding from RTVI CV: {e}")
            raise
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse RTVI CV response: {e}")
            raise ValueError(f"Invalid RTVI CV response format: {e}") from e

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Image embeddings not supported by RTVI CV client."""
        raise NotImplementedError("Image embeddings not supported by RTVI CV client")

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Video embeddings not supported by RTVI CV client."""
        raise NotImplementedError("Video embeddings not supported by RTVI CV client")
