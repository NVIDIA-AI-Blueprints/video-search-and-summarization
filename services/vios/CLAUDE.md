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

# CLAUDE.md — VIOS Programming Guidelines

## Communication Style

- Keep responses concise and direct. Lead with the answer; include reasoning only when it is necessary to understand the issue.
- Convey key information only — skip preamble, filler phrases, and summaries of what was just done.
- Do not reproduce large code snippets when a file path and line reference suffices.
- No emojis in responses, code, or comments.

---

## Subagents

| Agent | When to use |
|---|---|
| `gstreamer` | GStreamer pipeline design, element selection, caps negotiation, NvEnc/NvDec, NVIDIA plugin usage, pipeline debugging, WebRTC media paths |
| `vios-sqa` | One-click build → deploy → test → evaluate cycle. Deploys NVStreamer first, then stream-processor (or scaled microservices). Use for any task that involves testing, validation, or UI checks. |
| `vios-deployment` | **Standalone** build/deploy/stop/status commands when NO testing follows. Use for: building containers, deploying a specific target, stopping services, or checking stack status. Do NOT use `vios-deployment` before `vios-sqa` — `vios-sqa` handles build and deployment automatically. |
| `vios-api` | Interact with a running VIOS/VST backend over its REST API (list sensors, fetch timelines, download clips, capture snapshots, add/delete sensors, upload files). Uses `curl`. Does NOT deploy — assumes the stack is already up. |

### /vios-sqa usage

```
/vios-sqa                             # build + deploy (nvstreamer + stream-processor) + run all tests + validate UI
/vios-sqa --skip-build                # deploy + test (skip build, use existing images)
/vios-sqa test sensor_management      # build + deploy + run sensor_management tests only
/vios-sqa results                     # interpret results from the last test run (no build/deploy)
/vios-sqa ui http://<HOST>:30888      # validate the web dashboard and NVStreamer instances
```

### /vios-deployment usage

```
/vios-deployment build                               # build stream-processor + nvstreamer containers from source
/vios-deployment build --target scaled               # build scaled microservices + nvstreamer containers from source
/vios-deployment deploy                              # deploy stream-processor (default; "VST"/"VIOS" = stream-processor)
/vios-deployment deploy --target nvstreamer          # deploy NVStreamer only
/vios-deployment deploy --target all                 # deploy NVStreamer + stream-processor
/vios-deployment deploy --target scaled              # deploy scaled microservices
/vios-deployment deploy --target all --with-minio    # full stack with MinIO
/vios-deployment stop                                # stop all services
/vios-deployment stop --target vios                  # stop VIOS only
/vios-deployment stop --target nvstreamer            # stop NVStreamer only
/vios-deployment status                              # show running containers and their status
```

### /vios-api usage

```
/vios-api list sensors                              # list all configured sensors
/vios-api timelines <streamId>                       # recording timelines for a stream
/vios-api clip <streamId> <startTime> <endTime>      # download a clip (MP4/TS)
/vios-api snapshot <streamId>                        # live snapshot JPEG
/vios-api snapshot <streamId> <startTime>            # historical snapshot at a given timestamp
/vios-api add rtsp <rtsp-url>                        # add an RTSP sensor
/vios-api add onvif <ip> <user> <pass>               # add an ONVIF sensor by IP
/vios-api delete <sensorId>                          # delete a sensor (RTSP-aware cleanup)
/vios-api upload <file> [<sensorId>]                 # upload a video file (PUT v2)
```

The VST endpoint is resolved from the existing deployment context (no manual IP/port entry). All operations follow `.claude/commands/vios-api.md`.

The shared skills and guides live in `.claude/sqa/`.

## Commands

