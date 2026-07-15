<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# CI fast rebuild: how base and LVS image reuse works

This document explains how the CI pipeline reuses existing Docker images to shorten build time when only application code (or nothing) changed.

---

## 1. Base image: how reuse works

### Flow (CI: `runBaseImageBuild()` in pipeline-helpers.groovy)

Local dev builds the base as `via-engine-base` via `make -C docker/base build`. CI uses `ci/scripts/get_base_docker_img.sh` for registry naming:

- **NGCR tag** = `nvcr.io/.../via-engine-base:<commit>-<platform>[-<arm_platform>][-uncommitted-USER]`
  - `<commit>` = **latest commit that touched any base-related path** (from `git log -n 1 --oneline ${BASE_DIRS}`).
  - `BASE_DIRS` includes `services/video-summarization/docker/base` and `services/video-summarization/LICENSE.3rdparty`.

- **`runBaseImageBuild()`**:
  1. Run `docker pull $(NGCR tag)`.
  2. If **pull succeeds** → reuse; no build.
  3. If **pull fails** → `make -C docker/base build` (tags as `via-engine-base`), then `docker tag via-engine-base $(NGCR tag)` and push.

So the decision is: **reuse if an image with that tag exists in the registry; otherwise build, tag, and push.** The tag is derived only from git history (last commit touching base-related dirs); there is no separate dependency hash.

### Does it track dependency changes?

**Indirectly, via git.** The tag is tied to the last commit that touched any of `BASE_DIRS`. All base-image inputs (Dockerfile, `pdm.lock`, `pyproject.toml`, etc.) live under `docker/base`, which is in `BASE_DIRS`. So any change to base deps changes the commit used in the tag → new tag → pull fails (until that image is pushed) → local build runs. There is no explicit hash of lockfiles; the tag is purely “last commit touching base-related dirs.”

### Summary (base image)

| Question | Answer |
|----------|--------|
| How does it try to reuse? | `docker pull` by tag. |
| How is the tag chosen? | Last commit (7-char hash) that touched `BASE_DIRS`. |
| When does it build? | When pull fails (no image for that tag). |

---

## 2. LVS image: when we reuse and how app code is applied

### How the LVS image is built (normal path)

The pipeline runs `docker build` with `docker/Dockerfile`, using the base image, `TARGETARCH`, and `BUILD_COMMIT_SHA`. The image includes base + fixed `RUN pip install ...` (no Poetry in this project), TritonGdino, app code via `copy_sources.sh` / `package_file_list.txt`, and `copy_configs.sh` from `config/`, plus `start_via.sh`, `VERSION`, and other COPYs.

So a **dependency change** for the LVS image means any change that affects those layers: Dockerfile, pip lines, TritonGdino, copy scripts, or the set of copied files. This project does not use Poetry; the only “deps” are the fixed `pip install` lines and the base image.

### When can the pipeline reuse the last LVS image?

Reuse is allowed when **only “app-only” paths changed** — i.e. no change that would affect image layers (Dockerfile, deps, TritonGdino, copy scripts, or config that is baked in).

**Paths that force a full LVS rebuild:**

- `docker/Dockerfile`
- `docker/package_file_list.txt`, `docker/copy_sources.sh`, `docker/copy_configs.sh`
- `TritonGdino/**`
- `config/**` (used by Dockerfile: copy_configs + explicit COPYs)
- `start_via.sh`, `VERSION`, and other files copied in the Dockerfile

**Paths treated as “app-only” (reuse allowed if only these change):**

- Paths under `src/` that are copied via `package_file_list.txt` (in practice we use “all under `src/`” for the check).

### How we detect “app-only” changes

We use a **path-based rule** so we don’t need to store extra metadata (e.g. layer digests or hashes).

**Option A – Path-based (what we use)**

- Define an app-only list; we use “all paths under `src/`”.
- Before building the LVS image we run a check (see `services/video-summarization/ci/scripts/lvs_app_only_changes.py`):
  - Compare changes from a reference commit to HEAD: `git diff --name-only <ref>..HEAD`.
  - If **every** changed path is under the app-only list → treat as app-only: take the reuse path.
  - If **any** changed path is outside that list → full LVS image build.

