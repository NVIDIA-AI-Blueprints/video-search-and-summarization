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

# Decision Trees

Use these trees to decide which skill to run and which test scope to choose.

---

## Decision Tree 1: What should I do first?

```
User request received
│
├─ vios-sqa agent (build → deploy → test is always chained):
│   ├─ "run tests" / "verify" / "regression" / "test [feature]"
│   │   └─ → build-containers.md → deploy.md → run-bdd-tests.md → check-results.md
│   │   └─ (see Decision Tree 2 for scope)
│   ├─ "open UI" / "show dashboard" / "verify UI"
│   │   └─ → build-containers.md → deploy.md → browse-dashboard.md
│   └─ "check results" / "what failed" / "show report"
│       └─ → skills/testing/check-results.md (no build/deploy needed)
│
├─ vios-deployment agent (each action is independent):
│   ├─ "build" / "build containers" / "rebuild"
│   │   └─ → skills/build/build-containers.md
│   ├─ "deploy" / "start" / "bring up" / "set up environment"
│   │   └─ → skills/deployment/deploy.md
│   ├─ "stop" / "tear down" / "shut down"
│   │   └─ → skills/deployment/stop.md
│   └─ "status" / "check health"
│       └─ → docker ps + health check
│
└─ Deployment or test failure → guides/troubleshooting.md
```

---

## Decision Tree 2: Which BDD tests to run?

```
What changed or what needs to be verified?
│
├─ Camera / sensor CRUD, sensor configuration
│   └─ tests/unit_tests/sensor_management/
│
├─ Storage pools, storage policies, disk management
│   └─ tests/unit_tests/storage_management/
│
├─ Live video streaming, WebRTC, stream quality
│   ├─ API-level → tests/unit_tests/live_stream/
│   └─ Browser/player-level → skills/ui/browse-dashboard.md + tests/webrtc/
│
├─ Recorded video playback, clip export
│   └─ tests/unit_tests/replay_stream/
│
├─ RTSP proxy endpoints
│   └─ tests/unit_tests/rtsp_proxy/
│
├─ Recording jobs, schedules, retention
│   └─ tests/unit_tests/stream_recorder/
│
├─ MCP gateway / tool integration
│   └─ tests/unit_tests/mcp_gateway/
│
├─ File upload to VIOS
│   └─ tests/file_upload/
│
├─ File download / export
│   └─ tests/file_download/
│
├─ Snapshot / picture capture
│   └─ tests/picture/
│
├─ Performance / latency validation
│   └─ tests/perf/
│
├─ "sanity" / "sanity tests" / "sanity check"
│   └─ tests/ (entire BDD suite — use -n auto for parallel execution)
│
├─ Unknown scope or broad change
│   └─ tests/unit_tests/ (all unit tests — ~5–15 min)
│
└─ Full release regression
    └─ tests/ (entire suite — use -n auto for parallel execution)
```

---

## Decision Tree 3: Is a test failure a product bug or environment issue?

```
Test failed
│
├─ Many tests fail at once (>5 unrelated failures)
│   └─ Likely environment issue
│       ├─ Check VIOS health endpoint
│       ├─ Check docker ps for restarting containers
│       └─ → guides/troubleshooting.md
│
├─ Failure message: ConnectionError / Timeout / 503
│   └─ VIOS not reachable or overloaded
│       ├─ Check docker logs for the relevant service
│       └─ → guides/troubleshooting.md
│
├─ Failure message: 401 Unauthorized
│   └─ Auth issue — check credentials in config.json
│
├─ Failure message: 404 Not Found
│   └─ API endpoint missing — module not deployed or API changed
│
├─ Failure message: 4xx with body / AssertionError on response field
│   └─ Likely product bug — report with request/response details
│
├─ Failure message: 5xx Internal Server Error
│   └─ VIOS crashed — check docker logs for the container
│       └─ If crash, likely product bug — collect stack trace
│
└─ Single isolated failure, all others pass
    └─ Likely a product bug in that specific feature
        └─ Check the feature file for the scenario and report to dev team
```

---

## Decision Tree 4: Deploy with what flags?

NVStreamer must always be deployed before the stream-processor. See sequences below.

```
Deployment target?
│
├─ Default / "deploy" / "deploy VST" / "deploy VIOS" / no specific target
│   └─ Step 1: deploy --target nvstreamer --auto --force
│      Step 2: deploy --auto --force
│      ("VST" and "VIOS" always mean stream-processor, not the full stack)
│
├─ Explicit full stack keywords:
│   "full stack", "legacy deployment", "regular deployment",
│   "full service deployment", or mentions live / replay / storage /
│   recorder / rtsp services by name
│   └─ deploy --target all --auto --force
│      (NVStreamer-first handled internally by --target all)
│
├─ "scaled deployment" / "scaled" / lists multiple microservices
│   └─ Step 1: deploy --target nvstreamer --auto --force
│      Step 2: deploy --target scaled --auto --force
│
├─ "deploy NVStreamer" / NVStreamer only
│   └─ deploy --target nvstreamer --auto --force
│
├─ After a build (any target)
│   └─ Append to each deploy command:
│      --all-tag <BUILD_TAG>        (covers all VIOS service images)
│      --nvstreamer-tag <BUILD_TAG> (covers NVStreamer)
│      BUILD_TAG is from build-containers.md Step 6 (default: "latest")
│      See deploy.md Step 1b for the standalone-deploy probe path
│
├─ Test file upload/download features
│   └─ Add --with-minio
│
├─ Test metrics / alerting
│   └─ Add --with-monitoring
│
├─ Test a specific image build
│   └─ Add --all-tag <TAG> or per-service tag flag
│
└─ Clean slate (WARNING: destroys data)
    └─ --fresh-start — CONFIRM WITH USER FIRST
```
