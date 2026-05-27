---
name: "bdd-test"
description: "Write BDD tests (feature file + pytest-bdd step implementations) for a new VIOS feature"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - testing
    - bdd
    - pytest
  languages:
    - python
  frameworks:
    - pytest-bdd
  domain: testing
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

Write BDD tests for the new VIOS feature described by the user.

**Project test structure:**
- Feature files (Gherkin): `test/bdd_tests/features/<category>/<name>.feature`
- Step implementations: `test/bdd_tests/tests/<category>/test_<name>.py`
- Shared fixtures: `test/bdd_tests/tests/unit_tests/conftest.py` (for unit tests) or `test/bdd_tests/tests/<category>/conftest.py`
- Shared utils: `test/bdd_tests/tests/unit_tests/unit_test_utils.py`, `test/bdd_tests/tests/test_utils.py`
- Config loaded from `test/bdd_tests/config.json` via `api_config` and `unit_test_params` fixtures

**Framework:** `pytest-bdd` with `scenarios()` binding. Each test file imports the feature file via `scenarios("../../../features/...")`.

**Steps before writing:**
1. **Check for duplicates first.** Before adding any scenario, search the existing
   suite for an equivalent test so you don't reintroduce one that already passes
   or is owned by another branch:
   - Grep feature files for the API path, the error code, or the behavior phrase
     (e.g., `grep -rn "fullLength" test/bdd_tests/features/`).
   - Grep step impls for the same (`grep -rn "fullLength" test/bdd_tests/tests/`).
   - Check other branches if the user mentions one (`git branch -a | grep <topic>`
     then `git show <branch>:<path>`). The bug-fix branches under
     `fix/prakhar/...` and `fix/<user>/...` often carry regression tests that
     are not yet on `v2.1`.
   - If a similar test exists, **extend it with a new Scenario or Scenario
     Outline row** instead of creating a parallel test file. Two files binding
     to the same `.feature` will collect duplicate tests and at least one will
     fail at runtime because step impls are split across modules.
   - If the requested scenario is already covered, tell the user where and stop
     — do not duplicate.
2. Read the feature file for the closest existing module (e.g., `sensor_management_api.feature`) to match Gherkin style.
3. Read the corresponding test file (e.g., `test_sensor_management_api.py`) to match step implementation patterns.
4. Read `unit_test_utils.py` to understand available helpers (`api_get`, `UnitTestContext`, validators).
5. Understand the new feature's REST API endpoints (ask the user or read the relevant source under `src/framework/apis/` or `src/modules/`).

**Conventions to follow:**
- `Given` steps set up preconditions and store state on `UnitTestContext`
- `When` steps call `api_get` (or POST/PUT equivalent) and store `context.response`
- `Then` steps assert on `context.response.status_code` and validate the body
- Use `logger.debug(...)` for diagnostic output, not `print()`
- Use `api_config["base_url"]` and `unit_test_params.get("timeout", 30)` for all requests
- No hardcoded URLs or timeouts
- No emojis in code or comments

**Output:**
1. The `.feature` file with Gherkin scenarios covering: happy path, error/edge cases, invalid input
2. The `test_<name>.py` step implementation file
3. Any new fixtures needed in `conftest.py`
4. Brief note on what to add to `config.json` if new test parameters are needed