Option A avoids storing digests or metadata; the decision is based only on git and the path list.

**Option B – Content hash (not used)**

- An alternative would be to compute a “LVS layer digest” (e.g. hash of Dockerfile + package list + pip packages + TritonGdino tree) and store it (e.g. in a label). If the current digest equals the digest of the image we would reuse, we could reuse. We do not use this so we don’t need to persist or compare digests.

### What happens on the reuse path (CI only)

When reuse is chosen, the pipeline does the following (no change to the LVS image definition or entrypoint; this is CI-only logic):

1. **Decide**: App-only change (path-based check above).
2. **Choose previous image**: Find an ancestor commit whose LVS image exists in the registry and for which changes from that commit to HEAD are app-only; pull that image and tag it as the **current** commit’s image tag.
3. **Prepare app overlay**: Run `docker/copy_sources.sh` and `docker/copy_configs.sh` into a staging dir (same layout as `/opt/nvidia/via`).
4. **Copy into image**: Create a container from the tagged image, `docker cp` the overlay into the container at `/opt/nvidia/via`, then `docker commit` that container as the same image tag and remove the container. The image now has current app code; the image is **not** pushed.
5. **Run tests**: Compose runs using this locally updated image; unit and integration tests run as usual.

So on reuse you get “previous image + current app code” for testing, without a full build or push.

### Summary (LVS image)

| Question | Answer |
|----------|--------|
| Does the LVS Dockerfile use Poetry? | No. It uses fixed `pip install` and COPY. |
| When can we reuse? | When only app-only paths (under `src/`) changed. |
| How is app code applied on reuse? | Overlay is copied into the container and the container is committed as the current image tag (local only; no push). |
| How is “app-only” detected? | Path-based: `git diff --name-only` from a ref to HEAD; if all changed paths are under `src/`, we reuse. |

---

## 3. Implemented behavior: two booleans and four cases

The pipeline computes **canReuseBase** and **canReuseLvs**, then branches as follows:

| canReuseBase | canReuseLvs | Base stage        | LVS stage                    |
|--------------|-------------|-------------------|------------------------------|
| true         | true        | Skip              | Pull previous LVS + overlay  |
| true         | false       | Pull base         | Build LVS                    |
| false        | true        | Build base        | Build LVS (from new base)    |
| false        | false       | Build base        | Build LVS                    |

- **canReuseBase**: True if the base image for the current base-commit tag exists in the registry (`checkBaseImageExists` via manifest inspect).
- **canReuseLvs**: True if we found an ancestor commit whose LVS image exists in the registry and for which changes from that commit to HEAD are app-only (`hasReusableLvsImage` / `findReusableLvsImage`). Reuse is taken only when **both** are true.

### When reuse is skipped (always full build)

Reuse is **disabled** (canReuseBase and canReuseLvs are forced false) when:

- **Release build**: `TAG_NAME` is set (e.g. git tag build).
- **FORCE_REBUILD**: The pipeline parameter `FORCE_REBUILD` is true (useful when you want a fresh image from this commit).
- **Merge commit**: Current HEAD is a merge commit (two parents). Post-merge builds (e.g. right after an MR is merged) always do a full build so the merged state gets a new image in the registry.

### Merge stage

The **merge-multi-arch-manifests** stage (which verifies and merges pushed images) runs only when at least one architecture actually pushed an image. If all architectures took the reuse path, no image is pushed for the current commit, so that stage is skipped (the “current commit” tag would not exist in the registry).

---

## 4. For developers

- To force a full rebuild for a non-release commit (e.g. to get a fresh image from this commit), run the pipeline with **FORCE_REBUILD** set to true.
- Post-merge (merge commit) builds always do a full build; no parameter needed.
- Reuse is intended to shorten CI when you only change app code under `src/`; any change outside that (Dockerfile, config, TritonGdino, etc.) triggers a full base and/or LVS build.
