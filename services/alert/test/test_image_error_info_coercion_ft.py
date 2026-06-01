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

"""Mode-3 image error path writes raw
ints/lists into ``info``.

The direct-media success tail stringified ``media_metadata`` values
inline, but the pluggable-parser *error* tail copy-pasted the same
"merge metadata into info" loop without the coercion. A crashing custom
parser therefore published ``info["images_processed"] = 3`` (int) and
``info["media_urls"] = ["a", "b"]`` (list) — both violations of the
``map<string, string>`` contract enforced by the nvschema alignment / our ES
ingestion.

The fix pulls both tails through a shared
``_merge_media_metadata_into_info`` helper. These tests lock the
contract: after a parser failure, every value in ``info`` is a ``str``,
and the specific integer / list / None metadata keys are coerced to
their string representations.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import Mock

import pytest


def _load_direct_media_handler():
    """Load DirectMediaHandler directly from disk, isolated from any
    sibling test's ``sys.modules`` stubs."""
    here = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "direct_media_handler.py")
    )
    downloader_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "media_downloader.py")
    )

    pkg_name = "_coerce_dmh_pkg"
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


_handler_module = _load_direct_media_handler()
DirectMediaHandler = _handler_module.DirectMediaHandler
_merge_media_metadata_into_info = _handler_module._merge_media_metadata_into_info


class _ExplodingParser:
    """Always raises — forces the pluggable-parser error tail."""

    def parse(self, raw_response: str) -> dict:
        raise ValueError("boom")


def _make_handler_with_parser():
    return DirectMediaHandler(
        vlm_client=Mock(),
        vlm_enhanced_event_sink=Mock(),
        config={
            "alert_agent": {"media_download": {"enabled": True, "use_verdict": False}},
            "vlm": {"model": "m"},
        },
        pluggable_parser=_ExplodingParser(),
    )


def _base_message():
    return {
        "id": "img-err-1",
        "sensorId": "cam-99",
        "category": "ppe",
        "info": {
            "media_urls": ["https://cdn/a.jpg", "https://cdn/b.jpg"],
            "sensorId": "cam-99",
            "category": "ppe",
        },
    }


class TestMergeMediaMetadataHelper:
    """Unit test for the extracted ``_merge_media_metadata_into_info``
    helper — the single source of truth shared by success and error tails.
    """

    def test_int_value_coerced_to_string(self):
        info = {}
        _merge_media_metadata_into_info(info, {"images_processed": 3})
        assert info["images_processed"] == "3"
        assert isinstance(info["images_processed"], str)

    def test_list_value_json_encoded(self):
        info = {}
        _merge_media_metadata_into_info(info, {"media_urls": ["a", "b"]})
        assert info["media_urls"] == '["a","b"]'

    def test_dict_value_json_encoded(self):
        info = {}
        _merge_media_metadata_into_info(info, {"meta": {"k": "v"}})
        assert info["meta"] == '{"k":"v"}'

    def test_none_value_becomes_empty_string(self):
        info = {}
        _merge_media_metadata_into_info(info, {"missing": None})
        assert info["missing"] == ""

    def test_string_passthrough(self):
        info = {}
        _merge_media_metadata_into_info(info, {"name": "already-string"})
        assert info["name"] == "already-string"

    def test_does_not_overwrite_existing_key(self):
        """Matches the legacy ``if k not in message['info']`` semantics —
        inbound info keys always win over metadata."""
        info = {"images_processed": "inbound"}
        _merge_media_metadata_into_info(info, {"images_processed": 99})
        assert info["images_processed"] == "inbound"

    def test_none_metadata_is_noop(self):
        info = {"foo": "bar"}
        _merge_media_metadata_into_info(info, None)
        assert info == {"foo": "bar"}


class TestImageErrorPathCoercion:
    """Integration — when the pluggable parser raises on the image path,
    every value in the published ``info`` must be a ``str``.
    """

    def test_all_info_values_are_strings_after_parser_failure(self):
        handler = _make_handler_with_parser()
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content="garbage",
            media_type="images",
            media_metadata={
                "media_urls": ["https://cdn/a.jpg", "https://cdn/b.jpg"],
                "images_processed": 2,  # int
                "images_total": 2,      # int
                "probe": None,          # None
            },
        )

        info = msg["info"]
        non_string_values = {
            k: (v, type(v).__name__)
            for k, v in info.items()
            if not isinstance(v, str)
        }
        assert not non_string_values, (
            "every info value must be a string to satisfy ES map<string,string>; "
            f"found non-strings: {non_string_values}"
        )

    def test_int_metadata_keys_are_stringified_after_parser_failure(self):
        handler = _make_handler_with_parser()
        msg = _base_message()

        handler._publish_success(
            msg, "u", "s", "garbage", "images",
            media_metadata={"images_processed": 7, "images_total": 7},
        )

        info = msg["info"]
        assert info["images_processed"] == "7"
        assert info["images_total"] == "7"

    def test_list_metadata_json_encoded_after_parser_failure(self):
        handler = _make_handler_with_parser()
        msg = _base_message()

        handler._publish_success(
            msg, "u", "s", "garbage", "images",
            media_metadata={"media_urls": ["a", "b", "c"]},
        )

        info = msg["info"]
        # Pre-existing ``media_urls`` in info (inbound) must not be
        # overwritten — but the stored value must still be a string to
        # satisfy ES ingestion.
        assert isinstance(info["media_urls"], str)

    def test_sink_receives_error_payload_not_success(self):
        """Regression companion — the sink must see publish_error, not
        publish_success, after a parser failure. The coercion fix must
        not change routing."""
        handler = _make_handler_with_parser()
        msg = _base_message()

        handler._publish_success(
            msg, "u", "s", "garbage", "images",
            media_metadata={"images_processed": 1},
        )

        assert handler.vlm_enhanced_event_sink.publish_error.call_count == 1
        assert handler.vlm_enhanced_event_sink.publish_success.call_count == 0


class TestSuccessPathStillCoerces:
    """Baseline — the success tail (which always coerced) must continue
    to coerce after being refactored through the same helper."""

    class _EchoParser:
        def parse(self, raw_response: str) -> dict:
            return {"label": "ok"}

    def test_success_path_stringifies_media_metadata(self):
        handler = DirectMediaHandler(
            vlm_client=Mock(),
            vlm_enhanced_event_sink=Mock(),
            config={
                "alert_agent": {"media_download": {"enabled": True, "use_verdict": False}},
                "vlm": {"model": "m"},
            },
            pluggable_parser=self._EchoParser(),
        )
        msg = _base_message()

        handler._publish_success(
            msg, "u", "s", "{}", "images",
            media_metadata={"images_processed": 5, "images_total": 5},
        )

        info = msg["info"]
        assert info["images_processed"] == "5"
        assert info["images_total"] == "5"
        non_string = {k: v for k, v in info.items() if not isinstance(v, str)}
        assert not non_string, f"non-string values leaked: {non_string}"
