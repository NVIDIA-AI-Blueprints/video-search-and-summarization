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
Pytest configuration for E2E integration tests
"""

import pytest


def pytest_addoption(parser):
    """Add custom command line options for E2E tests"""
    parser.addoption(
        "--deployment-type",
        action="store",
        default="docker",
        choices=["docker", "kubernetes"],
        help="Deployment type: docker (docker-compose) or kubernetes (helm)",
    )
    parser.addoption(
        "--api-url",
        action="store",
        default=None,
        help="API server URL (default: http://localhost:8000)",
    )
    parser.addoption(
        "--elasticsearch-url",
        action="store",
        default=None,
        help="Elasticsearch URL (default: http://localhost:9200 for docker, http://df-elasticsearch-service:9200 for kubernetes)",
    )
    parser.addoption(
        "--workload-id",
        action="store",
        default="primary_assistant",
        help="Workload ID for test data",
    )
    parser.addoption(
        "--client-id", action="store", default="pytest-e2e-client", help="Client ID for test data"
    )
    parser.addoption(
        "--e2e-timeout",
        action="store",
        type=int,
        default=90,
        help="Timeout in minutes for E2E test completion",
    )


@pytest.fixture(scope="session")
def e2e_config(request):
    """Session-scoped fixture providing E2E test configuration"""
    return {
        "deployment_type": request.config.getoption("--deployment-type"),
        "api_url": request.config.getoption("--api-url"),
        "elasticsearch_url": request.config.getoption("--elasticsearch-url"),
        "workload_id": request.config.getoption("--workload-id"),
        "client_id": request.config.getoption("--client-id"),
        "timeout_minutes": request.config.getoption("--e2e-timeout"),
    }


@pytest.fixture(scope="session")
def embedding_e2e_config(request):
    """Session-scoped fixture for embedding E2E test configuration"""
    return {
        "api_url": request.config.getoption("--api-url"),
        "elasticsearch_url": request.config.getoption("--elasticsearch-url"),
        "workload_id": request.config.getoption("--workload-id") or "embedding-test",
        "client_id": request.config.getoption("--client-id"),
        "timeout_minutes": request.config.getoption("--e2e-timeout"),
        "data_file": "data/embedding_data.jsonl",  # Default for pytest
    }
