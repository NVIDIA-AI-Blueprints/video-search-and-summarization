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

# Skill: Check and Interpret Test Results

Parse and summarize BDD test results after a test run.

---

## Step 1 — Read the summary line

The pytest console output ends with a summary line, e.g.:
```
47 passed, 3 failed, 1 error in 142.33s
```

Report this line immediately to the user as the top-level outcome.

---

## Step 2 — Identify failures

### From console output
Failed tests appear as:
```
FAILED tests/unit_tests/sensor_management/test_sensor_management_api.py::test_add_sensor - AssertionError: ...
```

Collect: test file path, test function name, error message.

### From JUnit XML
```bash
# Extract failed test names and messages
REPORTS_DIR="$(git rev-parse --show-toplevel)/test/bdd_tests/reports"
python3 - <<EOF
import xml.etree.ElementTree as ET
tree = ET.parse('${REPORTS_DIR}/junit.xml')
for tc in tree.iter('testcase'):
    fail = tc.find('failure')
    err = tc.find('error')
    if fail is not None or err is not None:
        node = fail if fail is not None else err
        print(f"FAIL: {tc.get('classname')}::{tc.get('name')}")
        print(f"  {node.get('message', '')[:200]}")
        print()
EOF
```

### From CSV report
```bash
REPORTS_DIR="$(git rev-parse --show-toplevel)/test/bdd_tests/reports"
awk -F',' 'NR==1 || $2=="FAILED"' "${REPORTS_DIR}/test_results.csv"
```

---

## Step 3 — Diagnose failures

For each failure:

1. **Connection/timeout errors** (`ConnectionRefusedError`, `ReadTimeout`):
   - VIOS service may have restarted or crashed during the run
   - Check: `docker ps | grep vios` and `docker logs <container>`

2. **HTTP 4xx errors** (e.g., `AssertionError: expected 200, got 404`):
   - API endpoint changed or feature not enabled in this build
   - Check the feature flag / module that was built

3. **HTTP 5xx errors**:
   - VIOS internal error — check VIOS container logs
   - `docker logs vios-sensor 2>&1 | tail -100`
   - **503 NC / 504 Upstream Timeout on sensor endpoints**: likely a stale DB entry whose backing NVStreamer video file no longer exists. Follow the diagnostic steps in `guides/troubleshooting.md` → "Stale Database — Sensor Failures (503/504)".

4. **Assertion failures on response body**:
   - Schema or field name changed — check recent API changes
   - Compare actual vs expected in the failure message

5. **Fixture/setup errors** (`ERROR` not `FAILED`):
   - Pre-test state setup failed (e.g., couldn't create test sensor)
   - Often caused by leftover state from a previous failed run

---

## Step 4 — Check VIOS logs for correlated errors

```bash
# Get list of VIOS containers
docker ps --format "{{.Names}}" | grep vios

# Tail logs for a specific container around test time
docker logs <container-name> 2>&1 | tail -200

# Search for ERROR level logs
docker logs <container-name> 2>&1 | grep -E "\[error\]|\[critical\]|FATAL"
```

---

## Step 5 — Report to user

Structure the report as:

```
Result: X passed, Y failed, Z errors

Failures:
1. <test_name> (<feature_file>:<scenario>)
   Error: <message>
   Likely cause: <diagnosis>

2. ...

VIOS log errors (if any):
  [container] <log line>
```
