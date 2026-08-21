#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for eval_parity_scope."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval_parity_scope as eps  # noqa: E402


@pytest.mark.parametrize(
    "text,expected",
    [
        ("docker cp the file into the container", "HOST"),
        # HOST wins over FILE: the host requirement is the binding constraint.
        ("docker exec vss-x test -f /tmp/model.pt", "HOST"),
        # FILE wins over NET: this shadowing is what hid every FILE check.
        ("the stream url is a local file path under /home/vst/data", "FILE"),
        ("test -f ~/.ngc/config", "FILE"),
        ("curl -sf http://localhost:8000/health returns 200", "NET"),
        ("the final reply answers the question without errors", "PROSE"),
    ],
)
def test_classify_check_precedence(text, expected):
    assert eps.classify_check(text) == expected


def test_enumeration_finds_a_spec_dir_with_no_skill_md(tmp_path, monkeypatch):
    """Enumeration used to derive skills from SKILL.md while manual `*` dispatch
    walks directories. Every eval-bearing directory today has a SKILL.md, so only
    a fixture without one can tell the two implementations apart."""
    (tmp_path / "skills" / "ghost" / "evals").mkdir(parents=True)
    (tmp_path / "skills" / "ghost" / "evals" / "s.json").write_text("{}", encoding="utf-8")
    assert not (tmp_path / "skills" / "ghost" / "SKILL.md").exists()
    monkeypatch.setattr(eps, "REPO_ROOT", tmp_path)
    assert "ghost" in eps.all_skills()


def test_missing_skills_root_is_loud_not_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(eps, "REPO_ROOT", tmp_path)
    with pytest.raises(eps.SpecError):
        eps.all_skills()


def _write_spec(tmp_path, cells):
    import json

    (tmp_path / "spec.json").write_text(json.dumps({"expects": cells}), encoding="utf-8")


def test_manifest_detects_a_check_moving_between_cells(tmp_path, monkeypatch):
    """The reason the manifest exists. Reward is per cell (passed / len(checks)),
    so moving a check between cells changes scores -- while every aggregate the
    inventory reports stays identical."""
    monkeypatch.setattr(eps, "REPO_ROOT", tmp_path)

    _write_spec(tmp_path, [{"checks": ["alpha", "beta"]}, {"checks": ["gamma"]}])
    a = eps.scan_spec("spec.json")
    _write_spec(tmp_path, [{"checks": ["alpha"]}, {"checks": ["beta", "gamma"]}])
    b = eps.scan_spec("spec.json")

    # Every aggregate is unchanged...
    assert (a["cells"], a["checks"], a["scopes"]) == (b["cells"], b["checks"], b["scopes"])

    # ...but the manifest is not.
    totals = {"specs": 1, "cells": 2, "checks": 3}
    ma = eps.manifest({**totals, "by_spec": [a]})
    mb = eps.manifest({**totals, "by_spec": [b]})
    assert ma != mb
    assert [c["check_count"] for c in ma["specs"][0]["cells"]] == [2, 1]
    assert [c["check_count"] for c in mb["specs"][0]["cells"]] == [1, 2]


def test_manifest_is_deterministic():
    assert eps.manifest(eps.scan()) == eps.manifest(eps.scan())


def test_manifest_excludes_the_lexical_scope_hint():
    """Scope is a hint that will change as the classifier improves; pinning it
    would produce diffs unrelated to the corpus changing."""
    dumped = str(eps.manifest(eps.scan()))
    assert "HOST" not in dumped and "PROSE" not in dumped


def test_unreadable_spec_raises_spec_error(tmp_path, monkeypatch):
    monkeypatch.setattr(eps, "REPO_ROOT", tmp_path)
    with pytest.raises(eps.SpecError) as exc:
        eps.scan_spec("does-not-exist.json")
    assert "unreadable" in str(exc.value)


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ("[]", "top level must be an object"),
        ('{"expects": {}}', "'expects' must be a list"),
        ('{"expects": ["nope"]}', "cell must be an object"),
        ('{"expects": [{"checks": "abc"}]}', "'checks' must be a list"),
        ('{"expects": [{"checks": [1]}]}', "must be a string"),
        ("{not json", "invalid JSON"),
    ],
)
def test_malformed_specs_fail_loudly(tmp_path, monkeypatch, payload, fragment):
    """A damaged corpus must never silently undercount — every downstream gate
    sources its numbers from this scan."""
    p = tmp_path / "bad.json"
    p.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(eps, "REPO_ROOT", tmp_path)
    with pytest.raises(eps.SpecError) as exc:
        eps.scan_spec("bad.json")
    assert fragment in str(exc.value)
