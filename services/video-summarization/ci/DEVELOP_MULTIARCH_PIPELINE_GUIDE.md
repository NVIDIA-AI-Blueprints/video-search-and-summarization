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

# Develop Multi-Arch Pipeline Guide

This guide documents the behavior of the commit/build pipeline in
`services/video-summarization/ci/Jenkinsfile.develop.multiarch`.

## Scope

- Pipeline file: `services/video-summarization/ci/Jenkinsfile.develop.multiarch`
- Helper logic: `services/video-summarization/ci/pipeline-helpers.groovy`
- Goal: build/test/publish multi-arch images, then run optional downstream stages.

## High-Level Flow

0. `check-lvs-changes` (monorepo path gate)
1. `basics`
2. `build-multi-arch-images` (matrix)
3. `standalone-image-tests` (when `TEST_IMAGE_TAG` is set)
4. `sonarqube-with-coverage` (optional)
5. `merge-multi-arch-manifests` (optional)
6. `security-scan` (optional)
7. `bare-metal-test` (optional)
8. `post` actions

## Monorepo Path Gating

The pipeline lives in a monorepo. For ordinary branch commits it runs only when
`services/video-summarization/` changed between `HEAD^` and `HEAD`.

- Detection: `ci/scripts/lvs_app_only_changes.py --mode any` via
  `pipeline-helpers.groovy` (`hasLvsServiceChanges` / `evaluateLvsPipelineActive`).
- Stage `check-lvs-changes` performs a lightweight checkout, sets
  `LVS_PIPELINE_ACTIVE`, and skips all downstream stages when there are no
  service changes.
- Always runs for:
  - git tag builds (`TAG_NAME` / `EFFECTIVE_TAG_NAME` set)
  - standalone test mode (`TEST_IMAGE_TAG` non-empty)
- Jenkins multibranch jobs can additionally configure **Include regions**:
  `services/video-summarization/.*` so unrelated commits do not enqueue a build.

## Key Pipeline Controls

- `disableConcurrentBuilds(abortPrevious: true)` is enabled.
  - A newer build on the same branch can supersede and abort an older run.
- Matrix `ARCH` values:
  - `amd64`
  - `arm64-sbsa`
  - `arm64-igpu`
- `BUILD_ARCHS` chooses which matrix branches run.
- On any non-empty git tag build:
  - `RUN_BUILD` is treated as `true`.
  - `BUILD_ARCHS` is treated as `all`.
  - `RUN_UNIT_TESTS`, `RUN_FUNCTIONAL_TESTS`, and `RUN_INTEGRATION_TESTS` are treated as `true`.
  - `INTEGRATION_USE_COMPOSE_IMAGE` is ignored so the tag validates the built image.
  - `PUSH_TEST_RESULTS_EOS` is not required for Dev Dashboard/NGC upload once test CSVs are produced.
  - `RUN_KAFKA_E2E` remains opt-in.

## Stage Behavior

### 0) `check-lvs-changes`

Lightweight checkout and monorepo gate. When no files under
`services/video-summarization/` changed, sets `LVS_PIPELINE_ACTIVE=false` and
skips `basics`, build/test, merge, and security-scan stages. Build description
is set to `Skipped: no changes under services/video-summarization`.

### 1) `basics`

Runs checkout, vault credential fetch, and optional lint/security-scan gate before matrix builds.

### 2) `build-multi-arch-images` (matrix)

For each selected `ARCH` branch:

1. `checkout-source-for-build-${ARCH}`
2. `wait-for-dockerd-${ARCH}`
3. `build-base-image-${ARCH}` (skipped only for non-tag runs when `INTEGRATION_USE_COMPOSE_IMAGE == 'true'`)
4. `build-lvs-image-${ARCH}` (skipped only for non-tag runs when `INTEGRATION_USE_COMPOSE_IMAGE == 'true'`)
5. `unit-tests-${ARCH}` only when:
   - `ARCH == 'amd64'`
   - `RUN_UNIT_TESTS == 'true'` or this is a git-tag build
   - not a non-tag compose-image-only run
6. `functional-tests-${ARCH}` only when:
   - `ARCH == 'amd64'`
   - `RUN_FUNCTIONAL_TESTS == 'true'` or this is a git-tag build
7. `integration-tests-${ARCH}` only when:
   - `ARCH == 'amd64'`
   - `RUN_INTEGRATION_TESTS == 'true'` or this is a git-tag build
8. `combine-coverage-${ARCH}` only when:
   - `ARCH == 'amd64'`
   - unit tests are enabled or this is a git-tag build
   - functional or integration tests are enabled, or this is a git-tag build
   - not a non-tag compose-image-only run
9. `upload-test-results-${ARCH}` only when:
   - `ARCH == 'amd64'`
   - unit tests are enabled or this is a git-tag build
   - functional or integration tests are enabled, or this is a git-tag build
   - not a non-tag compose-image-only run
10. `push-image-${ARCH}` (skipped only for non-tag runs when `INTEGRATION_USE_COMPOSE_IMAGE == 'true'`)
11. `archive-build-metadata-${ARCH}`

