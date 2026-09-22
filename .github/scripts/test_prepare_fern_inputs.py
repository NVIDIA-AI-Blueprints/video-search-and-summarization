# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for temporary Fern input preparation."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("prepare_fern_inputs.py")
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("prepare_fern_inputs", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


def make_repo(root: Path, mdx: str, openapi: str = "{}\n") -> None:
    (root / "docs/assets").mkdir(parents=True)
    (root / "docs/example.mdx").write_text(mdx, encoding="utf-8")
    (root / "fern").mkdir()
    (root / "fern/docs.yml").write_text(
        "settings:\n  substitute-env-vars: true\n",
        encoding="utf-8",
    )
    (root / "fern/assets").symlink_to("../docs/assets")
    specification = (
        root
        / "services/analytics/video-analytics-api/src/app/specification"
    )
    specification.mkdir(parents=True)
    (specification / "openapi.json").write_text(openapi, encoding="utf-8")


def test_prepares_literals_and_preserves_allowlisted_substitutions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        source = temporary / "source"
        output = temporary / "prepared"
        make_repo(
            source,
            'Run "${VSS_DATA_DIR}/videos" and show ${VSS_DOCS_GIT_REF}.\n',
            r'{"description": "\u0024{FERN_TOKEN}"}' + "\n",
        )
        changed = PREPARE.prepare_tree(
            source,
            output,
            frozenset({"VSS_DOCS_GIT_REF"}),
        )

        assert changed == 2
        assert (output / "docs/example.mdx").read_text(encoding="utf-8") == (
            'Run "\\$\\{VSS_DATA_DIR\\}/videos" and show '
            "${VSS_DOCS_GIT_REF}.\n"
        )
        openapi = json.loads((output / PREPARE.OPENAPI_PATH).read_text())
        assert openapi["description"] == r"\$\{FERN_TOKEN\}"
        assert (output / "fern/assets").is_symlink()


def test_rejects_generated_escapes_in_authored_sources() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        source = temporary / "source"
        make_repo(source, r"Do not commit \$\{VSS_DATA_DIR\}." + "\n")

        try:
            PREPARE.prepare_tree(source, temporary / "prepared")
        except PREPARE.PreparationError as error:
            assert "generated Fern escape" in str(error)
        else:
            raise AssertionError("expected authored Fern escape to be rejected")


def test_rejects_allowlist_names_outside_docs_namespace() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        source = temporary / "source"
        make_repo(source, "${FERN_TOKEN}\n")

        try:
            PREPARE.prepare_tree(
                source,
                temporary / "prepared",
                frozenset({"FERN_TOKEN"}),
            )
        except PREPARE.PreparationError as error:
            assert "must use the VSS_DOCS_ prefix" in str(error)
        else:
            raise AssertionError("expected unsafe allowlist name to be rejected")


def test_rejects_non_allowlisted_yaml_substitution() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        source = temporary / "source"
        make_repo(source, "No Markdown placeholders.\n")
        (source / "fern/docs.yml").write_text(
            'title: "${FERN_TOKEN}"\n',
            encoding="utf-8",
        )

        try:
            PREPARE.prepare_tree(source, temporary / "prepared")
        except PREPARE.PreparationError as error:
            assert "unintended Fern substitution ${FERN_TOKEN}" in str(error)
        else:
            raise AssertionError("expected YAML substitution to fail closed")


def test_rejects_existing_output_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        source = temporary / "source"
        output = temporary / "prepared"
        make_repo(source, "${VSS_DATA_DIR}\n")
        output.mkdir()

        try:
            PREPARE.prepare_tree(source, output)
        except PREPARE.PreparationError as error:
            assert "output path already exists" in str(error)
        else:
            raise AssertionError("expected existing output path to be rejected")


if __name__ == "__main__":
    test_prepares_literals_and_preserves_allowlisted_substitutions()
    test_rejects_generated_escapes_in_authored_sources()
    test_rejects_allowlist_names_outside_docs_namespace()
    test_rejects_non_allowlisted_yaml_substitution()
    test_rejects_existing_output_path()
