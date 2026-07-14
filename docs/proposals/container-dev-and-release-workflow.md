<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Container dev & release workflow — GHCR release sets and the last-green channel

Status: **implementation in progress** (this document describes what this
repository implements and the cross-repository contract it exposes; the
GitLab `ci-vss-oss` acceptance mode and the weekly NGC promotion are owned
outside this repository).

## One direction

> GitHub builds a complete immutable candidate in GHCR → GitLab tests that
> exact candidate nightly → a passing candidate becomes the last-green
> developer version → Thursday promotes the same digests to NGC → nSpect and
> SQA approve GA.

Three rules everything below follows:

1. `develop` stays publicly runnable: its committed coordinates always
   resolve without private credentials or wrapper magic.
2. **Immutable digests — not moving tags — are the source of truth.** The
   release-set manifest and the committed last-green lock are what tests,
   promotion, and rollback point at.
3. A failure never changes the current developer channel.

## What lives in this repository

### 1. Single source of truth for container coordinates

* `deploy/docker/containers.env` — every first-party (`vss-core/*`) image
  coordinate as an env-overridable `${VAR:-default}`. Compose `image:` lines
  carry the same literal defaults inline, so behavior is identical whether or
  not the file is sourced. This was landed as a **no-behavior-change**
  foundation: `docker compose config` output is byte-identical before/after.
* `deploy/docker/container-inventory.json` — the machine-readable inventory:
  logical name, build strategy (`build` / `mirror` / `external-pin`), source
  path, Dockerfile, required platforms, accepted compose basenames.
* Guards (run on every PR in the `Container Coordinates Golden` CI job):
  * `.github/scripts/compose_image_golden.py` — golden test pinning every
    resolved image ref, plus a drift check proving `containers.env` and the
    inline defaults can never diverge silently.
  * `.github/scripts/release_set.py closure` — every first-party compose
    reference must be classified in the inventory (in scope or explicitly
    out), so nothing is silently omitted.
* Known naming debt, tracked for a follow-up migration: the agent tag
  variable is `VSS_AGENT_VERSION` (not `VSS_AGENT_TAG`), and nvstreamer /
  rt-vlm / calibration / video-analytics-ui keep their historical
  `*_IMAGE_TAG` variables. Normalizing them is a rename-only change gated by
  the same golden test.

### 2. Immutable GHCR candidate builds (`build-dev-images.yml`)

Triggered on `push` to `develop` and to repo-local `pull-request/N` branches
(the vetted mirror of PRs — fork code never reaches `packages: write`).

* The build matrix is derived from the inventory (`ghcr_build: true`) by the
  unit-tested `detect_changed_images.py`:
  * develop pushes diff `event.before..HEAD` (never `origin/develop...HEAD`,
    which is empty by construction on a push event);
  * `pull-request/N` branches diff against the develop merge-base;
  * initial pushes, orphaned `before` SHAs (force-push), and changes to the
    build contract itself build **everything** — over-building is safe,
    silently building nothing is not.
* Tags are **immutable only**: `pr-<N>-<sha12>` / `develop-<sha12>`. No
  moving alias is ever written by the build. `ghcr_image_guard.py preflight`
  refuses to overwrite an existing tag with different content (idempotent
  re-runs no-op; network errors fail closed).
* Images are multiarch (`linux/amd64,linux/arm64` per the inventory) and
  stamped with the OCI labels the container-source gate verifies —
  `com.nvidia.vss.source_tree_sha` is the **git tree hash** of the source
  folder (`git rev-parse HEAD:<source_path>`), not the commit SHA.
  `ghcr_image_guard.py verify` reads the labels back through the same code
  path as the gate immediately after the push, so a contract mismatch fails
  the build, not a later promotion.

### 3. The release set (`deploy/docker/release-set.schema.json`)

Changed-only builds plus one global tag can never describe a complete stack,
and multi-repository alias updates are not atomic. The release set fixes
both: one JSON manifest per candidate that records **every** in-scope image
as either freshly `build`/`mirror` (with digest, platforms, provenance) or
explicitly `reuse-pinned` at its current committed coordinate.

* `release_set.py fragment` — emitted per built image by the workflow,
  validated against the inventory (platform closure, tree-sha shape).
* `release_set.py assemble` — merges fragments + reuse entries, computes the
  content-addressed `release_set_id`, validates completeness, uploads
  `release-set.json` as the workflow artifact that downstream acceptance
  consumes.
* `reuse-pinned` digests may be `null` until the approved NGC→GHCR mirror
  program (issue I-07) resolves them; acceptance mode must then require full
  digest resolution before promotion.

### 4. The last-green developer channel (`last-green-controller.yml`, dormant)

`deploy/docker/last-green.lock.json` is the committed, immutable record of
the most recent release set that passed the full acceptance matrix. Moving
aliases are at most a convenience derived from the lock — the lock is
authoritative because multi-repo tag updates cannot be atomic.

The controller (all decision logic in the unit-tested `last_green.py`):

* consumes a `vss-acceptance-result` `repository_dispatch` whose payload
  contract is documented in `last_green.py`;
* **PASS** for the exact `release_set_id`, with digest parity between tested
  images and the manifest → opens a PR advancing the lock (history bounded,
  rollback is one operation);
* **FAIL** → hold; the channel is untouched;
* stays **dormant** (`vars.VSS_LAST_GREEN_ENABLED != 'true'`) until the
  GitLab blocking acceptance mode exists.

## The cross-repository handoff (contract, not implementation)

1. GitHub publishes a complete immutable release set (above).
2. GitHub triggers GitLab with the **release-set ID and manifest**, not just
   a source SHA.
3. GitLab acceptance mode verifies the digests, does **not** rebuild, runs
   the required profile × platform × mode matrix as blocking jobs
   (no `allow_failure`, no placeholder reports), on RTX 6000 Pro.
4. GitLab returns one structured terminal result (the payload contract in
   `last_green.py`), classifying product vs infrastructure failures.
5. On PASS the controller advances the lock. Thursday's weekly promotion
   (GitLab + artifact-promotion) selects the recorded last-green set and
   copies the **same digests** through NGC dev → nSpect gate → nvstaging →
   SQA → GA. Never rebuild after acceptance.

## Explicitly dropped from the original PR #1190 design

* **`auto-bump-container-tag.yml` / `bump_container_tag.py`** — a per-PR bot
  committing tag bumps back to PR branches. Superseded by the release-set +
  last-green lock: `develop` never advances on merge, only on verified
  acceptance PASS.
* **`qa-release-promote.yml` / `release_promote.py`** — NGC promotion from
  GitHub. Promotion is owned by the GitLab weekly flow; its old 13-image
  expectation against a 2-image build matrix would have failed
  unconditionally (review finding), and the digest-preserving copy belongs
  where the NGC credentials and nSpect integration live.
* **Moving `develop-latest` aliases written at build time** — the build
  publishes immutable tags only; any alias is derived from the lock after
  acceptance, never before.

## Still requires owners outside this repository

GHCR public-distribution approval per image (visibility, license, export),
the GitLab acceptance mode and schedule, the mirror program credentials,
nSpect thresholds and waiting semantics, artifact-promotion MR restoration,
and SQA sign-off identity. The inventory and schemas here are written so
those pieces consume the same contract instead of inventing a second one.