Important matrix rule:
- Even if `integration-tests-amd64` passes, the overall pipeline can still fail if another matrix branch (for example `arm64-sbsa`) fails or cannot schedule.

### 3) `standalone-image-tests`

Runs only when `TEST_IMAGE_TAG` is set. Build stages are skipped.

The stage runs two parallel branches:

1. `standalone-amd64-image-tests`
   - Tests the provided amd64 image tag, or the amd64 sibling if the provided tag already has another known arch suffix.
   - Uses `services/video-summarization/ci/pod-templates/docker-build-amd64-pod.yaml`.
2. `standalone-arm64-sbsa-image-tests`
   - Derives the SBSA sibling image tag by replacing a known arch suffix with `arm64-sbsa`.
   - Example: `nvcr.io/.../vss-video-summarization:3672fd1-amd64` becomes `nvcr.io/.../vss-video-summarization:3672fd1-arm64-sbsa`.
   - Reserves a lockable node using `STANDALONE_SBSA_NODE_LABEL` (default: `DGX-SPARK`) and runs unit tests there.

Standalone unit-test artifacts are written under `test-results/${ARCH}/` so amd64 and SBSA reports do not collide.

### 4) `sonarqube-with-coverage`

Runs only when:
- `RUN_SONARQUBE == 'true'`
- `RUN_UNIT_TESTS == 'true'` or this is a git-tag build

### 5) `merge-multi-arch-manifests`

Runs only when:
- `RUN_BUILD == 'true'` or this is a git-tag build
- not a non-tag compose-image-only run

Includes image verify and merge operations.

### 6) `security-scan`

Runs only when:
- `RUN_SECURITY_SCAN == 'true'`
- `RUN_BUILD == 'true'` or this is a git-tag build
- not a non-tag compose-image-only run

### 7) `bare-metal-test`

Runs only when:
- `RUN_NODE_TEST == 'true'`
- `INTEGRATION_USE_COMPOSE_IMAGE != 'true'`

## Test Result Artifacts

### Unit tests (`runUnitTests`)

Archives:
- `unit-tests-results.csv`
- `pytest-report.xml`
- coverage files (`coverage_reports/**`, `htmlcov/**`, `.coverage*`)

In standalone mode, unit-test outputs are scoped under `test-results/${ARCH}/`.

### Integration tests (`runIntegrationTests`)

Runs Docker Compose API tests and archives integration artifacts from helper logic.
Common files include:
- `pytest-report.api-tests.xml`
- `integration-test-results.csv`

## Upload-Test-Results Semantics

`mergeAndUploadTestResults(...)` behavior:

1. Collects available CSVs from:
   - `unit-tests-results.csv`
   - `test-results/${ARCH}/unit-tests-results.csv` in standalone mode
   - `functional-test-results.csv`
   - `integration-test-results.csv`
2. Merges them into a single CSV and always archives it in Jenkins.
3. External upload (`uploadTestResultsToDashboard`, `uploadTestResultsToNgc`) is gated by:
   - git-tag build (`env.TAG_NAME` is non-empty) OR
   - `PUSH_TEST_RESULTS_EOS == true` OR
   - `TEST_IMAGE_TAG` standalone mode

NGC test-result publishing targets `nv-metropolis-dev/vss-core/test_results`, and CI image uploads target `nvcr.io/nv-metropolis-dev/vss-summarization/...`.

So artifact archival is always attempted when this stage runs and input CSVs are present. On git-tag builds, the pipeline forces the standard tests so this stage produces and uploads the report without requiring `PUSH_TEST_RESULTS_EOS`.

## Understanding Final Status

### Why integration can pass but build still fails

Common pattern:
- `integration-tests-amd64` passes
- a different matrix branch fails (or is unschedulable)
- pipeline ends as failed/canceled/not-built depending on Jenkins/GitLab mapping

### Superseded runs

With `disableConcurrentBuilds(abortPrevious: true)`, older builds may show:
- `Superseded by #<newer build>`
- final Jenkins result often `NOT_BUILT` for the older run
- GitLab status may appear as `CANCELED`

This is expected concurrency behavior, not necessarily a test failure.

## Quick Triage Checklist

1. Confirm whether integration tests failed:
   - Search for `integration-tests-amd64`, `passed`, and `Post-deployment test phase completed successfully`.
2. Check if a different matrix branch failed:
   - Search for `Failed in branch Matrix - ARCH = ...`.
3. Check for supersession:
   - Search for `Superseded by #`.
4. Check scheduler/agent issues:
   - Search for `All nodes of label ... are offline`.
5. Check final status line:
   - `Pipeline completed with status: ...`.

Suggested command:

```bash
rg -n "integration-tests-amd64|passed|Post-deployment test phase|Failed in branch Matrix|Superseded by|All nodes of label|Pipeline completed with status" <pipeline-log.txt>
```

## Related Files

- `services/video-summarization/ci/Jenkinsfile.develop.multiarch`
- `services/video-summarization/ci/pipeline-helpers.groovy`
- `services/video-summarization/ci/pod-templates/docker-build-amd64-pod.yaml`
- `services/video-summarization/ci/pod-templates/docker-build-arm64-pod.yaml`
