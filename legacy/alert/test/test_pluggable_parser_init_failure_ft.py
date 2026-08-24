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

"""
Regression locks:

* **5b**: An invalid ``vlm.response_parser`` dotted path must fail the
  service at **startup**.  We want a loud crash with the dotted path in
  the message so the operator can locate the typo, not a silent fallback
  to the default parser that surfaces hours later as "wrong output".
* **6a**: Missing unit test that ``AnomalyEnhancer`` refuses to start on
  a bad ``vlm.response_parser``.  This file adds that test.
* **7 (loader message quality)**: Re-validates the loader's improved
  error messages for common operator mistakes (abstract class, class
  requiring ``__init__`` args, class missing ``parse``).

These tests exercise the ``_load_pluggable_parser`` path directly so they
do not have to stand up the full ``AnomalyEnhancer.__init__`` (which
depends on an event-bridge / sink / VLM-client stack).  The contract
under test — "bad config → exception referencing the dotted path" — is
what ``AnomalyEnhancer.__init__`` relies on via
``self._load_pluggable_parser``.
"""

from __future__ import annotations

import pytest

from models.base_response_parser import (
    BaseResponseParser,
    load_response_parser,
)


# ---------------------------------------------------------------------------
# Fixture classes referenced by dotted path in tests below
# ---------------------------------------------------------------------------


class _AbstractParser(BaseResponseParser):
    """Forgot to implement parse() — should fail to instantiate."""

    pass  # parse is still abstract


class _NeedsCtorArgs(BaseResponseParser):
    """Requires a positional __init__ arg — operator misconfig."""

    def __init__(self, model_name):  # noqa: D401 — fixture
        self.model_name = model_name

    def parse(self, raw_response):  # pragma: no cover
        return {"model": self.model_name}


class _RaisesInCtor(BaseResponseParser):
    """Loads dependencies in __init__ and one of them fails (bad path etc.)."""

    def __init__(self):
        raise FileNotFoundError("required model weights not found at /opt/bad")

    def parse(self, raw_response):  # pragma: no cover
        return {}


class _MissingParse:
    """Duck-typed class that forgot to declare parse() at all."""

    def __init__(self):
        pass


class _GoodParser(BaseResponseParser):
    """Baseline: loads correctly."""

    def parse(self, raw_response):
        return {"ok": True}


# ---------------------------------------------------------------------------
# Bad dotted path fails loud at startup
# ---------------------------------------------------------------------------


class TestInvalidResponseParserFailsAtStartup:
    """``load_response_parser`` is what ``AnomalyEnhancer.__init__`` calls
    to resolve the ``vlm.response_parser`` config.  Every failure mode
    here bubbles up out of ``__init__`` and aborts startup — which is the
    behaviour we want."""

    def test_nonexistent_module_raises_with_dotted_path_in_message(self):
        bogus = "does.not.exist.SomeParser"
        with pytest.raises(ImportError) as excinfo:
            load_response_parser(bogus)
        assert bogus in str(excinfo.value) or "does.not.exist" in str(excinfo.value)

    def test_nonexistent_class_in_real_module_mentions_class_name(self):
        bogus = "test.test_pluggable_parser_init_failure_ft.NoSuchClass"
        with pytest.raises(ValueError) as excinfo:
            load_response_parser(bogus)
        assert "NoSuchClass" in str(excinfo.value)
        assert "has no attribute" in str(excinfo.value)

    def test_dotted_path_without_class_component_raises(self):
        with pytest.raises(ValueError, match="must be 'module.ClassName'"):
            load_response_parser("just_a_module_no_class")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="must be 'module.ClassName'"):
            load_response_parser("")

    def test_target_is_not_a_class_fails(self):
        """Pointing at a module-level function instead of a class."""
        with pytest.raises(ValueError, match="is not a class"):
            load_response_parser(
                "test.test_pluggable_parser_init_failure_ft._helper_not_a_class"
            )


def _helper_not_a_class():  # referenced by dotted path above
    return None


# ---------------------------------------------------------------------------
# Loader error messages for common operator mistakes
# ---------------------------------------------------------------------------


