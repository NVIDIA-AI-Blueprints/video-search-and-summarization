# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""--base-url single-origin endpoint derivation tests."""

from cli.search import _base_url_endpoints
from cli.search import _parse_args
from cli.search import _runtime_from_args


def test_base_url_derives_all_service_endpoints() -> None:
    endpoints = _base_url_endpoints("http://host.example:7777/")
    assert endpoints == {
        "es_endpoint": "http://host.example:7777/elasticsearch",
        "behavior_es_endpoint": "http://host.example:7777/elasticsearch",
        "cosmos_embed_endpoint": "http://host.example:7777/cosmos-embed",
        "rtvi_cv_endpoint": "http://host.example:7777/rtvi-cv",
        "vst_internal_url": "http://host.example:7777",
        "vst_external_url": "http://host.example:7777",
    }


def test_base_url_satisfies_required_runtime_args() -> None:
    args = _parse_args(["--base-url", "http://host.example:7777", "--query", "forklift"], operation="run")
    runtime = _runtime_from_args(args)
    assert runtime.es_endpoint == "http://host.example:7777/elasticsearch"
    assert runtime.rtvi_cv_endpoint == "http://host.example:7777/rtvi-cv"
    assert runtime.vst_external_url == "http://host.example:7777"


def test_explicit_endpoint_flags_override_base_url() -> None:
    args = _parse_args(
        [
            "--base-url",
            "http://host.example:7777",
            "--es-endpoint",
            "http://other-es:9200",
            "--query",
            "forklift",
        ],
        operation="run",
    )
    runtime = _runtime_from_args(args)
    assert runtime.es_endpoint == "http://other-es:9200"
    # Un-overridden endpoints still derive from the base URL.
    assert runtime.cosmos_embed_endpoint == "http://host.example:7777/cosmos-embed"
