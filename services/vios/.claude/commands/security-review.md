---
name: "security-review"
description: "Review staged or recently changed VIOS code for Checkmarx and nspect security issues before committing"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - security
    - checkmarx
    - code-review
  languages:
    - cpp
  domain: security
---

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

Review the recently changed or staged code for security issues that could trigger Checkmarx or nspect findings.

Steps:
1. Run `git diff HEAD` (or `git diff --cached` if changes are staged) to get the changeset.
2. Analyze every changed file for the following categories:

**Hardcoded secrets**
- Credentials, API keys, tokens, passwords in source or config files
- Check `.nspect-allowlist.toml` if adding new third-party binaries or dependencies

**Unsafe C string operations**
- `strcpy`, `strcat`, `sprintf`, `gets`, `scanf` without bounds — flag and suggest `std::string`, `snprintf`, or bounded alternatives

**Format string issues**
- Unvalidated or user-controlled format strings passed to `printf`-family functions

**Buffer and memory safety**
- Unchecked array/pointer indexing — suggest `.at()`, `std::array`, or `std::span`
- Raw `new`/`delete` — suggest RAII / smart pointers
- Integer overflow and signed/unsigned mismatch in size or index arithmetic — flag expressions like `size_t` compared against `int`, or arithmetic that could wrap before a bounds check

**Command injection**
- `system()`, `popen()`, `exec*()` calls with user-derived input

**Path traversal**
- File paths constructed from user input without sanitization

**Input validation**
- Any data entering via REST API that bypasses `SchemaValidator`
- Missing null/bounds checks on external data before use
- If a new REST endpoint lacks schema validation, show how to wire it in: register the schema JSON under `src/framework/web/schemas/` and call `SchemaValidator::validate()` at the handler entry point

**Authentication bypass**
- Any code path that skips `UserAuthHandler`

**Information leakage**
- Logging of credentials, tokens, session IDs, or PII via `LOG()` macros or any other logger

For each finding, report:
- File and line number
- Category of issue
- Why it is a problem
- Concrete fix

If no issues are found, say so clearly and briefly. No emojis.
