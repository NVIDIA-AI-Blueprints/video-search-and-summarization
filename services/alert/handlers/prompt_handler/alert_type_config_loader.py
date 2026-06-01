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

"""
Alert type configuration loader for MoE Agent's VLM invocation.
Supports JSON-based configuration with prompt templates and placeholders.
"""

import json
import logging
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List, Literal

from pydantic import BaseModel, ConfigDict, field_validator


logger = logging.getLogger(__name__)

# Valid segment anchor values
SegmentAnchorType = Literal["start", "end", "middle"]


class Prompts(BaseModel):
    user: str
    system: Optional[str] = None
    enrichment: Optional[str] = None


class VlmParams(BaseModel):
    """Optional per-alert-type VLM parameter overrides.

    All fields are optional — only specified fields override the global
    ``vlm`` config in config.yaml.  Unspecified fields fall back to global defaults.

    Unknown fields (e.g. typos) raise ValidationError at startup so config
    mistakes fail fast instead of being silently ignored.
    """
    model_config = ConfigDict(extra="forbid")

    base_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    request_timeout: Optional[int] = None
    use_vlm_media_defaults: Optional[bool] = None
    do_resize: Optional[bool] = None
    min_pixels: Optional[int] = None
    max_pixels: Optional[int] = None
    num_frames: Optional[int] = None
    enable_sampling: Optional[bool] = None
    sampling_fps: Optional[int] = None
    cr1_optimization: Optional[bool] = None
    max_retries: Optional[int] = None
    chunk_duration: Optional[int] = None
    num_frames_per_second_or_fixed_frames_chunk: Optional[int] = None
    enable_reasoning: Optional[bool] = None


class AlertTypeConfig(BaseModel):
    """Represents a single alert type configuration."""
    alert_type: str
    output_category: Optional[str] = None
    output_verdict_description: Optional[Dict[str, str]] = None
    segment_anchor: Optional[SegmentAnchorType] = None
    prompts: Prompts
    vlm_params: Optional[VlmParams] = None

    @field_validator('segment_anchor', mode='before')
    @classmethod
    def normalize_segment_anchor(cls, value):
        """Normalize segment_anchor to lowercase and validate."""
        if value is None:
            return None
        if isinstance(value, str):
            value = value.lower().strip()
            if value not in {'start', 'end', 'middle'}:
                raise ValueError(f"segment_anchor must be 'start', 'end', or 'middle', got '{value}'")
            return value
        return value

    def generate_prompts(self, payload: Dict[str, Any]) -> Dict[str, str]:
        raise NotImplementedError("PromptManager handles prompt generation")


class AlertTypeConfigFile(BaseModel):
    version: str
    alerts: List[AlertTypeConfig]


