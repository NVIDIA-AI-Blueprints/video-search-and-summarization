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

import importlib
import logging
from pathlib import Path
from typing import Optional

import yaml

from .adapters.base import BaseModelAdapter

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Loads model manifest and instantiates adapters on demand."""

    def __init__(self, manifest_path: Path, project_root: Optional[Path] = None):
        self._manifest_path = Path(manifest_path)
        self._project_root = project_root or self._manifest_path.parent.parent.parent
        self._manifest: dict = {}
        self._load_manifest()

    def _load_manifest(self):
        with open(self._manifest_path, 'r') as f:
            raw = yaml.safe_load(f)
        self._manifest = raw.get("models", {})

    def list_enabled_models(self) -> list[str]:
        return [mid for mid, cfg in self._manifest.items() if cfg.get("enabled", False)]

    def list_required_models(self) -> list[str]:
        return [mid for mid, cfg in self._manifest.items()
                if cfg.get("enabled", False) and cfg.get("required_for_pose", False)]

    def get_manifest_entry(self, model_id: str) -> dict:
        if model_id not in self._manifest:
            raise KeyError(f"Model '{model_id}' not found in manifest")
        entry = dict(self._manifest[model_id])
        entry["_project_root"] = str(self._project_root)
        return entry

    def create_adapter(self, model_id: str) -> BaseModelAdapter:
        entry = self.get_manifest_entry(model_id)
        adapter_class_path = entry["adapter_class"]
        module_path, class_name = adapter_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        adapter_cls = getattr(module, class_name)
        return adapter_cls(model_id=model_id, manifest_entry=entry)
