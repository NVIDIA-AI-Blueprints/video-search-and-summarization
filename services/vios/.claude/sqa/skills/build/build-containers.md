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

# Skill: Build VIOS Containers

Build Docker container images from source before deployment. This is the first step in the build-deploy-test cycle and must run before `skills/deployment/deploy.md`.

---

## Module Mapping

Which modules to build depends on the deployment target:

| Deployment target | VIOS modules | Additional containers |
|---|---|---|
| Default (`vios` / stream-processor) | `streamprocessing,sensor` | `nvstreamer`, `ingress` |
| Scaled (`--target scaled`) | `sensor,rtspserver,recorder,livestream,replaystream,storage` | `nvstreamer`, `ingress` |
| NVStreamer only (`--target nvstreamer`) | _(none)_ | `nvstreamer` |
| All (`--target all`) | `streamprocessing,sensor` | `nvstreamer`, `ingress` |

> **`--target all` deploys NVStreamer + stream-processor** — the same containers as the default target. Use this flag when you want to be explicit about deploying both services together. It does NOT deploy the scaled microservices; use `--target scaled` for that.

`mcp` is never built — it takes too long and the pre-built image is used at deploy time.

`streambridge` is unused and never built.

---

## Step 1 — Determine deployment target

Infer the target from user context. Default to `vios` (stream-processor) if not specified.

If the user mentioned "tot" or "top of tree", run `git pull` before proceeding:

```bash
cd <PROJECT_ROOT>
git pull
```

---

## Step 2 — Clean rule (MANDATORY between every build.sh invocation)

Run `cc=0 make clean && cc=1 make clean && cc=2 make clean` **before every individual `build.sh` call**. Stale object files from a previous build will corrupt the next one. This applies regardless of whether you are switching between module types (e.g. VIOS modules → nvstreamer) or running the same module type again.

---

## Step 3 — Build VIOS module containers

Skip this step if the target is `nvstreamer` only.

```bash
cd <PROJECT_ROOT>

# Clean first (see Step 2 rule)
cc=0 make clean && cc=1 make clean && cc=2 make clean

# Default or all targets (stream-processor) — sensor-ms is also deployed by these targets
./build.sh container module=streamprocessing,sensor

# Scaled target only
./build.sh container module=sensor,rtspserver,recorder,livestream,replaystream,storage
```

This step compiles C++ source, packages binaries, and creates Docker images. It is long-running (~10-20 minutes depending on module count and whether build cache is warm).

Run in background and monitor output. The build is complete when `build.sh` exits with code 0.

---

## Step 4 — Build additional containers

Each container is a separate `build.sh` invocation — **clean before each one**.

```bash
cd <PROJECT_ROOT>

# Clean before NVStreamer build
cc=0 make clean && cc=1 make clean && cc=2 make clean
./build.sh container nvstreamer

# Clean before ingress build (skip ingress entirely for nvstreamer-only target)
cc=0 make clean && cc=1 make clean && cc=2 make clean
./build.sh container ingress
```

> **Do not build the MCP container** — `./build.sh container mcp` is intentionally skipped. It takes too long and the pre-built image is used instead.

---

## Step 5 — Capture image tag and verify build output

`build.sh` defaults to tagging images as `latest` unless `tag=<name>` was passed. Capture the tag for use in the deploy step:

```bash
# Determine the tag used (read from build.sh command, or default to "latest")
BUILD_TAG="latest"   # override if tag=<name> was passed to build.sh

# Verify images exist with that tag
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}" \
  | grep -E "vst|nvstreamer|ingress" | grep "$BUILD_TAG"
```

All matching images should show a recent `CreatedAt` timestamp.

---

## Step 6 — Report outcome and handoff tag

Report to the caller:
- Which modules were built
- Build duration
- Any build errors or warnings
- **The tag to use for deployment: `$BUILD_TAG`**

On build failure, stop and report the error. Do not proceed to deployment.

> **Handoff to deploy:** `compose.env` has pinned versioned tags that do NOT match locally built images. Always pass the build tag to the deploy command:
> ```
> --all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>
> ```
> `--all-tag` covers stream-processor and all VST microservices. `--nvstreamer-tag` covers NVStreamer. `build.sh` defaults to `latest`, so unless a custom `tag=` was passed, use `--all-tag latest --nvstreamer-tag latest`.
>
> **`--target all` vs `--all-tag` — do not confuse these:**
> - `--target all` is a **deploy target** that selects which services to run (NVStreamer + stream-processor). It has no effect on image tags.
> - `--all-tag <TAG>` is a **deploy option** that overrides the image tag used for stream-processor and all VST microservices.
> When building from source, you need both: `deploy --target all --all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>`.

---

## Notes

- **Never invoke `make` directly for any purpose.** The only permitted `make` usage is the specific clean command `cc=0 make clean && cc=1 make clean && cc=2 make clean`, run before each `build.sh` invocation. All builds go through `./build.sh`.
- Build uses the GitLab registry base images by default (appropriate for dev/test).
- A custom tag can be passed: `./build.sh container tag=<TAG> module=<modules>`.
- The build must complete successfully before proceeding to `skills/deployment/deploy.md`.
