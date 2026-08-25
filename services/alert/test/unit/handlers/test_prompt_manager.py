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

"""Unit tests for ``handlers.prompt_handler.prompt_manager``.

``PromptManager`` resolves the VLM prompt for an incoming message. Two callers
exist in the tree — the enhancer loop and the on-demand verification service —
and both go through ``get_prompts_for_message`` /
``get_enrichment_prompt_for_message``, so those paths are covered here along
with the machinery they depend on.

The behaviour that carries real risk is placeholder substitution: prompts are
operator-authored templates read from the alert-config store and interpolated
with untrusted event payloads. A missing path must raise ``VSSException``
(so the event is rejected loudly) rather than silently emitting a literal
``{sensor.id}`` into the prompt, and ``{{`` / ``}}`` escapes must survive
round-trip so a template can contain literal JSON braces.

Store reads happen on every call rather than being cached, which is what lets
a hot-reloaded prompt take effect immediately; the "fresh read" tests pin that.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from handlers.exception_handler.vss_exceptions import VSSException
from handlers.prompt_handler.prompt_manager import PromptManager

CONFIG_YAML = "prompt:\n  prefer_payload_prompt: false\n"


def make_manager(config_yaml=CONFIG_YAML, store=None, loader=None, loader_error=None,
                 seed_prompts=True):
    """Build a PromptManager with the config file, store and loader stubbed."""
    store = MagicMock() if store is None else store
    with patch("builtins.open", mock_open(read_data=config_yaml)), patch(
        "handlers.alert_config.build_alert_config_store", return_value=store
    ), patch(
        "handlers.prompt_handler.prompt_manager.AlertTypeConfigLoader",
        side_effect=loader_error,
        return_value=MagicMock() if loader is None else loader,
    ):
        return PromptManager("config.yaml", seed_prompts=seed_prompts)


@pytest.fixture
def store():
    store = MagicMock()
    store.get.return_value = {
        "prompt": "Is there a {category} at {sensorId}?",
        "system_prompt": "You are a safety analyst.",
        "enrichment_prompt": "Count vehicles at {sensorId}.",
    }
    return store


@pytest.fixture
def manager(store):
    return make_manager(store=store)


MESSAGE = {"category": "collision", "sensorId": "cam-1"}


class TestStartupSeeding:
    """The startup write is per instance; reads stay per pipeline.

    With several pipeline processes per instance, every one of them seeding
    would issue the same writes against a shared store concurrently, and a
    later process could overwrite a prompt the verification API had already
    changed on an earlier one.
    """

    OVERRIDE = "prompt:\n  override_prompts_on_start: true\n"

    @staticmethod
    def _seeds(**kwargs):
        with patch.object(PromptManager, "_seed_prompts_to_store") as seed:
            make_manager(**kwargs)
        return seed.called

    def test_seeds_by_default(self):
        assert self._seeds(config_yaml=self.OVERRIDE) is True

    def test_does_not_seed_when_not_the_seeding_process(self):
        assert self._seeds(config_yaml=self.OVERRIDE, seed_prompts=False) is False

    def test_still_silent_when_override_is_off(self):
        assert self._seeds(config_yaml=CONFIG_YAML) is False

    def test_reads_stay_available_without_seeding(self, store):
        manager = make_manager(config_yaml=self.OVERRIDE, store=store, seed_prompts=False)
        assert manager.alert_config_store is store


class TestConstruction:
    def test_reads_prompt_flags_from_the_config_file(self):
        manager = make_manager(
            "prompt:\n  prefer_payload_prompt: true\n  override_prompts_on_start: false\n"
        )
        assert manager.prefer_payload_prompt is True
        assert manager.override_prompts_on_start is False

    def test_flags_default_to_false(self):
        manager = make_manager("{}\n")
        assert manager.prefer_payload_prompt is False
        assert manager.override_prompts_on_start is False

    def test_empty_config_file_is_tolerated(self):
        assert make_manager("") is not None

    def test_unreadable_config_file_raises(self):
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(RuntimeError, match="Failed to read prompt configuration file"):
                PromptManager("missing.yaml")

    def test_malformed_config_file_raises(self):
        with patch("builtins.open", mock_open(read_data="prompt: [unclosed\n")):
            with pytest.raises(RuntimeError, match="Failed to read prompt configuration file"):
                PromptManager("bad.yaml")

    def test_store_build_failure_propagates(self):
        """Startup must fail fast when ES is enabled but unreachable."""
        with patch("builtins.open", mock_open(read_data=CONFIG_YAML)), patch(
            "handlers.alert_config.build_alert_config_store",
            side_effect=RuntimeError("ES unreachable"),
        ):
            with pytest.raises(RuntimeError, match="ES unreachable"):
                PromptManager("config.yaml")

    def test_loader_failure_degrades_to_none(self):
        """A broken alert_type_config.json must not block startup."""
        manager = make_manager(loader_error=RuntimeError("bad json"))
        assert manager.alert_config_loader is None

    def test_prompt_templates_are_available(self, manager):
        assert manager.GENERAL_PROMPT_TEMPLATE
        assert "Answer: Yes/No" in manager.FORMAT_PROMPT_TEMPLATE

    def test_prompts_are_not_seeded_by_default(self, store):
        loader = MagicMock()
        make_manager(store=store, loader=loader)
        loader.seed_to_store.assert_not_called()


class TestSeedPromptsToStore:
    def _config_yaml(self):
        return "prompt:\n  override_prompts_on_start: true\n"

    def test_every_alert_type_is_seeded(self, store):
        loader = MagicMock()
        loader.get_all_alert_types.return_value = ["collision", "fire"]
        loader.get_config_for_alert_type.side_effect = lambda t: {"type": t}

        make_manager(self._config_yaml(), store=store, loader=loader)

        assert loader.seed_to_store.call_count == 2
        loader.seed_to_store.assert_any_call("collision", {"type": "collision"}, store)

    def test_alert_types_without_config_are_skipped(self, store):
        loader = MagicMock()
        loader.get_all_alert_types.return_value = ["collision", "ghost"]
        loader.get_config_for_alert_type.side_effect = lambda t: None if t == "ghost" else {"t": t}

        make_manager(self._config_yaml(), store=store, loader=loader)

        assert loader.seed_to_store.call_count == 1

    def test_missing_loader_raises(self, store):
        with pytest.raises(RuntimeError, match="Alert type configuration loader not available"):
            make_manager(self._config_yaml(), store=store, loader_error=RuntimeError("bad json"))

    def test_missing_store_raises(self):
        with patch("builtins.open", mock_open(read_data=self._config_yaml())), patch(
            "handlers.alert_config.build_alert_config_store", return_value=None
        ), patch("handlers.prompt_handler.prompt_manager.AlertTypeConfigLoader"):
            with pytest.raises(RuntimeError, match="AlertConfigStore not initialized"):
                PromptManager("config.yaml")


class TestGetFreshPromptsForAlertType:
    def test_returns_system_and_user_prompts(self, manager, store):
        system, user = manager.get_fresh_prompts_for_alert_type("collision")

        assert system == "You are a safety analyst."
        assert user == "Is there a {category} at {sensorId}?"
        store.get.assert_called_once_with("collision")

    def test_missing_record_returns_a_pair_of_nones(self, manager, store):
        store.get.return_value = None
        assert manager.get_fresh_prompts_for_alert_type("ghost") == (None, None)

    def test_empty_prompt_fields_become_none(self, manager, store):
        store.get.return_value = {"prompt": "", "system_prompt": ""}
        assert manager.get_fresh_prompts_for_alert_type("collision") == (None, None)

    def test_partial_record_is_supported(self, manager, store):
        store.get.return_value = {"prompt": "Ask something."}
        system, user = manager.get_fresh_prompts_for_alert_type("collision")
        assert system is None
        assert user == "Ask something."

    def test_store_error_degrades_to_nones(self, manager, store):
        store.get.side_effect = RuntimeError("ES timeout")
        assert manager.get_fresh_prompts_for_alert_type("collision") == (None, None)

    def test_absent_store_degrades_to_nones(self, manager):
        manager.alert_config_store = None
        assert manager.get_fresh_prompts_for_alert_type("collision") == (None, None)

    def test_each_call_reads_the_store_again(self, manager, store):
        """No caching — a hot-reloaded prompt must take effect immediately."""
        manager.get_fresh_prompts_for_alert_type("collision")
        store.get.return_value = {"prompt": "updated"}
        _system, user = manager.get_fresh_prompts_for_alert_type("collision")

        assert user == "updated"
        assert store.get.call_count == 2


class TestGetPromptsForMessage:
    def test_substitutes_placeholders_in_the_user_prompt(self, manager):
        user, system = manager.get_prompts_for_message(MESSAGE)

        assert user == "Is there a collision at cam-1?"
        assert system == "You are a safety analyst."

    def test_missing_category_raises(self, manager):
        with pytest.raises(VSSException, match="Alert type missing in message"):
            manager.get_prompts_for_message({"sensorId": "cam-1"})

    def test_empty_category_raises(self, manager):
        with pytest.raises(VSSException, match="Alert type missing in message"):
            manager.get_prompts_for_message({"category": "", "sensorId": "cam-1"})

    def test_missing_user_prompt_yields_none_but_keeps_the_system_prompt(self, manager, store):
        store.get.return_value = {"system_prompt": "You are a safety analyst."}
        user, system = manager.get_prompts_for_message(MESSAGE)

        assert user is None
        assert system == "You are a safety analyst."

    def test_unknown_alert_type_yields_a_pair_of_nones(self, manager, store):
        store.get.return_value = None
        assert manager.get_prompts_for_message(MESSAGE) == (None, None)

    def test_missing_placeholder_path_raises(self, manager, store):
        store.get.return_value = {"prompt": "Check {nope.deep}"}
        with pytest.raises(VSSException, match="Missing placeholder path"):
            manager.get_prompts_for_message(MESSAGE)


class TestGetEnrichmentPromptForMessage:
    def test_substitutes_placeholders(self, manager):
        assert manager.get_enrichment_prompt_for_message(MESSAGE) == "Count vehicles at cam-1."

    def test_missing_category_returns_none(self, manager):
        assert manager.get_enrichment_prompt_for_message({"sensorId": "cam-1"}) is None

    def test_alert_type_without_enrichment_returns_none(self, manager, store):
        store.get.return_value = {"prompt": "Ask something."}
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None

    def test_empty_enrichment_prompt_returns_none(self, manager, store):
        store.get.return_value = {"enrichment_prompt": ""}
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None

    def test_unknown_alert_type_returns_none(self, manager, store):
        store.get.return_value = None
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None

    def test_store_error_returns_none(self, manager, store):
        store.get.side_effect = RuntimeError("ES timeout")
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None

    def test_absent_store_returns_none(self, manager):
        manager.alert_config_store = None
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None

    def test_substitution_failure_returns_none_rather_than_raising(self, manager, store):
        """Enrichment is optional, so a bad template must not fail the event."""
        store.get.return_value = {"enrichment_prompt": "Count {nope} vehicles"}
        assert manager.get_enrichment_prompt_for_message(MESSAGE) is None


class TestSubstitutePlaceholders:
    def test_top_level_field(self, manager):
        assert manager._substitute_placeholders("at {sensorId}", MESSAGE) == "at cam-1"

    def test_dotted_path(self, manager):
        payload = {"sensor": {"id": "cam-1"}}
        assert manager._substitute_placeholders("at {sensor.id}", payload) == "at cam-1"

    def test_deeply_nested_path(self, manager):
        payload = {"a": {"b": {"c": "deep"}}}
        assert manager._substitute_placeholders("{a.b.c}", payload) == "deep"

    def test_multiple_placeholders(self, manager):
        result = manager._substitute_placeholders("{category} at {sensorId}", MESSAGE)
        assert result == "collision at cam-1"

    def test_repeated_placeholder(self, manager):
        assert manager._substitute_placeholders("{sensorId}/{sensorId}", MESSAGE) == "cam-1/cam-1"

    def test_non_string_values_are_stringified(self, manager):
        assert manager._substitute_placeholders("{count}", {"count": 3}) == "3"
        assert manager._substitute_placeholders("{flag}", {"flag": True}) == "True"

    def test_null_value_is_stringified(self, manager):
        assert manager._substitute_placeholders("{x}", {"x": None}) == "None"

    def test_template_without_placeholders_is_unchanged(self, manager):
        assert manager._substitute_placeholders("plain text", MESSAGE) == "plain text"

    def test_escaped_braces_survive_round_trip(self, manager):
        template = 'Reply as {{"verdict": "yes"}} for {category}'
        result = manager._substitute_placeholders(template, MESSAGE)
        assert result == 'Reply as {"verdict": "yes"} for collision'

    def test_escaped_braces_are_not_treated_as_placeholders(self, manager):
        assert manager._substitute_placeholders("{{sensorId}}", MESSAGE) == "{sensorId}"

    def test_missing_top_level_path_raises(self, manager):
        with pytest.raises(VSSException, match="Missing placeholder path 'nope'"):
            manager._substitute_placeholders("{nope}", MESSAGE)

    def test_path_through_a_scalar_raises(self, manager):
        with pytest.raises(VSSException, match="Missing placeholder path"):
            manager._substitute_placeholders("{sensorId.id}", MESSAGE)


class TestResolvePlaceholderPath:
    def test_resolves_a_dotted_path(self, manager):
        assert manager._resolve_placeholder_path("sensor.id", {"sensor": {"id": "cam-1"}}) == "cam-1"

    def test_missing_key_raises_key_error(self, manager):
        with pytest.raises(KeyError):
            manager._resolve_placeholder_path("nope", {})

    def test_path_through_a_scalar_raises_key_error(self, manager):
        with pytest.raises(KeyError):
            manager._resolve_placeholder_path("a.b", {"a": "scalar"})


class TestFormatPrompt:
    def test_returns_the_format_template(self, manager):
        assert manager.get_format_prompt() == manager.FORMAT_PROMPT_TEMPLATE


class TestDeprecatedEntryPoints:
    def test_load_prompts_is_a_noop(self, manager):
        assert manager.load_prompts() is None

    def test_set_default_prompts_is_a_noop(self, manager):
        assert manager._set_default_prompts() is None

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Open defect: get_prompts_for_entity calls _extract_alert_type and "
            "get_fresh_prompt_for_alert_type, neither of which exists on the "
            "class or any base, so it always raises AttributeError. It has no "
            "callers. Fix: wire it to _extract_alert_type / "
            "get_fresh_prompts_for_alert_type, or delete the method. When "
            "either lands this test XPASSes — drop the marker then."
        ),
    )
    def test_get_prompts_for_entity_returns_the_stored_prompt(self, manager, store):
        store.get.return_value = {"prompt": "Is there a {category}?"}

        result = manager.get_prompts_for_entity(
            {"alert": {"type": "collision"}, "category": "collision"}
        )

        assert result == [{"question": "Is there a collision?", "expectedAnswer": "yes"}]
