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
import re
import yaml
from typing import Dict, Any, Optional, List

from handlers.alert_config import normalize_alert_type
from handlers.exception_handler.vss_exceptions import VSSException
from its_redis.dynamic_prompt_handler import DynamicPromptHandler

from .alert_type_config_loader import AlertTypeConfigLoader

logger = logging.getLogger(__name__)


class PromptManager:
    """Manages prompt templates and selection logic based on alert types."""
    
    def __init__(self, config_file: str = 'config.yaml'):
        """Initialize prompt manager with Redis-backed template handling."""
        self.logger = logging.getLogger(self.__class__.__name__)

        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            raise RuntimeError(f"Failed to read prompt configuration file '{config_file}': {exc}") from exc

        prompt_cfg = config.get('prompt', {}) or {}
        self.prefer_payload_prompt = bool(prompt_cfg.get('prefer_payload_prompt', False))
        self.override_prompts_on_start = bool(prompt_cfg.get("override_prompts_on_start", False))

        try:
            self.dynamic_prompt_handler = DynamicPromptHandler(config_file)
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize Redis prompt handler: {exc}") from exc

        if not self.dynamic_prompt_handler.health_check():
            raise RuntimeError("Redis prompt handler unavailable during PromptManager initialization")

        # Share a single cached-composite store across the process. The
        # factory returns the Redis-only variant when persistence is
        # disabled in config, matching the pre-ES-hydration deployment
        # shape without any conditional code here. Failures propagate so
        # startup fails fast when ES is enabled but unreachable.
        from handlers.alert_config import build_alert_config_store
        self.alert_config_store = build_alert_config_store(
            self.dynamic_prompt_handler._redis_client, config
        )

        try:
            alert_config_file = config.get('alert_type_config_file')
            self.alert_config_loader = AlertTypeConfigLoader(alert_config_file)
        except Exception as exc:
            self.logger.warning(f"Failed to initialize alert type config loader: {exc}")
            self.alert_config_loader = None


        self.GENERAL_PROMPT_TEMPLATE = 'Analyze this video and determine if there are any safety concerns or anomalies present.'
        self.FORMAT_PROMPT_TEMPLATE = 'Please provide your answer first, and finally conclude it in the following format: "Answer: Yes/No\\nConfidence: [score between 0.0 and 1.0]"'
        self.alert_type_prompts = {}
        self.alert_type_system_prompts = {}

        if self.override_prompts_on_start:
            self._override_prompts_in_redis()
        
    def load_prompts(self) -> None:
        self.logger.info("load_prompts() is deprecated; prompts are fetched directly from Redis")
    
    def _set_default_prompts(self) -> None:
        self.logger.info("_set_default_prompts() is unused in the new prompt flow")
    
    def get_system_prompt_for_entity(self, entity: Dict[str, Any]) -> Optional[str]:
        """Resolve system prompt with payload-first precedence, else fresh Redis (exact alert_type match), else None."""
        def _get_alert_type():
            if 'alert' in entity and isinstance(entity['alert'], dict):
                return entity['alert'].get('type')
            return None
        
        def _get_embedded_system_prompt():
            # Prefer snake_case schema; fall back to legacy camelCase if present
            if 'vss_params' in entity and 'vlm_params' in entity['vss_params']:
                vlm_params = entity['vss_params']['vlm_params']
                if isinstance(vlm_params, dict) and 'system_prompt' in vlm_params:
                    sp = vlm_params['system_prompt']
                    if sp and isinstance(sp, str) and sp.strip():
                        return sp
            if 'vssParams' in entity and 'vlmParams' in entity['vssParams']:
                vlm_params = entity['vssParams']['vlmParams']
                if isinstance(vlm_params, dict) and 'system_prompt' in vlm_params:
                    sp = vlm_params['system_prompt']
                    if sp and isinstance(sp, str) and sp.strip():
                        return sp
            return None
        
        # Payload-first precedence for system prompt
        embedded = _get_embedded_system_prompt()
        if embedded:
            self.logger.debug("Using embedded system_prompt from payload (payload-first)")
            return embedded
        
        alert_type = _get_alert_type()
        if alert_type:
            # Fetch fresh from Redis to reflect any dynamic updates (exact match only)
            fresh_system_prompt = self.get_fresh_system_prompt_for_alert_type(alert_type)
            if fresh_system_prompt:
                self.logger.debug(f"Using fresh Redis system_prompt for alert type: {alert_type}")
                return fresh_system_prompt
        
        return None
        
    def get_fresh_system_prompt_for_alert_type(self, alert_type: str) -> Optional[str]:
        """Fetch the system prompt from ``alert_config:{alert_type}``."""
        try:
            if self.alert_config_store is None:
                return None
            data = self.alert_config_store.get(alert_type)
            if not data:
                return None
            return data.get('system_prompt') or None
        except Exception as e:
            self.logger.error(f"Failed to fetch system prompt from alert_config:{alert_type}: {e}")
            return None

    def get_prompts_for_entity(self, entity: Dict[str, Any]) -> List[Dict[str, str]]:
        alert_type = self._extract_alert_type(entity)
        if not alert_type:
            event_id = entity.get('eventId') or entity.get('event_id') or 'N/A'
            sensor_id = entity.get('sensorId') or entity.get('sensor_id') or 'N/A'
            raise VSSException(f"Alert type missing for entity - eventId: {event_id}, sensorId: {sensor_id}")

        redis_prompt = self.get_fresh_prompt_for_alert_type(alert_type)
        if not redis_prompt:
            raise VSSException(f"No prompt found in Redis for alert type: {alert_type}")

        substituted_prompt = self._substitute_placeholders(redis_prompt, entity)
        return [{
            'question': substituted_prompt,
            'expectedAnswer': 'yes'
        }]
    
    def get_prompt_for_alert_type(self, alert_type: str) -> Optional[str]:
        """
        Get prompt based on alert type from Redis-backed prompt store.
        
        Uses a dynamic keyword matching algorithm that:
        1. First tries direct matches (exact, lowercase, normalized)
        2. Then uses intelligent keyword matching with scoring:
           - Matches keywords from input against config keys
           - Scores based on number of matches, match ratios, and relevance
           - Prefers exact substring matches
           - Penalizes overly long config keys for better specificity
        
        Args:
            alert_type: Type of alert (e.g., 'traffic_jam', 'animal_on_road', etc.)
            
        Returns:
            The appropriate prompt text, or None if not found
        """
        self.logger.debug(f"Selecting prompt for alert type: {alert_type}")
        
        # First check Redis (which includes prompts loaded from alert type config)
        if alert_type in self.alert_type_prompts:
            self.logger.debug(f"Found direct mapping for alert type: {alert_type}")
            return self.alert_type_prompts[alert_type]
        
        # This should not happen if initialization was done properly
        # Log a warning as prompts should have been loaded during initialization
        self.logger.warning(f"Prompt for alert type '{alert_type}' not found in cache. This indicates initialization issue.")
        
        # Try lowercase version
        alert_type_lower = alert_type.lower()
        if alert_type_lower in self.alert_type_prompts:
            self.logger.debug(f"Found mapping for lowercase alert type: {alert_type_lower}")
            return self.alert_type_prompts[alert_type_lower]
        
        # Try with underscores replaced by spaces and vice versa
        alert_type_normalized = alert_type.lower().replace('_', ' ')
        alert_type_with_underscores = alert_type.lower().replace(' ', '_')
        
        for key, prompt in self.alert_type_prompts.items():
            key_normalized = key.lower().replace('_', ' ')
            if key_normalized == alert_type_normalized or key.lower() == alert_type_with_underscores:
                self.logger.debug(f"Found mapping with normalized alert type: {key}")
                return prompt
        
        # Try keyword matching: split input alert_type and check if any keyword matches config keys
        alert_type_words = set(alert_type_lower.replace('_', ' ').split())
        
        # Score each config key based on matching keywords
        best_match = None
        best_score = 0
        
        for config_key, prompt in self.alert_type_prompts.items():
            config_key_words = set(config_key.lower().replace('_', ' ').split())
            
            # Calculate intersection of words
            matching_words = alert_type_words.intersection(config_key_words)
            
            if matching_words:
                # Base score: number of matching words
                score = len(matching_words)
                
                # Bonus for matching a higher proportion of the config key
                config_match_ratio = len(matching_words) / len(config_key_words)
                score += config_match_ratio
                
                # Bonus for matching a higher proportion of the input alert type
                input_match_ratio = len(matching_words) / len(alert_type_words)
                score += input_match_ratio * 0.5
                
                # Strong preference for exact substring matches
                if alert_type_lower in config_key.lower() or config_key.lower() in alert_type_lower:
                    score += 3
                
                # Penalty for config keys that are much longer than necessary
                # This helps prefer "traffic_jam" over "traffic_obstruction" for input "traffic"
                length_difference = abs(len(config_key_words) - len(alert_type_words))
                score -= length_difference * 0.1
                
                self.logger.debug(f"Scoring '{config_key}' for '{alert_type}': "
                                f"score={score:.2f}, matching_words={matching_words}")
                
                if score > best_score:
                    best_score = score
                    best_match = config_key
        
        if best_match:
            self.logger.debug(f"Found best keyword match for '{alert_type}': '{best_match}' (score: {best_score:.2f})")
            return self.alert_type_prompts[best_match]
        
        # No specific prompt found
        self.logger.warning(f"No specific prompt found for alert type: {alert_type}")
        return None

    def get_fresh_prompts_for_alert_type(self, alert_type: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch SYSTEM and USER prompt from ``alert_config:{alert_type}``.

        Returns ``(system_prompt, user_prompt)`` to keep the original
        signature stable for callers. Either field may be ``None`` when
        the record (or the field) is missing — the verification flow
        handles that gracefully.
        """
        self.logger.debug(f"Fetching prompts from alert_config:{alert_type}")
        try:
            if self.alert_config_store is None:
                self.logger.warning("AlertConfigStore not available, cannot fetch prompts")
                return None, None
            data = self.alert_config_store.get(alert_type)
            if not data:
                return None, None
            sp = data.get('system_prompt') or None
            up = data.get('prompt') or None
            return sp, up
        except Exception as e:
            self.logger.error(f"Failed to fetch prompts from alert_config:{alert_type}: {e}")
            return None, None
    
    def get_format_prompt(self) -> str:
        """Get the format prompt template."""
        return self.FORMAT_PROMPT_TEMPLATE
    
    def get_prompts_for_message(self, message: Dict[str, Any]) -> tuple[str, str]:
        """
        Get the system and user prompts for a message. If found in config but not in Redis,
        perform substitution and save the real prompt to Redis.
        
        Args:
            message: Message dictionary containing alert information
        
        Returns:
            The prompt string with substitutions applied, or empty string if not found
        """
        alert_type = message.get('category', '')
        if not alert_type:
            raise VSSException("Alert type missing in message for prompt lookup")

        # First, try to get fresh prompt from Redis
        redis_system_prompt, redis_user_prompt = self.get_fresh_prompts_for_alert_type(alert_type)

        if not redis_user_prompt:
            self.logger.warning(f"No user prompt found for alert type: {alert_type}")
            final_prompt = None
            #raise VSSException(f"No user prompt found in Redis for alert type: {alert_type}")
        else:                    
            self.logger.debug(f"User Prompt template before substitution: {redis_user_prompt}")
            final_prompt = self._substitute_placeholders(redis_user_prompt, message)
            self.logger.debug(f"Final User Prompt after substitution: {final_prompt}")

        return final_prompt, redis_system_prompt

    def get_enrichment_prompt_for_message(self, message: Dict[str, Any]) -> Optional[str]:
        """
        Get enrichment prompt for a message with placeholder substitution.
        
        Args:
            message: Message dict containing alert info
            
        Returns:
            Substituted enrichment prompt string, or None if not defined
        """
        alert_type = message.get('category', '')
        if not alert_type:
            return None
        
        enrichment_template = self._get_enrichment_prompt_from_redis(alert_type)
        if not enrichment_template:
            return None
        
        try:
            return self._substitute_placeholders(enrichment_template, message)
        except Exception as e:
            self.logger.warning(f"Failed to substitute placeholders in enrichment prompt: {e}")
            return None

    def _get_enrichment_prompt_from_redis(self, alert_type: str) -> Optional[str]:
        """Fetch enrichment prompt from ``alert_config:{alert_type}``.

        Returns ``None`` when the record is missing or has no enrichment
        prompt, so deployments without enrichment configured continue
        running without errors.
        """
        try:
            if self.alert_config_store is None:
                return None
            data = self.alert_config_store.get(alert_type)
            if not data:
                return None
            return data.get('enrichment_prompt') or None
        except Exception as e:
            self.logger.error(f"Failed to fetch enrichment prompt from alert_config:{alert_type}: {e}")
            return None

    def _substitute_placeholders(self, template: str, payload: Dict[str, Any]) -> str:
        # Temporarily replace escaped braces so they don't get interpreted
        template = template.replace("{{", "__ESCAPED_LBRACE__").replace("}}", "__ESCAPED_RBRACE__")

        def replace_placeholder(match: re.Match[str]) -> str:
            path = match.group(1)
            return self._resolve_placeholder_path(path, payload)

        try:
            result = re.sub(r'\{([^}]+)\}', replace_placeholder, template)
        except KeyError as exc:
            raise VSSException(f"Missing placeholder path '{exc.args[0]}' in payload") from exc

        # Restore escaped braces
        result = result.replace("__ESCAPED_LBRACE__", "{").replace("__ESCAPED_RBRACE__", "}")
        return result

    def _resolve_placeholder_path(self, path: str, payload: Dict[str, Any]) -> str:
        parts = path.split('.')
        current: Any = payload
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise KeyError(path)
        return str(current)

    def _override_prompts_in_redis(self) -> None:
        """Seed every alert type from ``alert_type_config.json`` into the
        ``alert_config:*`` key space so runtime hot-path lookups have a
        complete record without depending on the legacy ``prompts:*``."""
        if not self.alert_config_loader:
            raise RuntimeError("Alert type configuration loader not available; cannot override prompts")

        if self.alert_config_store is None:
            raise RuntimeError("AlertConfigStore not initialized; cannot seed alert_config:*")

        for alert_type in self.alert_config_loader.get_all_alert_types():
            config = self.alert_config_loader.get_config_for_alert_type(alert_type)
            if not config:
                continue
            self.alert_config_loader.save_to_redis(alert_type, config, self.alert_config_store)
