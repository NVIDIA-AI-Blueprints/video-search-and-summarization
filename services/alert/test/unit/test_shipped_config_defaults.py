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

"""``prompt.default_system_prompt`` in the shipped configs vs. the code constant.

Every shipped config sets the key explicitly, which is what makes it visible
and editable to an operator — but it also means each one overrides
``DEFAULT_SYSTEM_PROMPT``, so editing the constant alone would change no
deployment. This is the tripwire for that: change the constant and it fails
until the shipped configs follow, and vice versa.

The files are *discovered*, not listed, so a profile added later is covered
without anyone remembering to enumerate it here. Discovery is by the directory
conventions that mount the alert service's config — a new profile that invents
its own layout would go unchecked, which is why ``test_discovery_finds_the_
known_shipped_configs`` pins the count discovery is expected to reach.

Nothing here asserts anything about a *deployment's* value: overriding the key
is the supported way to give a verifier stronger framing. The rule applies only
to the configs in this repository.
"""

import glob
import os
import re
import sys

import pytest
import yaml

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, "..", ".."))

sys.path.insert(0, SERVICE_ROOT)

from handlers.prompt_handler.prompt_manager import DEFAULT_SYSTEM_PROMPT  # noqa: E402

# Where an alert-service config lives in this repo. Directory conventions rather
# than filenames: profiles ship theirs under ``vlm-as-verifier/configs/``, Helm
# under the chart's alert service, and the service itself carries the reference
# config plus the per-blueprint ones.
CONFIG_GLOBS = (
    "deploy/**/vlm-as-verifier/configs/*.yml",
    "deploy/**/vlm-as-verifier/configs/*.yaml",
    "deploy/helm/services/alert/configs/*.yml",
    "deploy/helm/services/alert/configs/*.yaml",
    "services/alert/blueprint_config/*.yml",
    "services/alert/blueprint_config/*.yaml",
    "services/alert/config.yaml",
)

# Bumped deliberately when a profile is added or removed. A drop means either a
# config was deleted or the layout moved out from under the globs above — the
# second is the failure mode a pure glob cannot tell you about.
EXPECTED_CONFIG_COUNT = 11


def _discover():
    found = set()
    for pattern in CONFIG_GLOBS:
        found.update(glob.glob(os.path.join(REPO_ROOT, pattern), recursive=True))
    return sorted(os.path.relpath(path, REPO_ROOT) for path in found)


def _load(relative_path):
    """Parse a shipped config, tolerating the Helm copies' Go templating."""
    with open(os.path.join(REPO_ROOT, relative_path)) as handle:
        text = handle.read()
    without_directives = re.sub(r"\{\{-?.*?-?\}\}", "TPL", text)
    body = "\n".join(
        line for line in without_directives.splitlines() if not line.strip().startswith("TPL")
    )
    return yaml.safe_load(body) or {}


DISCOVERED = _discover()


def test_discovery_finds_the_known_shipped_configs():
    """Guards the guard: without this, moving the config tree would turn every
    assertion below into a silent pass over an empty set."""
    assert os.path.isdir(os.path.join(REPO_ROOT, "deploy")), (
        f"no deploy/ under {REPO_ROOT} — this test needs a full repo checkout, "
        f"not a copy of services/alert alone"
    )
    assert len(DISCOVERED) == EXPECTED_CONFIG_COUNT, (
        f"expected {EXPECTED_CONFIG_COUNT} shipped alert configs, found "
        f"{len(DISCOVERED)}: {DISCOVERED}. If a profile was added or removed, "
        f"update EXPECTED_CONFIG_COUNT; if the layout moved, update CONFIG_GLOBS"
    )


@pytest.mark.parametrize("relative_path", DISCOVERED)
def test_the_shipped_default_matches_the_code_constant(relative_path):
    prompt_cfg = _load(relative_path).get("prompt")

    if prompt_cfg is None:
        # A config that does not configure the service-level prompt path at all
        # (realtime alert rules carry their own per-rule system prompts) has
        # nothing to keep in sync; DEFAULT_SYSTEM_PROMPT applies at runtime.
        return

    assert "default_system_prompt" in prompt_cfg, (
        f"{relative_path} configures prompts but has no "
        f"prompt.default_system_prompt; an operator cannot see or edit the "
        f"knob there"
    )
    assert prompt_cfg["default_system_prompt"] == DEFAULT_SYSTEM_PROMPT, (
        f"{relative_path} has drifted from DEFAULT_SYSTEM_PROMPT in "
        f"prompt_manager.py; update whichever of the two is stale"
    )
