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
"""The absent-vs-unwell predicate a caller uses to decide whether to skip.

Every case here is a real ``httpx.Response``, because the whole point of the
predicate is header semantics — case-insensitivity, absence, a value that
happens to be empty — and a mock would answer however the test asked it to.
"""

import httpx
import pytest

from vss_agents.utils.gateway import GATEWAY_UNAVAILABLE_HEADER
from vss_agents.utils.gateway import gateway_absent_service
from vss_agents.utils.gateway import gateway_reports_service_absent


class TestGatewayReportsServiceAbsent:
    def test_marked_503_is_absent(self):
        response = httpx.Response(503, headers={GATEWAY_UNAVAILABLE_HEADER: "rtvi-cv"})
        assert gateway_reports_service_absent(response) is True
        assert gateway_absent_service(response) == "rtvi-cv"

    def test_header_match_is_case_insensitive(self):
        # HAProxy emits the name lowercase over HTTP/1.1 and titled over
        # HTTP/2; neither the gateway nor this predicate should care.
        response = httpx.Response(503, headers={"X-VSS-Gateway-Unavailable": "lvs"})
        assert gateway_reports_service_absent(response) is True

    def test_bare_503_is_a_real_failure(self):
        """The case a careless fix breaks: deployed, and answering 503 itself."""
        response = httpx.Response(503, text="service unavailable")
        assert gateway_reports_service_absent(response) is False
        assert gateway_absent_service(response) == ""

    @pytest.mark.parametrize("status", [200, 201, 400, 404, 500, 502, 504])
    def test_the_marker_alone_is_not_enough(self, status: int):
        response = httpx.Response(status, headers={GATEWAY_UNAVAILABLE_HEADER: "rtvi-cv"})
        assert gateway_reports_service_absent(response) is False

    def test_marked_503_with_an_empty_value_is_still_absent(self):
        """Presence is the contract; the value is only ever a log detail."""
        response = httpx.Response(503, headers={GATEWAY_UNAVAILABLE_HEADER: ""})
        assert gateway_reports_service_absent(response) is True
        assert gateway_absent_service(response) == ""

    def test_success_is_never_absent(self):
        assert gateway_reports_service_absent(httpx.Response(200, json={"ok": True})) is False

    def test_an_object_without_headers_does_not_raise(self):
        class Bare:
            status_code = 503

        assert gateway_reports_service_absent(Bare()) is False
        assert gateway_absent_service(Bare()) == ""
