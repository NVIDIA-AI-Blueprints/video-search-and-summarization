---
name: "vios-dev-sanity"
description: "Execute VIOS/VST sanity test cases from the vios_dev_sanity.xlsx spreadsheet against a running VIOS web UI instance using Playwright. Tests are loaded dynamically from the xlsx — no hardcoded test matrix."
metadata:
  author: "Rahul Bhagwat <rbhagwat@nvidia.com>"
  tags:
    - testing
    - sanity
    - playwright
    - ui
  frameworks:
    - playwright
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

Use the `vios-dev-sanity` agent (via the Agent tool) to execute sanity test cases from `vios_dev_sanity.xlsx` against a running VIOS web UI instance using Playwright.

The agent reads the test plan **dynamically from the xlsx at runtime** — it does not have a hardcoded test matrix. When tests are added or changed in the xlsx, they are picked up automatically.

## Usage

```
/vios-dev-sanity                                               # parse xlsx, show test plan, await instruction
/vios-dev-sanity run all                                       # auto-detect deployment, execute all tests
/vios-dev-sanity run all --url http://<HOST>:30888/vst   # run against a specific deployment
/vios-dev-sanity run all --filter WebRTC                       # run only WebRTC category tests
/vios-dev-sanity run all --filter "Sensor Management"          # run only Sensor Management tests
/vios-dev-sanity run all --xlsx /path/to/custom.xlsx           # use a different xlsx file
/vios-dev-sanity run all --nvstreamer-url http://ip:31000      # specify NvStreamer URL explicitly
/vios-dev-sanity run all --skip-xls-update                     # dry run — do not write results back to xlsx
```

### `--url` flag

Explicitly target a specific VIOS deployment. The agent normalizes it automatically (strips hash fragment and trailing slash):
```
--url http://<HOST>:30888/vst/#/dashboard   # accepted
--url http://<HOST>:30888/vst               # accepted
```

**If omitted**: the agent auto-detects a running local deployment by probing `localhost`, `127.0.0.1`, and all local IPv4 addresses on port 30888. If nothing is found, it starts a deployment automatically using the `vios-deployment` skill.

### `--filter` flag

Substring match against sheet name or category name (case-insensitive). Use to run a subset of tests without editing the xlsx:
```
--filter WebRTC               # only WebRTC category
--filter "Sensor Management"  # only Sensor Management category
--filter Sheet2               # only tests from Sheet2
```

### `--xlsx` flag

Path to the xlsx test plan. Default: agent searches for `vios_dev_sanity.xlsx` in the working directory and parent directories.

### `--nvstreamer-url` flag

NvStreamer base URL. If omitted, the agent derives it from the deployment's `compose.env` file or defaults to `http://<BASE_HOST>:31000`.

### `--update-xls` flag

Accepted for backward compatibility but is now a no-op — results and remarks are **always written back to the xlsx** after execution. Use `--skip-xls-update` to suppress the write (dry-run or read-only environment).

## What the agent does

1. Prints `tail -f /tmp/vios-sanity-progress.log` immediately — open this in a second terminal for live progress
2. Parses the xlsx to build the test list (all sheets, all non-empty rows)
3. Prints a test plan summary (count per sheet, auto-skipped tests)
4. Verifies VIOS service health via `browser_navigate` to dashboard
5. Executes each test case in order, interpreting the "Steps to Verify" column
6. Captures a start timestamp before each test and end timestamp after
7. After each test: prints `[N/TOTAL] Category / Feature — PASS` to the main conversation
8. On FAIL: pulls `docker logs` from `stream-processor-ms` and `sensor-ms` for the test time window, analyzes for errors, and prints a root cause summary to the user immediately
9. Writes real-time progress to `/tmp/vios-sanity-progress.log` throughout
10. Always writes results and remarks back to the xlsx after all tests complete (use `--skip-xls-update` to suppress)
11. Emits a final summary table (PASS/FAIL/BLOCKED/SKIPPED counts + failed test list with root causes)

## Live progress

The agent always creates `/tmp/vios-sanity-progress.log`. Tail it for real-time status without waiting for the run to complete:

```bash
tail -f /tmp/vios-sanity-progress.log
```

Output looks like:
```
=== VIOS Dev Sanity Run: Wed Apr 30 12:00:00 UTC 2026 ===
[12:00:01] Plan loaded: 11 tests (2 auto-skipped)
[12:00:05] Service healthy — starting test execution
[12:00:05] START  [1/9] Sheet1/WebRTC / Live WebRTC Stream
[12:00:38] DONE   [1/9] WebRTC / Live WebRTC Stream — PASS
[12:00:38] START  [2/9] Sheet1/WebRTC / Replay WebRTC Stream
...
[12:08:12] DONE   [9/9] Stream Details / Stream details... — PASS
=== DONE Wed Apr 30 12:08:12 UTC 2026 ===
PASS: 8  FAIL: 0  BLOCKED: 1  SKIPPED: 2
```

Arguments passed by the user: $ARGUMENTS