| Command | When to use |
|---|---|
| `/vios-sqa` | One-click build + deploy (nvstreamer + stream-processor) + test + UI validation |
| `/vios-deployment` | Build, deploy, stop, or check status of the VIOS stack |
| `/vios-api` | Query the running VIOS REST API via curl (list sensors, timelines, clips, snapshots, add/delete/upload) |
| `/vios-architecture` | Explain the stream-processor + sensor deployment architecture: containers, ingress, RTSP, file upload, WebRTC, Envoy, SDR, routing, events. **Consult this before debugging any cross-service issue — the right mental model of where work happens (sensor-MS vs pod, Envoy vs nginx, Redis vs Postgres) is usually what tells you which file to open.** |
| `/vios-git` | Authoritative source for VIOS git conventions: branch naming, commit message format, MR description shape, Bug/Jira trailer rules, no-AI-attribution, no-sensitive-data. **Consult before creating any branch, commit, or merge request.** |
| `/security-review` | Before committing — scans changes for Checkmarx/nspect issues |
| `/new-adaptor` | Scaffold a new VMS adaptor following the project pattern |
| `/build-help` | Get the exact `build.sh` command for any build scenario |
| `/bdd-test` | Write feature files and pytest-bdd steps for a new feature |
| `/ui-test` | Run playwright-based UI tests against a running VIOS instance |
| `/setup-maas-mcp` | Install NVIDIA MaaS MCP servers (GitLab, Jira, Confluence, etc.) |
| `/review-mr [<MR-URL-or-IID>]` | Fetch all MR review comments, verify each against the codebase, fix valid issues, and post a reply to every comment |

### MaaS MCP Servers

Some tasks require access to NVIDIA internal systems (GitLab issues, Jira tickets, Confluence docs, NVBugs). These are available via MaaS MCP servers. If Claude cannot access these systems, run `/setup-maas-mcp` to install the relevant servers, then restart Claude Code.

---

## Project Overview

**VIOS (Video IO & Storage)** is an NVIDIA C++17 Video Management System abstraction layer. It provides a pluggable adaptor architecture that normalizes access to multiple VMS backends (Milestone, ONVIF, native sensors) and exposes REST/WebSocket APIs with WebRTC streaming. Part of the NVIDIA Metropolis Media Service (MMS) stack.

Current version: see `src/app/vstmodule.cpp` (update this file on each release, do not hardcode here)

---

## Build System

Use `./build.sh` — **do not invoke `make` directly**. Run `./build.sh help` for all options. Use `/build-help` skill if unsure which flags to use.

Valid modules: `sensor`, `rtspserver`, `recorder`, `livestream`, `replaystream`, `streambridge`, `storage`, `streamprocessing`

**Registry:** Always use the GitLab registry (`gitlab` flag) for dev and testing. NVCR is for production/release only.

---

## Architecture

### Directory Layout

```plaintext
src/
  app/           Application entry points (Main.cpp, server.cpp, vstmodule.cpp)
  framework/     Reusable framework components
    apis/        REST/WebSocket API handlers
    web/         HTTP server (civetweb), WebSocket, schema validation
    media/       GStreamer pipelines, WebRTC streamer, video source graph
    live555/     RTSP client
    protocols/   SOAP, gRPC, Prometheus, Elasticsearch clients
    notification/ Redis, Kafka, MQTT pub/sub
    database/    SQLite + PostgreSQL backends
    StreamSdk/   Stream SDK integration
  modules/       Feature modules (selected at build time via module= flag)
  adaptors/      VMS backend adaptors (pluggable shared libs)
include/         Third-party and SDK headers
prebuilts/       Pre-built static/shared libs per arch (x86_64, aarch64, sbsa)
test/gtests/     Google Test unit tests
test/bdd_tests/  Python BDD tests (pytest-bdd + Gherkin)
deployment/      Kubernetes Helm charts and UCF deployment configs
cicd_files/      CI/CD scripts, Dockerfiles per arch
mcp/             MCP gateway (Python)
```

### Module System

Each module sets its own `CPPFLAGS` defines (e.g., `-DRTSP_SERVER_MODULE`) and links corresponding prebuilt shared libs. Do not assume a module's symbols are present unless its define is active.

