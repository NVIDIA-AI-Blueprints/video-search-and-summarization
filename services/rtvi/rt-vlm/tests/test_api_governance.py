# SPDX-FileCopyrightText: Copyright (c) 2024, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import os
import subprocess
import tempfile

import pytest

from tests.tests_common import ViaTestServer

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

IGNORED_RULES_PATTERN = [
    "nspect-id-definition",
    "no-numeric-ids",
    "nvidia-paths-health",
    "no-path-versioning",
    # StreamAddRequest/StreamRemoveRequest have a "headers" field for event
    # metadata (source, created_at) — spectral misidentifies it as HTTP headers
    "define-cors-origin",
]

# Define server modules to test
SERVER_MODULES = [
    ("rtvi_vlm", "server.rtvi_vlm_server"),
    ("rtvi_embed", "server.rtvi_embed_server"),
    ("alert_verification", "server.alert_verification_server"),
]


@pytest.mark.no_gpu
@pytest.mark.test_in_ci
@pytest.mark.parametrize("server_name,server_module", SERVER_MODULES)
def test_api_governance(server_name, server_module):
    with ViaTestServer(
        "--num-gpus 0"
        " --disable-vlm --model-implementation-path /opt/nvidia/rtvi/rtvi/models/custom/samples/neva"
        " --vlm-model-type custom",
        24000,
        server_module=server_module,
    ) as t:
        errors_found = []
        response = t.get("/openapi.json")
        assert response.status_code == 200
        openapi_schema = response.text
        with tempfile.NamedTemporaryFile("w") as openapi_schema_file:
            openapi_schema_file.write(openapi_schema)
            with tempfile.TemporaryDirectory() as td:
                result = subprocess.run(
                    ["sh", f"{TESTS_DIR}/api_gov_spectral.sh", openapi_schema_file.name],
                    capture_output=True,
                    text=True,
                    cwd=td,
                )
                for line in result.stdout.splitlines():
                    if " error " in line or " warning " in line:
                        is_ignored = False
                        for pattern in IGNORED_RULES_PATTERN:
                            if pattern in line:
                                is_ignored = True
                                break
                        if not is_ignored:
                            errors_found.append(line)
        if errors_found:
            print(f"----- API Gov. Errors found for {server_name} -----")
            print("\n".join(errors_found))
            print(f"----- API Gov. Errors found for {server_name} -----")
        assert errors_found == []
