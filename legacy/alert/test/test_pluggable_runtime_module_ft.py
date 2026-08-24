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

"""Pluggable-parser helpers are in
the wrong module.

Pre-fix layout
--------------
* ``_apply_pluggable_parser_output`` / ``_apply_pluggable_parser_error``
  / ``_safe_json_dumps_parser_output`` / status constants all lived in
  ``enhance_alert_with_vlm`` (the orchestrator).
* ``handlers.direct_media.DirectMediaHandler`` had to lazy-import them
  inside a method body to avoid a circular import between the
  orchestrator module and the handler.

Post-fix layout (these tests lock the behavioural contract)
-----------------------------------------------------------
* Helpers live in :mod:`models.pluggable_parser_runtime`.
* ``handlers.direct_media.direct_media_handler`` imports them at
  *module load time*, no lazy import inside methods.
* Importing ``enhance_alert_with_vlm`` *and*
  ``handlers.direct_media.direct_media_handler`` in the same interpreter
  does not raise ``ImportError`` / ``CircularImportError``.
* The ``enhance_alert_with_vlm`` module re-exports the legacy private
  names (``_apply_pluggable_parser_output``, …) as module-level
  attributes so external tests and diagnostic scripts that monkey-patch
  them continue to work.

These tests deliberately focus on *behaviour* (can the helpers be
imported? do the re-exports still work? do they round-trip correctly?)
rather than structural "symbol X is not defined in file Y" assertions,
which would break on any future refactor that does not change behaviour.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import sys
import types

import pytest


def _fresh_real_direct_media_handler():
    """Load the real ``direct_media_handler`` module from disk, bypassing
    any ``sys.modules['handlers.direct_media']`` stubs that sibling tests
    (e.g. ``test_pluggable_parser_publish_routing.py``) install at
    collection time.

    Sibling tests register ``handlers.direct_media`` as a bare module (not
    a package), so ``importlib.import_module('handlers.direct_media.direct_media_handler')``
    fails with ``ModuleNotFoundError: ... is not a package`` once the
    pytest session has imported those tests.  Loading from disk with an
    isolated package name keeps our assertions running regardless of
    collection order.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "direct_media_handler.py")
    )
    downloader_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "media_downloader.py")
    )

    pkg_name = "_runtime_module_dmh_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.dirname(handler_path)]
        sys.modules[pkg_name] = pkg

        dl_spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.media_downloader", downloader_path
        )
        dl_mod = importlib.util.module_from_spec(dl_spec)
        sys.modules[f"{pkg_name}.media_downloader"] = dl_mod
        dl_spec.loader.exec_module(dl_mod)

        h_spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.direct_media_handler", handler_path
        )
        h_mod = importlib.util.module_from_spec(h_spec)
        sys.modules[f"{pkg_name}.direct_media_handler"] = h_mod
        h_spec.loader.exec_module(h_mod)

    return sys.modules[f"{pkg_name}.direct_media_handler"]


class TestRuntimeModuleImportable:
    """The helpers must be importable from their new home."""

    def test_public_functions_importable_from_runtime_module(self):
        from models.pluggable_parser_runtime import (  # noqa: F401
            apply_pluggable_parser_output,
            apply_pluggable_parser_error,
            safe_json_dumps_parser_output,
        )

    def test_error_source_constants_importable(self):
        from models.pluggable_parser_runtime import (
            ERROR_SOURCE_PLUGGABLE_PARSER,
            ERROR_SOURCE_VLM_SCHEMA,
            ERROR_SOURCE_VLM_API,
            ERROR_SOURCE_MEDIA_DOWNLOAD,
        )

        # Error-source buckets are distinct, non-empty strings.
        buckets = {
            ERROR_SOURCE_PLUGGABLE_PARSER,
            ERROR_SOURCE_VLM_SCHEMA,
            ERROR_SOURCE_VLM_API,
            ERROR_SOURCE_MEDIA_DOWNLOAD,
        }
        assert len(buckets) == 4, "error-source bucket constants must be distinct"
        assert all(isinstance(b, str) and b for b in buckets)

    def test_status_constants_importable(self):
        from models.pluggable_parser_runtime import (
            PLUGGABLE_PARSER_OK_STATUS,
            PLUGGABLE_PARSER_ERROR_STATUS,
        )

        assert PLUGGABLE_PARSER_OK_STATUS == "OK"
        assert PLUGGABLE_PARSER_ERROR_STATUS == "Pluggable parser failed"


