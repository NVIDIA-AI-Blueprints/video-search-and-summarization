# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Test fixtures for the API service tests.
"""

import json
import os

import pytest


def load_json_fixture(filename):
    """Load test data from a JSON fixture file."""
    fixture_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),  # tests directory
        "fixtures",
        "recorded_responses",
        filename,
    )
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_records():
    """
    Fixture providing sample records for testing ICL functionality.

    Returns a list of Record objects with different characteristics:
    - Simple conversation
    - Conversation with system message
    - Conversation with tool calls
    - Long conversation

    Loaded from chat_completion.json fixture file.
    """
    records_data = load_json_fixture("chat_completion.json")
    # Convert the loaded data to the expected Record format
    return [
        {"request": record["request"], "response": record["response"]} for record in records_data
    ]


@pytest.fixture
def get_record_by_name():
    """
    Fixture providing a function to get a specific record by its name.

    This allows tests to request specific records by name rather than using
    array indices, making tests more readable and maintainable.
    """
    records_data = load_json_fixture("chat_completion.json")
    records_by_name = {record["name"]: record for record in records_data}

    def _get_record(name):
        if name not in records_by_name:
            raise ValueError(f"No record found with name '{name}'")
        record = records_by_name[name]
        return {"request": record["request"], "response": record["response"]}

    return _get_record


@pytest.fixture
def oversized_record():
    """
    Fixture providing an oversized record that would exceed context limits.

    Loaded from oversized_records.json fixture file.
    """
    return {
        "request": {
            "messages": [{"role": "user", "content": "This is a very large message " * 1000}]
        },
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "This is a large response " * 1000,
                        "tool_calls": None,
                    }
                }
            ]
        },
    }
