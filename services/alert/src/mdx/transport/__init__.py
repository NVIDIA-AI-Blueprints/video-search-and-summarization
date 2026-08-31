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

"""Concerns every message transport has, belonging to none of them.

Deliberately small, and deliberately not a framework. What lives here is
whatever a second broker would otherwise copy out of the first, judged one piece
at a time: credential resolution qualifies, because reading a password from a
mounted Secret rather than from a rendered config is a deployment requirement
that has nothing to do with which broker is on the other end.

What does not qualify stays with its transport, even when it looks generic. TLS
options are the clearest case: they are ``redis-py`` keyword arguments, so
producing them here would mean inventing neutral names and a table to translate
them back -- a layer to maintain in exchange for nothing a second transport could
use, since it would need its own client's spelling anyway.
"""
