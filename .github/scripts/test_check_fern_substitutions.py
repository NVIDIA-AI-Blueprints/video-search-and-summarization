# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Fern substitution safety check."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("check_fern_substitutions.py")
SPEC = importlib.util.spec_from_file_location("check_fern_substitutions", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_literal_fern_escape_is_allowed() -> None:
    assert CHECK.find_issues(Path("docs/example.mdx"), r"Use \$\{VSS_DATA_DIR\} here.") == []


def test_unescaped_substitution_is_rejected() -> None:
    issues = CHECK.find_issues(Path("docs/example.mdx"), "${VSS_DATA_DIR} here.")
    assert len(issues) == 1
    assert "unintended Fern substitution ${VSS_DATA_DIR}" in issues[0]


def test_prefixed_substitution_still_requires_explicit_allowlisting() -> None:
    issues = CHECK.find_issues(Path("docs/example.mdx"), "${VSS_DOCS_GIT_REF}")
    assert len(issues) == 1
    assert "unintended Fern substitution ${VSS_DOCS_GIT_REF}" in issues[0]


def test_explicitly_allowlisted_docs_substitution_is_allowed() -> None:
    issues = CHECK.find_issues(
        Path("docs/example.mdx"),
        "${VSS_DOCS_GIT_REF}",
        frozenset({"VSS_DOCS_GIT_REF"}),
    )
    assert issues == []


def test_allowlist_rejects_names_outside_docs_namespace() -> None:
    issues = CHECK.find_allowlist_issues(frozenset({"FERN_TOKEN", "VSS_DOCS_GIT_REF"}))
    assert issues == [
        "allowlisted Fern substitution ${FERN_TOKEN} must use the VSS_DOCS_ prefix"
    ]


def test_partial_escape_is_rejected() -> None:
    issues = CHECK.find_issues(Path("docs/example.mdx"), r"Use \$\{VSS_DATA_DIR} here.")
    assert len(issues) == 1
    assert "malformed Fern literal escape" in issues[0]


def test_shell_default_expression_is_not_a_fern_substitution() -> None:
    assert CHECK.find_issues(Path("docs/example.mdx"), "${PORT:-8080}") == []


if __name__ == "__main__":
    test_literal_fern_escape_is_allowed()
    test_unescaped_substitution_is_rejected()
    test_prefixed_substitution_still_requires_explicit_allowlisting()
    test_explicitly_allowlisted_docs_substitution_is_allowed()
    test_allowlist_rejects_names_outside_docs_namespace()
    test_partial_escape_is_rejected()
    test_shell_default_expression_is_not_a_fern_substitution()