### Adaptor Pattern

Adaptors are loaded dynamically via `adaptor_loader.cpp`. Use `/new-adaptor` skill to scaffold one correctly.

---

## Coding Standards

### Language & Standard
- **C++17** — actively prefer modern features over legacy patterns:
  - `std::optional` / `std::variant` over raw pointers or sentinel values
  - RAII and smart pointers (`std::unique_ptr`, `std::shared_ptr`) — avoid raw `new`/`delete`
  - `std::string_view` for read-only strings; range-based for loops; `auto`; `[[nodiscard]]`
  - Structured bindings, `if constexpr`, `std::chrono`, `std::filesystem` where applicable
- All source files must carry the NVIDIA SPDX copyright header

### C++ Quality Rules
- **No raw owning pointers** — use `std::unique_ptr`, `std::shared_ptr`, or RAII wrappers
- **No undefined behavior** — avoid signed integer overflow, out-of-bounds access, dangling references, and uninitialized variables
- **Const correctness** — apply `const` to parameters, member functions, and variables wherever appropriate
- **Include guards** — every header must have `#pragma once` (or traditional include guards)
- **Warnings as errors** — code must compile cleanly under `-Wall -Wextra -Werror`
- **Never silently swallow errors** — every error path must log or propagate; use `[[nodiscard]]` on functions whose return value must be checked
- **Resource management** — every acquisition must have a corresponding release on all code paths, including error paths

### Naming Conventions
- Classes: `PascalCase` — Methods: `camelCase` — Members: `m_camelCase` — Constants: `UPPER_SNAKE_CASE`
- Files match the primary class name (e.g., `WebsocketClient.cpp`)

### Error Handling
- Log via `logger.h` using `LOG(error)` / `LOG(info)` (or appropriate `LOG(level)` macros), not `std::cerr` or `printf`
- Prefer return codes / `std::optional` / callbacks over exceptions in hot paths
- Media pipeline errors must degrade gracefully (reconnect, skip frame)

### Threading
- GStreamer pipeline callbacks run on GStreamer threads — do not block them
- civetweb HTTP/WebSocket handlers must be non-blocking
- Protect shared state with `std::mutex` / `std::shared_mutex`; document lock ordering

### Security
- Validate all external inputs at REST API boundaries via `SchemaValidator`
- Never log credentials, tokens, or PII
- Do not bypass `UserAuthHandler`
- Run `/security-review` before committing any change

### Video Pipeline Guidelines
- **Buffer lifetime** — pipeline buffers may be ref-counted; never hold a raw pointer past the callback that delivers it
- **Timestamp precision** — use `int64_t` for PTS/DTS values (nanoseconds or microseconds); avoid `float`/`double` for media time
- **Hot path allocations** — flag and eliminate unnecessary copies or heap allocations in encode/decode/transmux loops
- **Thread affinity** — many GStreamer and live555 callbacks have thread affinity requirements; do not call pipeline APIs from arbitrary threads
- **Format negotiation** — always verify caps/format compatibility before linking pipeline elements; mismatches produce silent black frames or crashes at runtime
- **Codec constraints** — account for keyframe intervals, B-frame reordering, and profile/level compliance when constructing or reconfiguring pipelines
- **Supported codecs/containers** — H.264, H.265, VP8/VP9, AV1; MP4, MKV, TS; RTSP, WebRTC, HLS, DASH

### Platform-Specific Code
- CUDA/NvEnc/NvDec: `#ifdef USE_NV_ENC` / `#ifdef USE_NV_DEC` (aarch64 only)
- OpenTelemetry: `#ifdef USE_OTEL` (x86_64 only)
- gRPC: `#ifdef USE_GRPC_SERVER` / `#ifdef USE_GRPC_CLIENT` (x86_64 only)
- SBSA vs Jetson: `#ifdef SBSA_PLATFORM` / `#ifdef JETSON_PLATFORM`

