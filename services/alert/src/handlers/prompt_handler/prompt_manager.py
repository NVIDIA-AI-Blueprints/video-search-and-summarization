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
from typing import Dict, Any, Optional

from handlers.exception_handler.vss_exceptions import VSSException

from .alert_type_config_loader import AlertTypeConfigLoader

logger = logging.getLogger(__name__)

# Applied when the resolved alert config carries no system prompt of its own.
# The user prompt is the detection question; the system prompt is the VLM
# contract, so it is the service's call, not the operator's. Matches the
# ``system`` text in the shipped ``alert_type_config.json``, and is mirrored by
# ``prompt.default_system_prompt`` in every shipped config, which is what an
# operator overrides -- a verifier usually wants stronger framing than this.
# Editing it here alone changes no deployment; the drift guard in
# test/unit/test_shipped_config_defaults.py fails until the configs follow.
DEFAULT_SYSTEM_PROMPT = 'You are a helpful assistant.'


class PromptManager:
    """Manages prompt templates and selection logic based on alert types."""
    
    def __init__(self, config_file: str = 'config.yaml', seed_prompts: bool = True):
        """Initialize prompt manager backed by the alert-config store (ES/in-process).

        ``seed_prompts`` gates the startup write. Reading the store is per
        instance of this class; writing it is not — with several pipeline
        processes every one of them would seed the same documents and race,
        so only one is asked to.
        """
        self.logger = logging.getLogger(self.__class__.__name__)

        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            raise RuntimeError(f"Failed to read prompt configuration file '{config_file}': {exc}") from exc

        prompt_cfg = config.get('prompt', {}) or {}
        self.prefer_payload_prompt = bool(prompt_cfg.get('prefer_payload_prompt', False))
        self.override_prompts_on_start = bool(prompt_cfg.get("override_prompts_on_start", False))
        self.default_system_prompt = self._read_default_system_prompt(prompt_cfg)

        # Share a single alert-config store across the process. The
        # factory returns an in-process store when persistence is
        # disabled, or the ES-primary cached composite when persistence
        # is enabled. No Redis connection is made. Failures propagate so
        # startup fails fast when ES is enabled but unreachable.
        from handlers.alert_config import build_alert_config_store
        self.alert_config_store = build_alert_config_store(config)

        try:
            alert_config_file = config.get('alert_type_config_file')
            self.alert_config_loader = AlertTypeConfigLoader(alert_config_file)
        except Exception as exc:
            self.logger.warning(f"Failed to initialize alert type config loader: {exc}")
            self.alert_config_loader = None


        self.GENERAL_PROMPT_TEMPLATE = 'Analyze this video and determine if there are any safety concerns or anomalies present.'
        self.FORMAT_PROMPT_TEMPLATE = 'Please provide your answer first, and finally conclude it in the following format: "Answer: Yes/No\\nConfidence: [score between 0.0 and 1.0]"'

        if self.override_prompts_on_start and seed_prompts:
            self._seed_prompts_to_store()

    @staticmethod
    def _read_default_system_prompt(prompt_cfg: Dict[str, Any]) -> str:
        """Read ``prompt.default_system_prompt`` as a string, or fail startup.

        YAML will hand back an int, list or dict here as readily as a string,
        and neither outcome is one an operator would notice: a truthy
        non-string reaches the VLM as invalid message content, while a falsy
        one silently turns the fallback off — the failure the default exists to
        prevent. An empty or whitespace-only value is the supported way to ask
        for no system message.
        """
        value = prompt_cfg.get('default_system_prompt', DEFAULT_SYSTEM_PROMPT)
        if value is None:
            return ''
        if not isinstance(value, str):
            raise RuntimeError(
                f"prompt.default_system_prompt must be a string, got "
                f"{type(value).__name__} ({value!r}). Use \"\" to send no system prompt."
            )
        return value.strip()

    def load_prompts(self) -> None:
        self.logger.info("load_prompts() is deprecated; prompts are fetched directly from the alert-config store")
    
    def _set_default_prompts(self) -> None:
        self.logger.info("_set_default_prompts() is unused in the new prompt flow")
    
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
    
    def get_prompts_for_message(
        self, message: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Get the user and system prompts for a message from the alert-config store,
        then perform placeholder substitution.
        
        Args:
            message: Message dictionary containing alert information
        
        Returns:
            ``(user_prompt, system_prompt)``. Either is ``None`` when the alert
            type has none configured; ``(None, None)`` is what the pipelines
            read as "not configured" to record the ``no_prompt`` outcome.
        """
        alert_type = message.get('category', '')
        if not alert_type:
            raise VSSException("Alert type missing in message for prompt lookup")

        # Fetch the current prompts from the alert-config store
        stored_system_prompt, stored_user_prompt = self.get_fresh_prompts_for_alert_type(alert_type)

        if not stored_user_prompt:
            self.logger.warning(f"No user prompt found for alert type: {alert_type}")
            final_prompt = None
        else:                    
            self.logger.debug(f"User Prompt template before substitution: {stored_user_prompt}")
            final_prompt = self._substitute_placeholders(stored_user_prompt, message)
            self.logger.debug(f"Final User Prompt after substitution: {final_prompt}")

        return final_prompt, self._resolve_system_prompt(alert_type, final_prompt, stored_system_prompt)

    def _resolve_system_prompt(
        self,
        alert_type: str,
        user_prompt: Optional[str],
        stored_system_prompt: Optional[str],
    ) -> Optional[str]:
        """Resolve the system prompt a VLM call is made with. Single entry point.

        A config created over the API can legitimately omit ``system_prompt``
        — it is optional there — and callers of this method hand what they get
        straight to the VLM, which drops the system role when it is empty. So
        the default is applied here rather than at seed time, where only
        ``alert_type_config.json`` would ever benefit from it.

        The *default* is what the user prompt gates, not the stored value.
        Without a user prompt there is no VLM call to give a contract to, and
        the enhancer reads ``(None, None)`` as "this alert type is not
        configured" to record the ``no_prompt`` outcome — inventing a system
        prompt would mask that. A record that stores a system prompt and no
        user prompt still resolves to ``(None, <stored>)``: the API rejects an
        empty ``prompt``, so that shape only comes from a malformed store
        record, and it is not this method's job to paper over one.

        Whitespace never survives this method: a blank stored value resolves to
        ``None`` (an omitted role beats an empty system message) and padding is
        trimmed off a real one. Store contents are not all API-validated —
        ``alert_type_config.json`` is seeded verbatim — so normalizing here is
        what makes the guarantee hold for every source.
        """
        stored = (stored_system_prompt or '').strip()
        if stored:
            return stored
        if user_prompt and self.default_system_prompt:
            self.logger.debug(
                f"No system prompt configured for alert type '{alert_type}'; "
                "using the service default"
            )
            return self.default_system_prompt
        return None

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
        
        enrichment_template = self._get_enrichment_prompt_from_store(alert_type)
        if not enrichment_template:
            return None
        
        try:
            return self._substitute_placeholders(enrichment_template, message)
        except Exception as e:
            self.logger.warning(f"Failed to substitute placeholders in enrichment prompt: {e}")
            return None

    def _get_enrichment_prompt_from_store(self, alert_type: str) -> Optional[str]:
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

    def _seed_prompts_to_store(self) -> None:
        """Seed every alert type from ``alert_type_config.json`` into the
        alert-config store (``alert_config:*`` records) so runtime hot-path
        lookups have a complete record at startup."""
        if not self.alert_config_loader:
            raise RuntimeError("Alert type configuration loader not available; cannot override prompts")

        if self.alert_config_store is None:
            raise RuntimeError("AlertConfigStore not initialized; cannot seed alert_config:*")

        for alert_type in self.alert_config_loader.get_all_alert_types():
            config = self.alert_config_loader.get_config_for_alert_type(alert_type)
            if not config:
                continue
            self.alert_config_loader.seed_to_store(alert_type, config, self.alert_config_store)
