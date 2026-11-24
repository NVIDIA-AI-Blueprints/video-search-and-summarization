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

from abc import ABC, abstractmethod
import logging

import torch

from ..schemas import AdapterInput, AdapterOutput

logger = logging.getLogger(__name__)


class BaseModelAdapter(ABC):
    """Common interface for all focal-length estimation models.

    Lifecycle:
        1. __init__(model_id, manifest_entry)
        2. validate_environment() -> bool
        3. load_model(device)
        4. infer(input) -- called 1..N times
        5. unload_model()   [before switching to next model]
    """

    def __init__(self, model_id: str, manifest_entry: dict):
        self.model_id = model_id
        self.manifest = manifest_entry
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # --- Abstract methods (must be implemented by every adapter) ---

    @abstractmethod
    def validate_environment(self) -> bool:
        """Return True if repo, weights, and deps are ready."""
        ...

    @abstractmethod
    def load_model(self, device: str = "cuda") -> None:
        """Load weights into memory. Called once before infer() loop."""
        ...

    @abstractmethod
    def infer(self, inp: AdapterInput) -> AdapterOutput:
        """Run inference on a single image."""
        ...

    # --- Concrete methods (shared across adapters) ---

    def unload_model(self) -> None:
        """Release GPU memory. Override if model needs custom cleanup."""
        if hasattr(self, '_model') and self._model is not None:
            del self._model
            self._model = None
        self._loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info(f"[{self.model_id}] Model unloaded, GPU cache cleared")

    def set_seed(self, seed: int) -> None:
        """Best-effort deterministic seeding for benchmark reproducibility."""
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        logger.debug(f"[{self.model_id}] Seed set to {seed}")
