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

"""Sanitizers for untrusted (backend- or user-controlled) data."""

from __future__ import annotations


def scrub_log(value: object) -> str:
    """Return ``value`` as a single-line string safe to log (CWE-117).

    Replaces CR/LF with a space and drops C0 control characters except tab.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)
