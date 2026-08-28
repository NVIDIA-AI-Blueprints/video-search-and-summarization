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

"""The system prompt an API-created alert config is actually verified against.

``system_prompt`` is optional on ``POST /api/v1/verification/config``, and a
config created without one used to reach the VLM as user prompt plus media with
no system message at all — weaker than the same alert type seeded from
``alert_type_config.json``, which always carries a ``system`` text.

Every layer on that path was individually well-behaved: the schema allows the
field to be absent, the service stores what it is given, the resolver returns
what it reads, and the VLM client omits an empty role. Tests that stop at any
one seam pass either way, so this one runs the whole path with nothing stubbed
between the HTTP request and the VLM payload:

    POST /api/v1/verification/config   (real router → schema → service)
        → alert-config store           (one in-process instance, shared)
        → PromptManager                (real config file, real store read)
        → VLMClient.analyze_video_url  (real payload builder)
        → the ``messages`` array on the wire

Only the OpenAI transport is patched, so the assertions are about the request
the NIM would have received. The store instance is injected into both sides
because the deployment shape is two processes over one Elasticsearch; sharing
it here is what makes "written by the API" and "read by the pipeline" the same
record.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from handlers.alert_config import AlertConfigService, AlertConfigStore  # noqa: E402
from handlers.prompt_handler.prompt_manager import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    PromptManager,
)
from vlm.vlm_client import VLMClient  # noqa: E402
import web.api.alert_config_routes as _routes_mod  # noqa: E402

ALERT_TYPE = "FOV Count Violation"
USER_PROMPT = (
    "Is anyone on the ladder without a hardhat and safety vest? Answer yes or no."
)
CLIP_URL = "http://vios:30888/clip.mp4"

# The issue's reproducer: alert_type and prompt only.
CREATE_PAYLOAD = {
    "alert_type": ALERT_TYPE,
    "prompt": USER_PROMPT,
    "output_category": "Ladder PPE Violation",
}

MESSAGE = {"category": ALERT_TYPE, "sensorId": "cam-1"}


@pytest.fixture
def store():
    """The one record both the API and the resolver see."""
    return AlertConfigStore()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(_routes_mod.router)
    # Patch the accessor the routes call rather than the ``_service`` cache
    # behind it: the cache is private and would disappear if service lookup
    # ever moved to ``Depends``, whereas the accessor is the seam either way.
    service = AlertConfigService(store=store)
    with patch.object(_routes_mod, "_get_service", return_value=service):
        yield TestClient(app)


@pytest.fixture
def config_file(tmp_path):
    """A real config file, so the default is read the way a deployment reads it."""
    path = tmp_path / "config.yaml"
    path.write_text("prompt:\n  prefer_payload_prompt: false\n")
    return str(path)


@pytest.fixture
def prompt_manager(store, config_file):
    with patch(
        "handlers.alert_config.build_alert_config_store", return_value=store
    ), patch("handlers.prompt_handler.prompt_manager.AlertTypeConfigLoader"):
        return PromptManager(config_file)


@pytest.fixture
def vlm():
    with patch("vlm.vlm_client.OpenAI"):
        client = VLMClient({"model": "nvidia/cosmos-reason2-8b", "max_tokens": 256})
    message = MagicMock()
    message.content = "<answer>yes</answer>"
    client.client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    return client


def verify(prompt_manager, vlm, message=MESSAGE):
    """Run one verification the way the pipeline does; return the wire messages."""
    user_prompt, system_prompt = prompt_manager.get_prompts_for_message(message)
    vlm.analyze_video_url(CLIP_URL, user_prompt, system_prompt)
    return vlm.client.chat.completions.create.call_args.kwargs["messages"]


def content_of(messages, role):
    """The content of the one message with ``role``, or ``None`` if absent."""
    found = [m["content"] for m in messages if m["role"] == role]
    assert len(found) <= 1, f"expected at most one {role} message, got {len(found)}"
    return found[0] if found else None


class TestConfigCreatedWithoutASystemPrompt:
    def test_the_vlm_request_still_carries_a_system_message(
        self, client, prompt_manager, vlm
    ):
        assert client.post("/api/v1/verification/config", json=CREATE_PAYLOAD).status_code == 201

        messages = verify(prompt_manager, vlm)

        assert [m["role"] for m in messages] == ["system", "user"]
        assert content_of(messages, "system") == DEFAULT_SYSTEM_PROMPT

    def test_the_user_prompt_and_media_are_unaffected(self, client, prompt_manager, vlm):
        client.post("/api/v1/verification/config", json=CREATE_PAYLOAD)

        content = content_of(verify(prompt_manager, vlm), "user")

        assert {"type": "video_url", "video_url": {"url": CLIP_URL}} in content
        assert {"type": "text", "text": USER_PROMPT} in content

    def test_the_api_keeps_reporting_the_field_as_unset(self, client):
        """The default is applied at inference; the store stays honest about
        what the operator actually configured."""
        client.post("/api/v1/verification/config", json=CREATE_PAYLOAD)

        body = client.get(f"/api/v1/verification/config/{ALERT_TYPE}").json()

        assert body["system_prompt"] is None


class TestStoredSystemPromptWins:
    def test_a_created_one_reaches_the_vlm_unchanged(self, client, prompt_manager, vlm):
        client.post(
            "/api/v1/verification/config",
            json={**CREATE_PAYLOAD, "system_prompt": "You are a PPE compliance auditor."},
        )

        messages = verify(prompt_manager, vlm)

        assert content_of(messages, "system") == "You are a PPE compliance auditor."

    def test_an_updated_one_takes_effect_without_a_restart(
        self, client, prompt_manager, vlm
    ):
        client.post("/api/v1/verification/config", json=CREATE_PAYLOAD)
        assert content_of(verify(prompt_manager, vlm), "system") == DEFAULT_SYSTEM_PROMPT

        client.put(
            f"/api/v1/verification/config/{ALERT_TYPE}",
            json={"system_prompt": "You are a PPE compliance auditor."},
        )

        assert content_of(verify(prompt_manager, vlm), "system") == (
            "You are a PPE compliance auditor."
        )

    def test_clearing_it_over_the_api_falls_back_to_the_default(
        self, client, prompt_manager, vlm
    ):
        """``null`` on a PUT clears the stored value, which puts the alert type
        back in the state a create-without-one leaves it in."""
        client.post(
            "/api/v1/verification/config",
            json={**CREATE_PAYLOAD, "system_prompt": "You are a PPE compliance auditor."},
        )
        client.put(
            f"/api/v1/verification/config/{ALERT_TYPE}", json={"system_prompt": None}
        )

        assert client.get(
            f"/api/v1/verification/config/{ALERT_TYPE}"
        ).json()["system_prompt"] is None
        assert content_of(verify(prompt_manager, vlm), "system") == DEFAULT_SYSTEM_PROMPT


    def test_a_blank_one_does_not_count_as_stored(self, client, prompt_manager, vlm):
        """Blank means "I am not setting one", so the default still applies.

        Two layers agree on that — the schema normalizes it to ``None`` at
        ingress and the resolver treats a blank read as unset — because the
        seed file reaches the store without passing through the schema.
        """
        client.post(
            "/api/v1/verification/config", json={**CREATE_PAYLOAD, "system_prompt": "   "}
        )

        assert client.get(
            f"/api/v1/verification/config/{ALERT_TYPE}"
        ).json()["system_prompt"] is None
        assert content_of(verify(prompt_manager, vlm), "system") == DEFAULT_SYSTEM_PROMPT

    def test_padding_never_reaches_the_wire(self, client, prompt_manager, vlm):
        client.post(
            "/api/v1/verification/config",
            json={**CREATE_PAYLOAD, "system_prompt": "  You are a PPE compliance auditor.  "},
        )

        assert content_of(verify(prompt_manager, vlm), "system") == (
            "You are a PPE compliance auditor."
        )


class TestUnconfiguredAlertType:
    def test_nothing_is_sent_and_the_pair_stays_empty(self, prompt_manager, vlm):
        """``(None, None)`` is what the pipeline records as ``no_prompt``; the
        default must not turn an unconfigured alert type into a VLM call."""
        assert prompt_manager.get_prompts_for_message(MESSAGE) == (None, None)

        with pytest.raises(ValueError, match="user_prompt is required"):
            verify(prompt_manager, vlm)

        vlm.client.chat.completions.create.assert_not_called()
