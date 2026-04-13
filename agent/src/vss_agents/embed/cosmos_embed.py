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
from typing import override

import httpx

from vss_agents.embed.embed import EmbedClient

logger = logging.getLogger(__name__)


class CosmosEmbedClient(EmbedClient):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.text_embeddings_url = f"{endpoint}/v1/generate_text_embeddings"
        self.image_embeddings_url = f"{endpoint}/v1/generate_image_embeddings"
        self.video_embeddings_url = f"{endpoint}/v1/generate_video_embeddings"

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate embedding for image input"""
        # Handles base64 data URI and presigned_url format
        if image_url.startswith("data:image/"):
            # base64 URI ("data:image/jpeg;base64,...")
            formatted_input = image_url
        else:
            # presigned_url format
            formatted_input = f"data:image/jpeg;presigned_url,{image_url}"

        payload = {
            "input": [formatted_input],
            "request_type": "query",
            "encoding_format": "float",
            "model": "nvidia/cosmos-embed1",
        }
        try:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.image_embeddings_url, json=payload)
                response.raise_for_status()
                result = response.json()
            embedding: list[float] = result["data"][0]["embedding"]
            return embedding
        except httpx.HTTPError as e:
            logger.error(f"Failed to get image embedding: {e}")
            raise

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input"""
        payload = {
            "text_input": [text],
            "model": "cosmos-embed1-448p",
        }

        try:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.text_embeddings_url, json=payload)
                response.raise_for_status()
                result = response.json()
            embeddings: list[float] = result["data"][0]["embeddings"]
            return embeddings
        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding: {e}")
            raise

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate embedding for video input"""
        return (await self.get_video_embeddings_from_urls([video_url]))[0]

    async def get_video_embeddings_from_urls(self, urls: list[str]) -> list[list[float]]:
        """Generate embeddings for videos from URLs (public or presigned)"""
        logger.info(f"Generating embeddings for {len(urls)} video chunks via URLs")

        # Format URLs according to the required format
        formatted_urls = [f"data:video/mp4;presigned_url,{url}" for url in urls]

        payload = {
            "input": formatted_urls,
            "model": "nvidia/cosmos-embed1",
            "encoding_format": "float",
            "request_type": "bulk_video",
        }
        logger.info(f"Payload: {payload}")

        timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.video_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()

        # Extract embeddings from response
        embeddings = [item["embedding"] for item in result["data"]]
        logger.info(f"Successfully generated {len(embeddings)} embeddings")
        return embeddings