class TestDirectMediaHandlerNoLazyImport:
    """DirectMediaHandler must import the helpers at module load time."""

    def test_direct_media_handler_imports_without_lazy_helper_access(self):
        """Just importing the handler module must succeed without calling
        any VLM method. Pre-fix this worked too, but the helpers were
        only bound inside methods — the behaviour we want to lock is
        that importing the handler itself resolves the helpers eagerly,
        not on first use.
        """
        mod = _fresh_real_direct_media_handler()
        assert hasattr(mod, "_apply_pluggable_parser_output"), (
            "helper must be bound at module load, not inside a method"
        )
        assert hasattr(mod, "_apply_pluggable_parser_error")

    def test_helpers_come_from_runtime_module(self):
        """The helpers bound to the handler module point to the
        :mod:`models.pluggable_parser_runtime` implementation, not a
        lazy-imported shadow copy from the orchestrator.
        """
        dmh = _fresh_real_direct_media_handler()

        assert dmh._apply_pluggable_parser_output.__module__ == (
            "models.pluggable_parser_runtime"
        )
        assert dmh._apply_pluggable_parser_error.__module__ == (
            "models.pluggable_parser_runtime"
        )

    def test_no_lazy_import_statement_inside_publish_success(self):
        """Guard against regressing to the previous lazy-import pattern.

        The handler body used to contain
        ``from enhance_alert_with_vlm import (_apply_pluggable_parser_…)``
        inside ``_publish_success``. Locking it out with a source-level
        check is the cheapest way to ensure we do not accidentally
        reintroduce the circular-import workaround.
        """
        dmh = _fresh_real_direct_media_handler()

        source = inspect.getsource(dmh.DirectMediaHandler._publish_success)
        assert "from enhance_alert_with_vlm import" not in source, (
            "DirectMediaHandler._publish_success must not lazy-import from "
            "enhance_alert_with_vlm — helpers were moved to "
            "models.pluggable_parser_runtime so the import can happen at "
            "module load time."
        )


class TestOrchestratorReExportsLegacyNames:
    """``enhance_alert_with_vlm`` keeps the legacy private names as
    module-level aliases so older tests / diagnostic scripts that
    monkey-patched them keep working."""

    def test_legacy_symbols_still_resolve(self):
        import enhance_alert_with_vlm as orchestrator

        assert hasattr(orchestrator, "_apply_pluggable_parser_output")
        assert hasattr(orchestrator, "_apply_pluggable_parser_error")
        assert hasattr(orchestrator, "_safe_json_dumps_parser_output")
        assert hasattr(orchestrator, "_PLUGGABLE_PARSER_OK_STATUS")
        assert hasattr(orchestrator, "_PLUGGABLE_PARSER_ERROR_STATUS")

    def test_legacy_symbols_point_to_runtime_module(self):
        import enhance_alert_with_vlm as orchestrator

        assert orchestrator._apply_pluggable_parser_output.__module__ == (
            "models.pluggable_parser_runtime"
        )
        assert orchestrator._apply_pluggable_parser_error.__module__ == (
            "models.pluggable_parser_runtime"
        )


class TestRuntimeHelpersRoundTrip:
    """End-to-end: the helpers produce the documented Option-B shape."""

    def test_success_helper_populates_vlm_response_and_clears_verdict(self):
        from models.pluggable_parser_runtime import apply_pluggable_parser_output

        msg = {"info": {"sensorId": "cam-1"}}
        apply_pluggable_parser_output(
            msg,
            {"label": "no-spotter", "severity": "high"},
            video_source="https://cdn/x.mp4",
        )

        info = msg["info"]
        # Pluggable path writes parser JSON under the dedicated slot.
        decoded = json.loads(info["vlm_response"])
        assert decoded == {"label": "no-spotter", "severity": "high"}
        # Per Option B contract: verdict is "" and transport metadata flows.
        assert info["verdict"] == ""
        assert info["videoSource"] == "https://cdn/x.mp4"
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == "OK"
        # Default-path slot must NOT leak on the pluggable path.
        assert "reasoning" not in info

    def test_error_helper_sets_error_source_and_verdict(self):
        from models.pluggable_parser_runtime import (
            apply_pluggable_parser_error,
            ERROR_SOURCE_PLUGGABLE_PARSER,
        )

        msg = {"info": {}}
        try:
            raise ValueError("synthetic parser crash")
        except ValueError as e:
            apply_pluggable_parser_error(msg, e, video_source="src")

        info = msg["info"]
        assert info["verdict"] == "verification-failed"
        assert info["verificationResponseCode"] == "500"
        assert "Pluggable parser failed" in info["verificationResponseStatus"]
        # The pluggable-error helper owns the
        # ``pluggable_parser`` error bucket.
        assert info["errorSource"] == ERROR_SOURCE_PLUGGABLE_PARSER

    def test_safe_json_dumps_falls_back_on_non_serializable_values(self):
        from datetime import datetime

        from models.pluggable_parser_runtime import safe_json_dumps_parser_output

        out = safe_json_dumps_parser_output({"when": datetime(2026, 1, 1)})
        # ``default=str`` renders the datetime to ISO-ish; the outer
        # ``json.dumps`` produces a valid JSON string containing that
        # rendered value.
        parsed = json.loads(out)
        assert isinstance(parsed["when"], str)
        assert "2026" in parsed["when"]


class TestNoCircularImport:
    """Both modules must coexist in a single interpreter without a
    circular-import crash."""

    def test_both_modules_importable_together(self):
        # Importing in either order must not raise.
        orchestrator = importlib.import_module("enhance_alert_with_vlm")
        handler_module = _fresh_real_direct_media_handler()
        runtime = importlib.import_module("models.pluggable_parser_runtime")

        assert orchestrator is not None
        assert handler_module is not None
        assert runtime is not None
        # Cross-module reference: the handler's runtime helpers and the
        # orchestrator's legacy alias both resolve to the same object.
        assert (
            handler_module._apply_pluggable_parser_output
            is orchestrator._apply_pluggable_parser_output
        )
