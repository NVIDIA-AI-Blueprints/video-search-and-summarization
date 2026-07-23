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
| Default (`vst` / stream-processor; alias `vios`) | `streamprocessing,sensor` | `nvstreamer`, `ingress` |
| NVStreamer only (`--target nvstreamer`) | _(none)_ | `nvstreamer` |
| All (`--target all`) | `streamprocessing,sensor` | `nvstreamer`, `ingress` |

> **`--target all` deploys NVStreamer + stream-processor** — the same containers as the default target plus NVStreamer. Use this flag when you want to be explicit about deploying both services together.

`mcp` is never built — it takes too long and the pre-built image is used at deploy time.

`streambridge` is unused and never built.

---

## Toolchain & base image are automatic — do NOT ask the user about them

`build.sh` compiles inside a **toolchain** container and layers modules on a **base** image. Both are handled automatically — you never need to ask the user about them:

- On the first build, `build.sh` **auto-builds** the toolchain and base if they're missing, then proceeds. On later builds it **detects them on disk and skips** — they are never rebuilt unless deleted.
- So for any build request, just run the `./build.sh container module=…` command directly. Do **not** prompt the user to build the toolchain first, and do **not** ask which toolchain tag to use.
- **Heads-up to surface (don't ask, just mention):** the *first* build on a fresh clone is ~10-15 min longer because it builds the toolchain + base once. Subsequent builds are fast.
- Only deviate when the user explicitly wants a pre-pulled toolchain from a registry — then pass `toolchain-image=<ref>` (or set `X86_BUILD_IMAGE`/`AARCH64_CC_IMAGE`) and add `no-auto-deps` to fail fast instead of building locally. See the main `README.md` "Going further" section.
- `./build.sh all` is the one-shot path (toolchain → base → all modules → NVStreamer) when the user says "build everything".

### Explicitly building / pushing the toolchain

If the user asks to **build the toolchain itself** (e.g. "build the toolchain for x86" / "build and push the arm64 toolchain to our registry"):

```bash
# Build locally
./build.sh toolchain                 # x86_64
./build.sh arch=arm64 toolchain      # aarch64 cross-compile

# Build + push (push REQUIRES a registry-qualified image name)
./build.sh toolchain push=1 toolchain-image=<registry>/vios-build:x86-devel-ubuntu24.04-cuda13.2.0
./build.sh arch=arm64 toolchain push=1 toolchain-image=<registry>/vios-build:aarch64-devel-ubuntu24.04-cuda13.2.0
```

**Ask before pushing if no registry was given.** Pushing the default tag (`vios-build:x86-devel-ubuntu24.04-cuda13.2.0`) targets Docker Hub, which is almost never intended. If the user says "push to registry" without naming one, ask which registry / image path to use, then pass it via `toolchain-image=` (or `X86_BUILD_IMAGE`/`AARCH64_CC_IMAGE`). This is the one build case where you SHOULD ask a clarifying question. The same applies to `base-container push=1` (use `image-registry=<ref>`).

---

## Step 1 — Determine deployment target

Infer the target from user context. Default to `vios` (stream-processor) if not specified.

If the user mentioned "tot" or "top of tree", run `git pull` before proceeding:

```bash
cd <PROJECT_ROOT>
git pull
```

---

## Step 2 — Clean rule (only when switching module set or arch)

Run `./build.sh clean` (add `arch=arm64` when cross-compiling) **only when**:

- switching between module sets that change compile flags (e.g. VIOS modules → nvstreamer, or adding/removing a `module=`), or
- switching architecture (x86_64 ↔ aarch64).

A plain rebuild of the **same** modules does **not** need a clean — Make tracks dependencies incrementally, and forcing a clean every time triggers a slow full recompile.

Notes:

- `./build.sh clean` cleans only the **current arch's** build context. Do **not** clean the other arch — cleaning runs inside that arch's toolchain container, so e.g. cleaning aarch64 on an x86-only host would needlessly require (and pull) the aarch64 cross-compiler image just to delete files.
- It clears C++ object files only; it does **not** remove the cached toolchain/base Docker images (so it never triggers a toolchain rebuild — use `no-cache` for that).

---

## Step 3 — Build VIOS module containers

Skip this step if the target is `nvstreamer` only.

```bash
cd <PROJECT_ROOT>

# Clean ONLY if switching module set or arch (see Step 2). Skip for a same-modules rebuild.
./build.sh clean                  # add arch=arm64 when cross-compiling

# Default or all targets (stream-processor) — sensor-ms is also deployed by these targets
./build.sh container module=streamprocessing,sensor

# Scaled target only
./build.sh container module=sensor,rtspserver,recorder,livestream,replaystream,storage
```

This step compiles C++ source, packages binaries, and creates Docker images. It is long-running (~10-20 minutes depending on module count and whether build cache is warm).

Run in background and monitor output. The build is complete when `build.sh` exits with code 0.

---

## Step 4 — Build additional containers

Each container is a separate `build.sh` invocation. Going from VIOS modules → NVStreamer/ingress **is** a module-set switch, so clean once before the first one (see Step 2).

```bash
cd <PROJECT_ROOT>

# Module-set switch (VIOS modules → nvstreamer): clean once
./build.sh clean                  # add arch=arm64 when cross-compiling
./build.sh container nvstreamer

# Ingress is another container (skip ingress entirely for nvstreamer-only target)
./build.sh container ingress
```

> **Do not build the MCP container** — `./build.sh container mcp` is intentionally skipped. It takes too long and the pre-built image is used instead.

---

## Other build variants

- **Base image, built alone** ("build the base image"): `./build.sh base-container`. To publish it, `./build.sh base-container push=1 image-registry=<registry>` — ask the user for the registry if they didn't name one (default tag would push to Docker Hub).
- **Force rebuild / no cache** ("force rebuild", "rebuild from scratch", "rebuild without cache"): add `no-cache` to any build, e.g. `./build.sh container module=streamprocessing no-cache`. This is the way to force a **toolchain/base rebuild** — they are otherwise cached and skipped. (Deleting the image works too.)
- **Debug build** ("build sensor in debug mode"): `./build.sh debug module=<module>` (compile only) or `./build.sh container debug module=<module>` for a debug container image.

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

- **Never invoke `make` directly.** All builds and cleans go through `./build.sh` — use `./build.sh clean` (see Step 2), not raw `make clean`.
- The toolchain + base images are built locally on first use and cached (see "Toolchain & base image are automatic" above). `./build.sh clean` clears C++ object files only — it does NOT remove the cached toolchain/base Docker images, so cleaning does not trigger a toolchain rebuild.
- A custom tag can be passed: `./build.sh container tag=<TAG> module=<modules>`.
- To publish under a registry, pass `image-registry=<ref>` / `nvstreamer-image=<ref>` / `toolchain-image=<ref>` (or the matching env vars) at build time, then `push=1`. See the main `README.md`.
- The build must complete successfully before proceeding to `skills/deployment/deploy.md`.
