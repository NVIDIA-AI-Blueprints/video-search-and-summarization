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
from abc import ABC
from abc import abstractmethod


class EmbedClient(ABC):
    """Abstract base class for embedding clients."""

    @abstractmethod
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate embedding for image input."""
        pass

    @abstractmethod
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input."""
        pass

    @abstractmethod
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate embedding for video input."""
        pass
