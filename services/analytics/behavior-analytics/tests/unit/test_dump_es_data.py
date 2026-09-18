# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "integration" / "dump_es_data.py"
SPEC = importlib.util.spec_from_file_location("dump_es_data", SCRIPT_PATH)
DUMP_ES_DATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DUMP_ES_DATA)

FROM_TS = "2026-09-18T10:00:00Z"
TO_TS = "2026-09-18T10:10:00Z"

INTERVAL_INDEXES = (
    "mdx-behavior*",
    "mdx-incidents*",
    "mdx-vlm-incidents*",
    "mdx-events*",
)

# Indexes dumped by extract_data_type that store a single timestamp, not an interval.
POINT_IN_TIME_INDEXES = (
    "mdx-raw*",
    "mdx-frames*",
    "mdx-alerts*",
    "mdx-space-utilization*",
)


@pytest.mark.parametrize("index", INTERVAL_INDEXES)
def test_interval_indexes_use_overlap_query_and_end_sort(index):
    body = DUMP_ES_DATA.build_search_body(index, FROM_TS, TO_TS)

    assert body == {
        "size": 1000,
        "sort": [{"end": {"order": "desc"}}],
        "query": {
            "bool": {
                "must": [
                    {"range": {"timestamp": {"lte": TO_TS}}},
                    {"range": {"end": {"gte": FROM_TS}}},
                ]
            }
        },
    }


@pytest.mark.parametrize("index", POINT_IN_TIME_INDEXES)
def test_point_in_time_indexes_use_bounded_timestamp_query_and_timestamp_sort(index):
    body = DUMP_ES_DATA.build_search_body(index, FROM_TS, TO_TS)

    assert body == {
        "size": 1000,
        "sort": [{"timestamp": {"order": "desc"}}],
        "query": {
            "range": {
                "timestamp": {
                    "gte": FROM_TS,
                    "lte": TO_TS,
                }
            }
        },
    }


def test_search_body_honors_custom_size():
    body = DUMP_ES_DATA.build_search_body("mdx-raw*", FROM_TS, TO_TS, size=250)
    assert body["size"] == 250


@pytest.mark.parametrize(
    ("index", "sort_field"),
    [(name, "end") for name in INTERVAL_INDEXES]
    + [(name, "timestamp") for name in POINT_IN_TIME_INDEXES],
)
def test_query_without_window_keeps_index_specific_sort(index, sort_field):
    assert DUMP_ES_DATA.build_search_body(index) == {
        "size": 1000,
        "sort": [{sort_field: {"order": "desc"}}],
        "query": {"match_all": {}},
    }
