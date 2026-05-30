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

"""Unit tests for vss_agents.utils.sanitize."""

import pytest

from vss_agents.utils.sanitize import quote_path_segment
from vss_agents.utils.sanitize import safe_basename
from vss_agents.utils.sanitize import scrub_log


def test_scrub_log_replaces_crlf_with_space() -> None:
    forged = "cam1\r\nERROR injected forged log line"
    scrubbed = scrub_log(forged)
    assert "\n" not in scrubbed
    assert "\r" not in scrubbed
    assert scrubbed == "cam1  ERROR injected forged log line"


def test_scrub_log_strips_control_chars_but_keeps_tab() -> None:
    assert scrub_log("a\x00b\x1bc\td") == "ab" + "c\td"


def test_scrub_log_accepts_non_str() -> None:
    assert scrub_log(1234) == "1234"


def test_quote_path_segment_encodes_separators_and_traversal() -> None:
    assert quote_path_segment("../../etc/passwd") == "..%2F..%2Fetc%2Fpasswd"
    assert quote_path_segment("a b/c") == "a%20b%2Fc"


def test_safe_basename_strips_directories() -> None:
    assert safe_basename("../../evil.txt") == "evil.txt"
    assert safe_basename("report.pdf") == "report.pdf"


@pytest.mark.parametrize("bad", ["", ".", "..", "foo/.."])
def test_safe_basename_rejects_traversal_only(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_basename(bad)
