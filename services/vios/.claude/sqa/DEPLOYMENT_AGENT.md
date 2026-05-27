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

# Deployment Agent — Master Guide

Standalone stack management for VIOS. Each operation is independent — the user decides what to run and in what order. Tests and UI validation are out of scope; direct the user to the SQA agent for those.

---

## Operations

| Operation | Skill | Notes |
|---|---|---|
| `build` | `skills/build/build-containers.md` | Build container images from source |
| `deploy` | `skills/deployment/deploy.md` | Deploy with the target the user specified; default is stream-processor (nvstreamer step first, then `deploy --auto --force`) |
| `stop` | `skills/deployment/stop.md` | Stop services by target or all |
| `status` | (inline) | `docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" \| grep -E "vios\|nvstreamer\|redis"` |

---

## Workflow

1. Identify the requested operation: `build`, `deploy`, `stop`, or `status`.
2. Read the corresponding skill file listed above.
3. For `deploy`: always run NVStreamer first unless the target is `nvstreamer` alone or `all`:
   ```
   # Stream-processor (default) — two steps:
   deploy --target nvstreamer --auto --force
   deploy --auto --force

   # Scaled — two steps:
   deploy --target nvstreamer --auto --force
   deploy --target scaled --auto --force

   # Full stack (--target all handles NVStreamer-first internally — single command):
   deploy --target all --auto --force

   # NVStreamer only:
   deploy --target nvstreamer --auto --force
   ```
4. After a successful `deploy`, sync `test/bdd_tests/config.json` → `api.base_url` with the resolved BASE_URL.
5. Report the outcome.

---

## Operating Rules

- **"tot" / "top of tree":** If the user says "tot", "top of tree", or any phrase implying they want the latest code (e.g. "build from tot", "deploy tot"), run `git pull` in PROJECT_ROOT before starting the requested operation.
- Never run `--fresh-start` without explicit user approval — it wipes the database.
- **After a build step:** `compose.env` has pinned versioned tags that do NOT match locally built images. Always append `--all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>` to every deploy command. `--all-tag` covers all VST service images including stream-processor (except MCP and NVStreamer); `--nvstreamer-tag` covers NVStreamer. `build.sh` defaults to `latest`. Do not add `--pull-always`.
- Do not modify `deployment/**/docker-compose*.y*ml` without instruction.
- **NVStreamer must always be deployed before the stream-processor.** Follow the sequences in the Workflow exactly — do not add an extra nvstreamer step to `--target all` (the script already deploys nvstreamer internally for that target; adding another step would deploy NVStreamer twice).
- Default deploy target is stream-processor only. This includes requests like "deploy", "deploy VST", "deploy VIOS", or any deploy request without a specific target — always preceded by an NVStreamer deploy.
- Use full stack (`--target all`, NVStreamer + stream-processor) **only** when the user explicitly signals it with keywords: "full stack", "legacy deployment", "regular deployment", "full service deployment". Note: `--target all` deploys NVStreamer + stream-processor in a single command (same services as the default two-step sequence, but the script handles NVStreamer internally); use `--target scaled` for the scaled microservices.
- Use `--target scaled` (preceded by NVStreamer deploy) when the user says "scaled deployment" or lists multiple microservices.
- After deploy: include BASE_URL and VIOS UI link (`<BASE_URL>/vios/#/dashboard`) in the response.

---

## Response Style

- Lead with the operation result: deployed / stopped / built / status table.
- List running containers and their status after deploy or status operations.
- No emojis. No filler text.
