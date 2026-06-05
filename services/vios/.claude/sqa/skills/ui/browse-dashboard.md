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

# Skill: Browse VIOS Web UI

Use Playwright browser tools to navigate, inspect, and interact with the VIOS web dashboard and NVStreamer instances.

---

## Prerequisites

1. **Resolve BASE_URL** — follow the BASE_URL resolution steps in `AGENT.md`.

2. **Check VIOS health:** `curl -s -o /dev/null -w "%{http_code}" http://localhost:30888/api/health`
   - The health endpoint is localhost-only — do not substitute BASE_URL here
   - If `200` → proceed
   - If anything else → execute `skills/deployment/deploy.md` first, wait for health to return `200`, then continue

3. **Resolve NVStreamer instances** — read ports from the nvstreamer compose.env:

```bash
grep "NVIOSREAMER_HTTP_PORT" <PROJECT_ROOT>/deployment/scaling/docker-compose/nvstreamer/compose.env \
  | sort | awk -F'=' '{print $2}'
# e.g. 31000, 31001, 31002, 31003, 31004
```

Also confirm which nvstreamer containers are actually running:
```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep nvstreamer | sort
```

Only validate instances whose containers are running.

---

## Step 1 — Validate VIOS Dashboard

1. Navigate to `<BASE_URL>/vios/#/dashboard`
2. Wait 2–3s for the SPA to render
3. Take a screenshot
4. Check for:
   - "All services available" badge — PASS if present
   - Sensor count tiles visible
   - No blank page

Check network requests filtered to `/api/` — flag any 4xx/5xx (404 on `streambridge/version` is expected and not a failure).

---

## Step 2 — Validate NVStreamer Instances

For each running NVStreamer instance, derive its URL:
```
HOST = <host extracted from BASE_URL>
NVStreamer-N URL = http://<HOST>:<NVIOSREAMER_HTTP_PORT_N>/#/dashboard
```

For each instance:
1. Navigate to `http://<HOST>:<PORT>/#/dashboard`
2. Wait 2–3s for the page to render
3. Take a screenshot named `sqa-nvstreamer-<N>.png`
4. Check for:
   - Dashboard page loads (not blank, not error page)
   - Navigation sidebar visible
   - No JS errors in `browser_console_messages`

**Pass criteria:** Page renders with a recognizable NVStreamer dashboard UI.
**Fail criteria:** Blank page, connection refused, or JS errors that prevent rendering.

---

## Step 3 — Capture network activity for API validation

After VIOS dashboard load, use `browser_network_requests` filtered to `/api/` to see response codes. Flag anything unexpected beyond the known `streambridge/version` 404.

---

## Step 4 — Report findings

Structure the report as:

```
VIOS Dashboard:     PASS/FAIL — <brief description>
NVStreamer-1 (:31000): PASS/FAIL — <brief description>
NVStreamer-2 (:31001): PASS/FAIL — <brief description>
...
```

For each failure include: URL, what was visible, console errors if any.

---

## Page URL Reference

| Page | URL |
|---|---|
| VIOS Dashboard | `<BASE_URL>/vios/#/dashboard` |
| VIOS Sensors | `<BASE_URL>/vios/#/sensors` |
| VIOS Live streams | `<BASE_URL>/vios/#/live-streams` |
| VIOS Recordings | `<BASE_URL>/vios/#/recordings` |
| VIOS Settings | `<BASE_URL>/vios/#/settings` |
| NVStreamer-N | `http://<HOST>:<NVIOSREAMER_HTTP_PORT_N>/#/dashboard` |

---

## Notes

- The VIOS UI is a single-page application — always use `browser_wait_for` after navigation to let Vue/React render.
- NVStreamer ports are defined in `deployment/scaling/docker-compose/nvstreamer/compose.env` as `NVIOSREAMER_HTTP_PORT_1` through `NVIOSREAMER_HTTP_PORT_N`.
- If a NVStreamer container is not running, skip that instance and note it in the report — do not mark as FAIL.
- Screenshots are the primary evidence — take one per instance at each significant state change.