---

## Debugging

**Workflow:** reproduce → gather evidence (logs, stack traces) → hypothesize root causes ranked by likelihood → trace code paths through the shim layer → isolate minimal reproduction → fix with clear reasoning → check for regressions.

**Always orient yourself with the architecture before guessing.** Most non-trivial bugs in this stack are cross-service (sensor-MS publishes, SDR routes, Envoy proxies, pod handles) — fixing the wrong layer is the default failure mode. Run `/vios-architecture` (or read `.claude/commands/vios-architecture.md`) to map the symptom to the responsible container, the headers/IDs that gate routing, and the event channel involved before opening source files. The architecture model tells you *where* to look; the code tells you *what* to change.

**Tools:** GDB (stack traces, watchpoints), Valgrind / AddressSanitizer (memory errors), ThreadSanitizer (data races), `perf` / `gprof` (CPU profiling), `GST_DEBUG` + pipeline dot graphs (GStreamer).

---

## Testing

- Unit tests: `test/gtests/` — binary is `vst_test`, built with `-D UNIT_TEST`
- BDD tests: `test/bdd_tests/` — pytest-bdd, requires a running VIOS service; run via `cd test/bdd_tests && poetry run pytest tests/`
- Use `/bdd-test` skill to write tests for new features

---

## CI/CD

- Pipeline: `.gitlab-ci.yml` — stages: `coverity` → `deploy` → `push_helm_chart`
- `/test` folder excluded from nspect scans (`.nspect-allowlist.toml`)
- Code review automation: `.coderabbit.yaml`

### Git Workflow

Git conventions live in **`/vios-git`** (`.claude/commands/vios-git.md`). Consult it before any branch, commit, or MR.

---

## Key Dependencies

| Library | Purpose |
|---|---|
| GStreamer 1.0 | Media pipeline |
| live555 | RTSP client |
| civetweb | HTTP + WebSocket server |
| jsoncpp | JSON serialization |
| libcurl / libxml2 / nvsoap | REST, SOAP, ONVIF |
| gRPC + protobuf | Inter-service RPC (x86_64) |
| libpqxx / sqlite3 | PostgreSQL / embedded DB |
| Redis / Kafka / paho-mqtt | Pub/sub and event streaming |
| Prometheus cpp / OpenTelemetry | Metrics and tracing |
| OpenCV 4 / Boost | Image processing, utilities |

---

## Code Review

Structure feedback as: **Critical** (bugs, memory leaks, UB, thread-safety violations) → **Warnings** (missing error handling, performance issues, style drift) → **Suggestions** (optional improvements).

Focus on recently changed code unless a full audit is requested. Provide concrete examples when suggesting changes. Before proposing a fix that touches a public interface, confirm whether ABI backward compatibility must be maintained.

---

## Clarification Protocol

Before implementing, ask if any of the following are ambiguous:
- Codec, format, or protocol context (e.g. H.264 vs H.265, RTSP vs WebRTC)
- Whether the change must maintain backward ABI compatibility
- Target platform or compiler version constraints
- Whether existing tests need updating or new ones are required

Never assume on changes that could cause ABI breakage or silent behavioral differences.

---

## Cursor Rules

The following `.mdc` files in `.cursor/rules/` are the authoritative deep-reference rules for this project. They are scoped by glob pattern — before editing files in the areas listed below, read the corresponding rule file. It contains implementation-level detail not replicated in this document.