class AlertTypeConfigLoader:
    """Loads and manages alert type configurations."""

    def __init__(self, config_file_path: Optional[str] = None, main_config_file: str = "config.yaml"):
        """
        Initialize the alert type configuration loader.

        Args:
            config_file_path: Path to the alert type configuration JSON file.
                            If not provided, looks for path in main config.yaml.
            main_config_file: Path to the main configuration YAML file. Defaults to "config.yaml".
        """
        self.logger = logging.getLogger(self.__class__.__name__)

        if config_file_path:
            self.config_file_path = Path(config_file_path)
        else:
            # Load alert_type_config_file from HOME directory config
            try:
                with open(Path.home() / main_config_file, 'r') as f:
                    config = yaml.safe_load(f)
                    alert_config_file = config.get('alert_type_config_file')
                    if alert_config_file:
                        self.config_file_path = Path(alert_config_file) if os.path.isabs(alert_config_file) else Path.home() / alert_config_file
                    else:
                        raise ValueError("No alert_type_config_file in config")
            except Exception as e:
                self.logger.warning(f"Error reading config: {e}, using default")
                self.config_file_path = Path(__file__).parent.parent.parent / 'alert_type_config.json'

        # Load configurations
        self._load_configurations()

    def _load_configurations(self) -> None:
        """Load alert type configurations from JSON file."""
        if not self.config_file_path.exists():
            self.logger.warning(f"Alert type config file not found at {self.config_file_path}")
            return

        try:
            with open(self.config_file_path, 'r') as f:
                config_data = json.load(f)

            # Load alert configurations
            self.alert_configs = {
                alert_config.alert_type: alert_config for alert_config in AlertTypeConfigFile.model_validate(config_data).alerts
            }

            self.logger.info(
                f"Successfully loaded {len(self.alert_configs)} alert type configurations "
                f"from {self.config_file_path}"
            )
        except Exception as e:
            self.logger.error(f"Failed to load alert type configurations: {e}")
            raise

    def get_config_for_alert_type(self, alert_type: str) -> Optional[AlertTypeConfig]:
        """Get configuration for a specific alert type."""
        return self.alert_configs.get(alert_type)

    def get_vlm_params_for_alert_type(self, alert_type: str) -> Optional[VlmParams]:
        """Get per-alert-type VLM parameter overrides, or None if not configured."""
        config = self.alert_configs.get(alert_type)
        return config.vlm_params if config else None

    def get_all_alert_types(self) -> List[str]:
        """Get all configured alert types."""
        return list(self.alert_configs.keys())

    def get_output_category_mapping(self) -> Dict[str, str]:
        """Return mapping of alert_type -> output_category for types with custom output names.


        Returns:
            Dict mapping original alert_type to custom output_category.
            Only includes entries where output_category is explicitly defined.
            Returns empty dict if no custom mappings are configured.
        """
        if not hasattr(self, 'alert_configs') or not self.alert_configs:
            return {}
        return {
            cfg.alert_type: cfg.output_category
            for cfg in self.alert_configs.values()
            if cfg.output_category  # Only include if output_category is defined
        }

    def get_verdict_description_mapping(self) -> Dict[str, Dict[str, str]]:
        """Return mapping of alert_type -> {verdict -> description}.

        Returns:
            Dict mapping original alert_type to verdict->description mappings.
            Only includes entries where output_verdict_description is defined.
            Returns empty dict if no custom mappings are configured.
        """
        if not hasattr(self, 'alert_configs') or not self.alert_configs:
            return {}
        return {
            cfg.alert_type: cfg.output_verdict_description
            for cfg in self.alert_configs.values()
            if cfg.output_verdict_description
        }


    def save_to_redis(self, alert_type: str, config: AlertTypeConfig, store) -> None:
        """
        Seed an alert type config from the static JSON file into the
        ``alert_config:{alert_type}`` Redis key. Existing API-managed values
        win at the top level so user updates survive restart, but
        ``vlm_params`` is deep-merged with file defaults so partial API
        updates (e.g. only ``temperature``) do not drop file-supplied keys
        such as ``num_frames`` after a container restart.

        Args:
            alert_type: Alert type identifier
            config: ``AlertTypeConfig`` parsed from ``alert_type_config.json``
            store: ``handlers.alert_config.AlertConfigStore`` instance
        """
        try:
            from datetime import datetime, timezone
            from handlers.alert_config import normalize_alert_type

            normalized = normalize_alert_type(alert_type)
            existing = store.get(normalized) or {}

            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            file_vlm_params = (
                config.vlm_params.model_dump(exclude_none=True)
                if config.vlm_params else None
            )

            file_data = {
                "alert_type": normalized,
                "prompt": config.prompts.user if config.prompts.user else None,
                "system_prompt": config.prompts.system if config.prompts.system else None,
                "enrichment_prompt": (
                    config.prompts.enrichment if config.prompts.enrichment else None
                ),
                "vlm_params": file_vlm_params,
                "output_category": config.output_category,
            }

            # Top-level merge: API-managed values (existing) win for non-None
            # keys; the file populates keys that are missing from Redis.
            merged = {**file_data, **{k: v for k, v in existing.items() if v is not None}}

            # vlm_params needs a deep merge so partial API updates do not
            # drop file-supplied defaults on restart. File defaults seed any
            # key that the API record has not explicitly set.
            existing_vlm_params = existing.get("vlm_params")
            if isinstance(existing_vlm_params, dict) and isinstance(file_vlm_params, dict):
                merged["vlm_params"] = {**file_vlm_params, **existing_vlm_params}

            merged.setdefault("created_at", existing.get("created_at", now))
            merged["updated_at"] = existing.get("updated_at", now)

            store.set(normalized, merged)

            if not config.prompts.user and not config.prompts.system:
                self.logger.warning(f"No prompts found for alert type '{alert_type}'")
            else:
                self.logger.info(
                    f"Seeded alert_config for '{alert_type}' (user={bool(config.prompts.user)}, "
                    f"system={bool(config.prompts.system)}, enrichment={bool(config.prompts.enrichment)})"
                )
        except Exception as e:
            self.logger.warning(f"Failed to seed alert_config for '{alert_type}': {e}")