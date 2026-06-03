---
name: "ui-test"
description: "Run VIOS UI tests using the playwright plugin"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - testing
    - ui
    - playwright
    - webrtc
  languages:
    - javascript
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

Ask the user for the full VIOS UI base URL including any path prefix (e.g. `http://${HOST}:${PORT}/${PATH}`) if not already provided. Store it as BASE_URL for use in all navigation steps below.

Then run the following test scenarios in order. Report pass/fail for each.

---

## Scenario 1: Live Stream

1. Navigate to `BASE_URL/#/live-streams`
2. Open the "Select Sensors" dropdown
3. Pick any 2 sensors from the list (use the first two available)
4. Wait for stream cards to appear (up to 10 seconds) before checking console logs
5. For each selected sensor, verify:
   - A stream card appears with the sensor name
   - Console logs show `Stream status update received: PLAYING`
   - Console logs show `on First FrameReceived` or FPS logs (e.g. `<sensor> fps: N`)
   - No WebRTC connection errors in console
6. Take a screenshot as evidence

**Pass criteria:** Both sensors show PLAYING status and FPS > 0
**Fail criteria:** No sensors listed in dropdown, any sensor fails to reach PLAYING state, shows WebRTC *connection* errors, or video element never receives frames. Note: `WebRTC Issue detected: {type: cpu}` is a quality/degradation warning, not a connection failure — do not treat it as a FAIL.

---

## Scenario 2: Recorded / Replay Stream

1. Navigate to `BASE_URL/#/recorded-streams`
2. Open the "Select Sensors" dropdown
3. If the dropdown is empty (no sensors listed) — mark as FAIL immediately, do not proceed
4. Pick any 2 sensors from the list (use the first two available)
5. Wait up to 10 seconds for stream cards and recording timelines to appear
6. For each selected sensor, verify:
   - A stream card appears with the sensor name
   - A recording timeline is visible (seek bar or timeline segment)
   - Console logs show `Stream status update received: PLAYING`
   - Console logs show `on First FrameReceived` or FPS logs (e.g. `<sensor> fps: N`)
   - No WebRTC connection errors in console
7. Take a screenshot as evidence

**Pass criteria:** Both sensors show PLAYING status, FPS > 0, and recording timeline is visible
**Fail criteria:** No sensors listed in dropdown, any sensor fails to reach PLAYING state, no timeline visible, or WebRTC connection errors

---

## Scenario 3: Media URL Video Generation

1. Fetch the timeline data directly from the storage API: `GET ${HOST_AND_PORT}/vios/api/v1/storage/size?timelines=true` (replace `${HOST_AND_PORT}` with the host:port extracted from BASE_URL). Parse the JSON response to find a sensor with at least one timeline entry where the range is at least 2 minutes long. Extract `startTime` and `endTime` in UTC ISO 8601 format.
   - **Important:** The UI timeline slider displays times in local timezone — do not use those values. Always use the UTC times from this API response.
   - If the API returns no sensors with timelines — mark as FAIL immediately.
2. Navigate to `BASE_URL/#/experimental`
3. Click the "Media URL" tab
4. If the "Select Sensor" dropdown is empty — mark as FAIL immediately
5. Open the "Select Sensor" dropdown and select the sensor from step 1
6. Clear the "Start Time (ISO 8601)" field and enter the `startTime` value from the API (UTC, ISO 8601)
7. Clear the "End Time (ISO 8601)" field and enter `startTime + 2 minutes` (must not exceed the `endTime` from the API)
8. Click "Generate Video URL"
9. Wait up to 5 seconds for the URL to appear
10. Verify a video URL is generated and visible in the UI
11. Navigate to the generated video URL in the browser
12. Verify the response is successful (HTTP 200, video plays, or page does not return an error)
13. Take a screenshot with the video playing or the generated URL visible

**Pass criteria:** Video URL is generated, navigating to it returns a valid response (not 404/403/500)
**Fail criteria:** No sensors in dropdown, no timelines from API, URL generation fails, or navigating to URL returns an error

---

## Reporting

After all scenarios, output a summary table:

| Scenario | Result | Notes |
|---|---|---|
| Live Stream | PASS/FAIL | details |
| Recorded Stream | PASS/FAIL | details |
| Media URL | PASS/FAIL | details |

Include screenshot paths and any console errors worth flagging.