| Files | Rule |
|---|---|
| `**/*.cpp`, `**/*.h`, `**/*.hpp` | `.cursor/rules/vios-cpp-standards.mdc`, `.cursor/rules/vios-error-security.mdc`, `.cursor/rules/vios-platform-code.mdc` |
| `src/app/**` | `.cursor/rules/vios-deep-lifecycle.mdc` |
| `src/adaptors/**`, `src/modules/**`, `src/app/vstmodule.*` | `.cursor/rules/vios-deep-adaptors-modules.mdc` |
| `src/framework/apis/**`, `src/framework/web/**` | `.cursor/rules/vios-deep-api-routing.mdc` |
| `src/framework/utilities/**`, `include/device_manager.h`, `include/error_code.h` | `.cursor/rules/vios-deep-config-threading.mdc` |
| `src/framework/database/**`, `src/framework/notification/**`, `src/framework/protocols/**` | `.cursor/rules/vios-deep-data-notification.mdc` |
| `src/framework/media/**`, `src/framework/live555/**`, `src/framework/webrtc_streamer/**`, `src/framework/stream_monitor/**` | `.cursor/rules/vios-deep-media-pipeline.mdc`, `.cursor/rules/vios-media-pipeline.mdc` |
| `src/framework/stream_monitor/**`, `src/framework/media/video_source/producers/**` | `.cursor/rules/vios-deep-stream-monitor.mdc` |
| `src/modules/stream_recorder/**` | `.cursor/rules/vios-deep-recording.mdc` |
| `src/modules/rtsp_server/**`, `src/modules/sensor_management/**` | `.cursor/rules/vios-deep-rtsp-sensor.mdc` |
| `src/modules/storage_management/**` | `.cursor/rules/vios-deep-storage-mgmt.mdc` |
| `src/modules/webrtc_stream_bridge/**` | `.cursor/rules/vios-deep-stream-bridge.mdc` |
| `src/modules/webrtc_live/**`, `src/framework/webrtc_streamer/**` | `.cursor/rules/vios-deep-webrtc-live.mdc` |
| `src/modules/webrtc_replay/**` | `.cursor/rules/vios-deep-webrtc-replay.mdc` |
| `src/framework/apis/common/vst_common.*`, `src/framework/media/video_source/encoders/image_encoder.*` | `.cursor/rules/vios-deep-image-capture.mdc` |
| `test/**` | `.cursor/rules/vios-testing-cicd.mdc` |
| `deployment/**`, `.claude/sqa/skills/deployment/**`, `.claude/sqa/skills/build/**` | `.cursor/rules/vios-dev.mdc` |
| `test/bdd_tests/**`, `.claude/sqa/**`, `.claude/commands/**`, `.claude/agents/**` | `.cursor/rules/vios-sqa.mdc` |

The four `alwaysApply: true` rules (`vios-overview`, `vios-build-system`, `vios-communication`, `vios-code-review`) are incorporated into this document and do not need to be read separately.

---

## Common Pitfalls

1. **Use `./build.sh`, not `make`** — arch detection, Docker, and module flags won't be set correctly otherwise.
2. **Wrong arch prebuilts** — `PREBUILT_DIR` is `prebuilts/$(arch)/`; wrong arch links silently against the wrong libs.
3. **Missing module define** — features gated by a `CPPFLAGS` define silently no-op if `module=` is omitted.
4. **Kafka consumer depends on `notification_proto`** — enforced in the Makefile; do not remove it.
5. **OpenTelemetry on aarch64/SBSA** — `USE_OTEL=0`; OTEL headers are not available there.
6. **Multi-module plain build** — `module=a,b` without `package` or `container` will error out.

---

## Post-Change Hooks

After modifying files in certain directories, follow up with the corresponding action:

| Changed files | Action |
|---|---|
| `test/bdd_tests/Dockerfile`, `test/bdd_tests/docker-entrypoint.sh`, `test/bdd_tests/pyproject.toml`, `test/bdd_tests/poetry.lock` | Read and follow `.cursor/skills/bdd-container-update/SKILL.md` — bump the BDD container image version, rebuild, push, and update the tag in `cicd_files/docker-compose-test/start_test.sh`. Test code under `test/bdd_tests/{tests,features,scripts,data,conftest.py,config.json}` is bind-mounted at runtime and does NOT require a rebuild. |
