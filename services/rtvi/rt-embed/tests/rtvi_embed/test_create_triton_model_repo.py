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
"""Unit tests for _precision_flags(), _parse_extra_trtexec_args(), and
_engine_name_extras_suffix() in create_triton_model_repo.py."""

import importlib.util
import os

import pytest

_SCRIPT_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "src",
        "models",
        "custom",
        "samples",
        "cosmos-embed1",
        "create_triton_model_repo.py",
    )
)


def _load_module():
    spec = importlib.util.spec_from_file_location("create_triton_model_repo", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.mark.no_gpu
@pytest.mark.test_in_ci
class TestPrecisionFlags:
    @pytest.mark.parametrize(
        "precision,expected",
        [
            ("fp32", []),
            ("fp16", ["--fp16"]),
            ("bf16", ["--bf16"]),
            ("int8", ["--int8"]),
            ("fp8", ["--fp8"]),
            ("best", ["--best"]),
        ],
    )
    def test_known_precision_returns_expected_flags(self, mod, precision, expected):
        assert mod._precision_flags(precision) == expected

    def test_returns_fresh_list_each_call(self, mod):
        # Caller should be free to mutate (e.g. via list-spread); each call returns a copy.
        first = mod._precision_flags("fp16")
        first.append("--mutated")
        second = mod._precision_flags("fp16")
        assert second == ["--fp16"]

    def test_unknown_precision_raises_value_error(self, mod):
        with pytest.raises(ValueError) as exc:
            mod._precision_flags("int4")
        msg = str(exc.value)
        assert "int4" in msg
        # Error names the supported choices so callers can self-correct.
        for choice in ("fp16", "fp32", "bf16", "int8", "fp8", "best"):
            assert choice in msg


@pytest.mark.no_gpu
@pytest.mark.test_in_ci
class TestParseExtraTrtexecArgs:
    @pytest.mark.parametrize("empty", ["", "   ", "\t", "\n"])
    def test_empty_or_whitespace_returns_empty_list(self, mod, empty):
        assert mod._parse_extra_trtexec_args(empty) == []

    def test_none_returns_empty_list(self, mod):
        assert mod._parse_extra_trtexec_args(None) == []

    def test_simple_flags(self, mod):
        result = mod._parse_extra_trtexec_args("--stronglyTyped --builderOptimizationLevel=5")
        assert result == ["--stronglyTyped", "--builderOptimizationLevel=5"]

    def test_quoted_value_preserved_as_single_token(self, mod):
        # shlex collapses quoting; the quoted value remains one token.
        result = mod._parse_extra_trtexec_args('--profilingVerbosity="layer names only"')
        assert result == ["--profilingVerbosity=layer names only"]

    def test_mismatched_quotes_raise_value_error(self, mod):
        with pytest.raises(ValueError):
            mod._parse_extra_trtexec_args('--foo="unterminated')


@pytest.mark.no_gpu
@pytest.mark.test_in_ci
class TestEngineNameExtrasSuffix:
    def test_empty_list_returns_empty_string(self, mod):
        # Backwards compat with engines built before extras support.
        assert mod._engine_name_extras_suffix([]) == ""

    def test_non_empty_returns_8char_hex_suffix(self, mod):
        suffix = mod._engine_name_extras_suffix(["--stronglyTyped"])
        assert suffix.startswith("_")
        assert len(suffix) == 9  # "_" + 8 hex chars
        # Lowercase hex
        assert all(c in "0123456789abcdef" for c in suffix[1:])

    def test_deterministic_for_same_tokens(self, mod):
        a = mod._engine_name_extras_suffix(["--stronglyTyped", "--builderOptimizationLevel=5"])
        b = mod._engine_name_extras_suffix(["--stronglyTyped", "--builderOptimizationLevel=5"])
        assert a == b

    def test_different_tokens_produce_different_suffix(self, mod):
        a = mod._engine_name_extras_suffix(["--stronglyTyped"])
        b = mod._engine_name_extras_suffix(["--builderOptimizationLevel=5"])
        assert a != b

    def test_token_order_changes_suffix(self, mod):
        # Order matters because trtexec arg order can be semantically different
        # (later flags can override earlier ones). Treat order changes as different builds.
        a = mod._engine_name_extras_suffix(["--a", "--b"])
        b = mod._engine_name_extras_suffix(["--b", "--a"])
        assert a != b
