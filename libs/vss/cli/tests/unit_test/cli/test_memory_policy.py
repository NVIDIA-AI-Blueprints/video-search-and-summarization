# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Effective static memory policy tests."""

from __future__ import annotations

import pytest

from vss_cli.config import ConfigError
from vss_cli.config import Deployment
from vss_cli.config import MarkdownMemoryConfig
from vss_cli.config import MemoryConfig
from vss_cli.memory_policy import MemoryPolicyInputError
from vss_cli.memory_policy import effective_persist
from vss_cli.memory_policy import resolve_memory_policy


@pytest.mark.parametrize(
    ("memory_config", "no_persist", "expected"),
    [
        (None, False, False),
        (MemoryConfig(enabled=False, persist_by_default=False), False, False),
        (MemoryConfig(enabled=True, persist_by_default=False), False, False),
        (MemoryConfig(), False, True),
        (MemoryConfig(), True, False),
    ],
)
def test_effective_persist(
    memory_config: MemoryConfig | None,
    no_persist: bool,
    expected: bool,
) -> None:
    deployment = Deployment(base_url="http://h", memory=memory_config)
    assert effective_persist(deployment, no_persist=no_persist) is expected


def test_no_deployment_never_persists() -> None:
    assert effective_persist(None, no_persist=False) is False


def _deployment(
    *,
    persist: bool = True,
    markdown: bool = True,
    note_default: bool = False,
) -> Deployment:
    return Deployment(
        base_url="http://h",
        memory=MemoryConfig(
            persist_by_default=persist,
            markdown=MarkdownMemoryConfig(
                enabled=markdown,
                harness="openclaw",
                workspace="/openclaw/workspace" if markdown else None,
                write_by_default=note_default,
            ),
        ),
    )


def test_note_policy_uses_tri_state_override() -> None:
    deployment = _deployment(note_default=True)
    assert resolve_memory_policy(deployment, no_persist=False, note_override=None).write_note is True
    assert resolve_memory_policy(deployment, no_persist=False, note_override=False).write_note is False
    assert resolve_memory_policy(_deployment(), no_persist=False, note_override=True).write_note is True


def test_default_note_is_skipped_under_safe_persistence_opt_out() -> None:
    policy = resolve_memory_policy(_deployment(note_default=True), no_persist=True, note_override=None)
    assert policy.persist is False
    assert policy.write_note is False


def test_explicit_note_cannot_override_persistence_policy() -> None:
    with pytest.raises(MemoryPolicyInputError, match="--no-persist"):
        resolve_memory_policy(_deployment(), no_persist=True, note_override=True)
    with pytest.raises(MemoryPolicyInputError, match="static persistence"):
        resolve_memory_policy(_deployment(persist=False), no_persist=False, note_override=True)


def test_explicit_note_requires_configured_markdown_sink() -> None:
    with pytest.raises(ConfigError, match="Markdown memory is disabled"):
        resolve_memory_policy(_deployment(markdown=False), no_persist=False, note_override=True)
    with pytest.raises(ConfigError, match="not configured"):
        resolve_memory_policy(Deployment(base_url="http://h"), no_persist=False, note_override=True)
