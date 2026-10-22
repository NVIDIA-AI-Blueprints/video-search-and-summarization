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
"""Run the AOI-loop mock microservices: uv run python -m tests.mock_services."""

import os

import uvicorn

from tests.mock_services.app import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("MOCK_HOST", "0.0.0.0"),  # — test mock, container/host use
        port=int(os.environ.get("MOCK_PORT", "9000")),
        log_level="warning",
    )
