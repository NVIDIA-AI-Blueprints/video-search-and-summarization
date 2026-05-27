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

# Skill: Clean Test State

Remove leftover state from previous test runs to prevent fixture errors on the next run.

Run this before a test suite if a prior run ended with `ERROR` (not `FAILED`) on setup/teardown steps, or if you suspect orphaned test resources.

---

## Step 1 — Identify leftover resources via API

```bash
BASE_URL=<BASE_URL>

# List test sensors (look for names starting with "test_" or "sqa_")
curl -s "$BASE_URL/vios/api/v1/sensors" | python3 -c "
import sys, json
sensors = json.load(sys.stdin).get('sensors', [])
test_sensors = [s for s in sensors if s['name'].startswith(('test_', 'sqa_', 'bdd_'))]
for s in test_sensors:
    print(s['id'], s['name'])
"
```

---

## Step 2 — Delete test sensors

For each test sensor ID found in Step 1:

```bash
curl -s -X DELETE "$BASE_URL/vios/api/v1/sensors/<SENSOR_ID>"
```

---

## Step 3 — Restart VIOS containers (if Step 2 is insufficient)

If orphaned state persists (e.g. dangling recording jobs, stuck pipelines), a container restart clears in-memory state without losing database content:

```bash
cd <PROJECT_ROOT>/deployment
python3 oneclick_dc_deployment_for_dev.py stop
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
```

Wait for health check to return 200 before proceeding:
```bash
MAX_WAIT=120
elapsed=0
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:30888/api/health)" = "200" ]; do
  if [ "$elapsed" -ge "$MAX_WAIT" ]; then
    echo "VIOS not ready after ${MAX_WAIT}s — aborting"
    exit 1
  fi
  sleep 3
  elapsed=$((elapsed + 3))
done
echo "VIOS ready"
```

---

## Step 4 — Verify clean state

```bash
# Confirm no test sensors remain
curl -s "$BASE_URL/vios/api/v1/sensors" | python3 -c "
import sys, json
sensors = json.load(sys.stdin).get('sensors', [])
print(f'{len(sensors)} sensors remaining')
"

# Confirm no active recording jobs
curl -s "$BASE_URL/vios/api/v1/recordings/jobs" | python3 -c "
import sys, json
jobs = json.load(sys.stdin).get('jobs', [])
print(f'{len(jobs)} active recording jobs')
"
```

Expected: counts are 0 or only contain non-test resources.

---

## Notes

- Never delete sensors whose names don't match test prefixes (`test_`, `sqa_`, `bdd_`) without user confirmation.
- If unsure whether a resource is from a test, skip it — a false positive deletion can disrupt a live environment.
- `--fresh-start` (full volume wipe) is a last resort and requires explicit user approval.