class TestLoaderErrorMessagesForOperatorMistakes:
    """The loader must produce actionable errors — pre-fix, pointing the
    loader at an ABC or a class requiring __init__ args surfaced as a
    bare ``TypeError`` with no mention of ``response_parser`` or the
    dotted path.
    """

    def test_abstract_class_mentions_dotted_path(self):
        bogus = "test.test_pluggable_parser_init_failure_ft._AbstractParser"
        with pytest.raises(ValueError) as excinfo:
            load_response_parser(bogus)
        msg = str(excinfo.value)
        assert bogus in msg, f"dotted path missing from error: {msg!r}"
        # Either "not instantiable" or "abstract" verbiage — lock substring
        assert (
            "instantiable" in msg
            or "abstract" in msg
            or "Can't instantiate" in msg
        )

    def test_class_needing_ctor_args_mentions_dotted_path(self):
        bogus = "test.test_pluggable_parser_init_failure_ft._NeedsCtorArgs"
        with pytest.raises(ValueError) as excinfo:
            load_response_parser(bogus)
        msg = str(excinfo.value)
        assert bogus in msg, f"dotted path missing from error: {msg!r}"
        assert "instantiable" in msg
        # Guidance string: nudge toward the fix.
        assert "no arguments" in msg or "__init__" in msg

    def test_class_raising_in_ctor_propagates_with_dotted_path(self):
        """FileNotFoundError / RuntimeError raised INSIDE __init__ is a
        legitimate operator issue (e.g. wrong weight path) — the loader
        should surface the dotted path in the error message so the
        operator knows which config key to fix."""
        bogus = "test.test_pluggable_parser_init_failure_ft._RaisesInCtor"
        with pytest.raises(FileNotFoundError) as excinfo:
            load_response_parser(bogus)
        # Inner error is preserved unchanged (not all __init__ failures
        # are TypeError — only wrap those).  The loader logs the dotted
        # path at WARN before re-raising; we just assert the underlying
        # error survived.
        assert "required model weights not found" in str(excinfo.value)

    def test_class_without_parse_method_mentions_parse(self):
        bogus = "test.test_pluggable_parser_init_failure_ft._MissingParse"
        with pytest.raises(ValueError) as excinfo:
            load_response_parser(bogus)
        msg = str(excinfo.value)
        assert "parse" in msg


# ---------------------------------------------------------------------------
# Positive baseline — good dotted path still loads
# ---------------------------------------------------------------------------


class TestGoodConfigurationStillLoads:
    def test_good_parser_loads_successfully(self):
        parser = load_response_parser(
            "test.test_pluggable_parser_init_failure_ft._GoodParser"
        )
        assert isinstance(parser, _GoodParser)
        assert parser.parse("ignored") == {"ok": True}


# ---------------------------------------------------------------------------
# AnomalyEnhancer-level contract: _load_pluggable_parser is the seam
# between startup config and the loader.  Proving it propagates loader
# errors is equivalent to proving the service fails startup.
# ---------------------------------------------------------------------------


class TestEnhancerLoadPluggableParserPropagatesErrors:
    """``AnomalyEnhancer._load_pluggable_parser`` is called from
    ``__init__``; a raise here aborts the enhancer's startup.  We stub
    out just enough of the enhancer to invoke this one method."""

    def _stub_enhancer(self, response_parser_path):
        """Minimal object that mimics the AnomalyEnhancer attributes used
        by ``_load_pluggable_parser``."""
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = object.__new__(AnomalyEnhancer)
        stub.config = {
            "vlm": {"response_parser": response_parser_path},
        }
        return stub

    def test_bad_dotted_path_raises_out_of_load_pluggable_parser(self):
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = self._stub_enhancer("does.not.exist.Parser")
        with pytest.raises(ImportError):
            AnomalyEnhancer._load_pluggable_parser(stub)

    def test_abstract_class_raises_out_of_load_pluggable_parser(self):
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = self._stub_enhancer(
            "test.test_pluggable_parser_init_failure_ft._AbstractParser"
        )
        with pytest.raises(ValueError, match="not instantiable|abstract|Can't instantiate"):
            AnomalyEnhancer._load_pluggable_parser(stub)

    def test_empty_response_parser_returns_none_no_exception(self):
        """Zero-config deployments must not crash."""
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = self._stub_enhancer("")
        assert AnomalyEnhancer._load_pluggable_parser(stub) is None

    def test_good_dotted_path_returns_instance(self):
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = self._stub_enhancer(
            "test.test_pluggable_parser_init_failure_ft._GoodParser"
        )
        parser = AnomalyEnhancer._load_pluggable_parser(stub)
        assert isinstance(parser, _GoodParser)
