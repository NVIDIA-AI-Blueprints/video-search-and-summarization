# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import requests

from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.embedding")


class Embedding:
    """Handles embedding operations using NeMo embedding services."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        model_name: str = "nvidia/llama-3.2-nv-embedqa-1b-v2",
        api_key: str | None = None,
    ):
        # If no endpoint URL is provided, use nim_base_url from settings
        if endpoint_url is None:
            from src.config import settings

            endpoint_url = f"{settings.system_config.nmp_config.nim_base_url}/v1/embeddings"

        self.endpoint_url = endpoint_url
        self.model_name = model_name
        self.api_key = api_key

    def get_embedding(
        self, text: str | list[str], input_type: str = "query"
    ) -> list[list[float]] | None:
        """
        Get embeddings from the NIM endpoint.

        Args:
            text: Text to embed (string or list of strings)
            input_type: 'query' for search queries, 'passage' for documents

        Returns:
            List of embedding vectors or None if failed
        """
        if not isinstance(text, list):
            text = [text]

        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model_name, "input": text, "input_type": input_type}

        try:
            response = requests.post(self.endpoint_url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            response_json = response.json()
            embeddings = [item["embedding"] for item in response_json["data"]]
            return embeddings
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling embedding API: {e}")
            return None

    def get_embeddings_batch(
        self, texts: list[str], input_type: str = "query", batch_size: int = 32
    ) -> list[list[float]]:
        """
        Batch the input texts for the embedding API.
        The default batch size is 32.
        """
        embeddings = []
        for i in range(0, len(texts), batch_size):
            embedding = self.get_embedding(texts[i : i + batch_size], input_type=input_type)
            if embedding:
                embeddings.extend(embedding)
            else:
                cnt = len(texts[i : i + batch_size])
                embeddings.extend([None] * cnt)
        return embeddings
