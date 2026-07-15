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

# Perf pipeline stack diagrams and migration to run_benchmark.sh

This document diagrams the Jenkins perf flow, the internal flow of `run_benchmark.sh`, identifies gaps, and describes the migration. **Option B is implemented:** the pipeline calls `compose/run_benchmark.sh` for deploy + benchmark + teardown; parallel execution by node is unchanged.

---

## 1. Current Jenkins perf call stack (Jenkinsfile.perf → runPerfBenchmark)

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ Jenkinsfile.perf (lines 266–282)                                                             │
│                                                                                              │
│   helpers.runPerfConfigsParallel([                                                           │
│     allConfigs, selectedIds, imageTag, scenarioName, credentials{},                          │
│     sshPublicKey, helpersStashName, uploadToMinio, customNodeOverrides                        │
│   ])                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ pipeline-helpers.groovy: runPerfConfigsParallel(args)                                        │
│                                                                                              │
│ IN:  allConfigs, selectedIds, scenarioName, credentials{ngc,nvidia,hf,artifactory},          │
│      sshPublicKey, helpersStashName, uploadToMinio, customNodeOverrides                       │
│ OUT: (none; drives parallel stages)                                                          │
│                                                                                              │
│ • Filter configs by selectedIds, enabled; apply customNodeOverrides                          │
│ • Group configs by nodeLabel → byNode                                                        │
│ • For each nodeLabel: stageMap["perf-${label}"] = { ... }                                    │
│ • parallel(stageMap)  ← parallel by node type (e.g. perf-H100, perf-RTX6000PROBW-SE)        │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
          (per parallel branch: one nodeLabel, one bare metal node)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ Inside each "perf-${label}" branch (on orchestrator pod):                                     │
│   getNodeIp(label, sshPublicKey) → lock node                                                  │
│   installInfraOnBareMetal(label)  [unless custom node]                                        │
│   node(jenNode) {  ← switch to bare metal agent                                               │
│     unstash, checkout, verifyNvidiaDriver                                                     │
│     resolvedTag = imageTag ?: getImageTag('amd64')                                           │
│     cfgBatch.each { cfg → stage("${cfg.id}") { runBareMetalDockerComposePerfTest([...]) } }   │
│   }                                                                                          │
│   finally: releaseLock()                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
          (per config in cfgBatch; sequential on same node)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ runBareMetalDockerComposePerfTest(config)                                                    │
│                                                                                              │
│ IN:  ngcApiKey, nvidiaApiKey, openaiApiKey, hfToken, artifactoryUser, artifactoryToken,      │
│      builtImageTag, useSudo, composeFilePath, scenarioName,                                  │
│      vlmGpus, llmGpus, vlmModel, llmModel, visionInputTokens, gpuModel, configId,           │
│      uploadToMinIO                                                                           │
│                                                                                              │
│ Calls: runBareMetalDockerComposeWorkflow(config) { runPerfBenchmark(...) }                    │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ runBareMetalDockerComposeWorkflow(config, testRunner)                                        │
│                                                                                              │
│ IN:  (same as runBareMetalDockerComposePerfTest; composeFilePath, builtImageTag, etc.)       │
│      testRunner = closure that calls runPerfBenchmark(...)                                   │
│                                                                                              │
│ STEPS:                                                                                       │
│   1. preCleanupDockerCompose(useSudo, composeFilePath)                                        │
│   2. checkDiskUsage(useSudo)                                                                  │
│   3. prepareDockerComposeDeployment(ngcApiKey, builtImageTag, useSudo, composeFilePath)       │
│      → sed replace LVS image in compose file with builtImageTag                              │
│      → docker logout nvcr.io; docker login nvcr.io                                             │
│      → NIM cache dir creation (getNimCacheDir), chown/chmod                                  │
│   4. pullDockerComposeImages(..., builtImageTag, ...)  [timeout 20 min]                       │
│   5. runDockerComposeDeployment(...)  [timeout DEPLOYMENT_TIMEOUT_MINUTES]  ◄── DEPLOY       │
│      → buildDockerComposeCommand (NGC_API_KEY, LOCAL_NIM_CACHE, NVIDIA_API_KEY, etc.)         │
│      → compose up -d                                                                          │
│      → compose logs -f lvs &                                                                 │
│      → compose up --wait --wait-timeout ${timeoutMinutes*60}                                  │
│   6. testRunner()  → runPerfBenchmark(...)                                                    │
│ finally: cleanupDockerCompose(useSudo, composeFilePath)                                       │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ runPerfBenchmark(...)                                                                         │
│                                                                                              │
│ IN:  scenarioName, configPath(=null), vlmGpus, llmGpus, configId, uploadToMinIO,              │
│      composeFilePath, vlmModel, llmModel, visionInputTokens, gpuModel                         │
│                                                                                              │
│ • cd ${WORKSPACE}/perf/benchmark                                                              │
│ • Ensure python3-venv; create /tmp/vss-perf-venv; pip install -r requirements.txt            │
│ • export VIA_BACKEND=http://localhost:38111                                                   │
│ • withCredentials(MINIO_ACCESS_KEY, MINIO_SECRET_KEY) {                                       │
│     python vss_perf_benchmark.py \                                                            │
│       [--config] --scenario ... --vlm-gpus --llm-gpus \                                       │
│       --vlm-model --llm-model --vision-input-tokens --gpu-model \                             │
│       --output-json vss-perf-results --config-id --triggered-by ci_pipeline \                 │
│       --pipeline-url $BUILD_URL [--upload]                                                    │
│   }                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Parameters at each call (summary)

| Layer | Key parameters |
|-------|----------------|
| **runPerfConfigsParallel** | allConfigs, selectedIds, imageTag, scenarioName, credentials{ngcApiKey, nvidiaApiKey, hfToken, artifactoryUser, artifactoryToken}, sshPublicKey, helpersStashName, uploadToMinio, customNodeOverrides |
| **runBareMetalDockerComposePerfTest** | From config: ngcApiKey, nvidiaApiKey, openaiApiKey, hfToken, artifactory*, builtImageTag, useSudo, composeFilePath, scenarioName, vlmGpus, llmGpus, vlmModel, llmModel, visionInputTokens, gpuModel, configId, uploadToMinIO |
| **runBareMetalDockerComposeWorkflow** | Same config; plus resolveDeploymentTimeoutMinutes() for deploy phase |
| **runDockerComposeDeployment** | ngcApiKey, nvidiaApiKey, openaiApiKey, useSudo, composeFilePath, hfToken, artifactoryUser, artifactoryToken; timeout from DEPLOYMENT_TIMEOUT_MINUTES |
| **runPerfBenchmark** | scenarioName, configPath, vlmGpus, llmGpus, configId, uploadToMinIO, composeFilePath, vlmModel, llmModel, visionInputTokens, gpuModel; env: BUILD_URL, MINIO_* credentials |

---

## 3. run_benchmark.sh internal stages

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ run_benchmark.sh (compose/run_benchmark.sh)                                                   │
│                                                                                              │
│ CLI: -f COMPOSE_FILE (required), -c CONFIG, -s SCENARIO, -p PORT, -t TIMEOUT,                │
│      -v VLM_GPUS, -l LLM_GPUS, -d (teardown), -h                                              │
│ Env: ARTIFACTORY_USER, ARTIFACTORY_TOKEN (required); .env sourced from SCRIPT_DIR           │
│ Paths: SCRIPT_DIR = compose/; BENCHMARK_DIR = ../perf/benchmark                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

  ┌─ Step 1: Launch compose stack ─────────────────────────────────────────────────────────┐
  │  LVS_BACKEND_PORT="$LVS_PORT" docker compose -f "$SCRIPT_DIR/$COMPOSE_FILE" up -d        │
  │  (No image substitution; no NGC/Artifactory env passed into compose)                     │
  └─────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌─ Step 2: Wait for LVS ready ───────────────────────────────────────────────────────────┐
  │  Poll http://localhost:$LVS_PORT/v1/ready every 5s, max HEALTH_TIMEOUT (default 600s)   │
  │  Then sleep 30s "for services to stabilize"                                              │
  └─────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌─ Step 3: Run benchmark ───────────────────────────────────────────────────────────────┐
  │  cd $BENCHMARK_DIR                                                                       │
  │  Create/use venv vss-perf-env; pip install -r requirements.txt                         │
  │  export VIA_BACKEND="http://localhost:$LVS_PORT"                                          │
  │  export VIA_VLM_GPUS, VIA_LLM_GPUS (if -v/-l); VIA_OUTPUT_DIR="vss-perf-report-..."      │
  │  python3 vss_perf_benchmark.py --config $BENCHMARK_CONFIG [--scenario $SCENARIO_ARGS]     │
  │  (No --output-json, --config-id, --triggered-by, --pipeline-url, --upload, or metadata)   │
  └─────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌─ Step 4: Teardown (optional) ───────────────────────────────────────────────────────────┐
  │  if -d: docker compose -f "$SCRIPT_DIR/$COMPOSE_FILE" down                                │
  └─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Gaps (pipeline vs run_benchmark.sh)

### 4.1 Deploy / pre-deploy

| Item | Pipeline | run_benchmark.sh | Gap |
|------|----------|------------------|-----|
| Image tag | prepareDockerComposeDeployment: sed replace LVS image with builtImageTag | None | Script uses compose file as-is. Need either pipeline to rewrite compose before calling script, or script to accept an image override (e.g. env or flag). |
| Registry / env for compose | NGC login; buildDockerComposeCommand passes NGC_API_KEY, LOCAL_NIM_CACHE, NVIDIA_API_KEY, HF_TOKEN, ARTIFACTORY_* | Only LVS_BACKEND_PORT; expects .env / pre-set ARTIFACTORY_* | Script does not set NGC_API_KEY, LOCAL_NIM_CACHE, etc. Pipeline must set env before invoking script or script must accept/pass these. |
| Pre-cleanup | preCleanupDockerCompose | None | Optional: pipeline can run cleanup before calling script if same node. |
| Disk check | checkDiskUsage | None | Optional: pipeline can run before script. |
| Pull phase | Separate 20-min pull then deploy | compose up -d (pull implicit) | Script has no separate pull timeout. Acceptable if we keep pull on pipeline side or add optional pull step to script. |
| Health wait | compose up --wait --wait-timeout (Docker healthchecks) | curl /v1/ready every 5s, then 30s sleep | Different mechanism; both valid. Script’s 600s default can be aligned with DEPLOYMENT_TIMEOUT_MINUTES via -t. |
| Teardown | Always in workflow finally: cleanupDockerCompose | Only if -d | For CI: call script with -d or have pipeline run cleanup after script (recommend -d for consistency). |

### 4.2 Benchmark invocation (vss_perf_benchmark.py args)

| Arg / env | Pipeline (runPerfBenchmark) | run_benchmark.sh | Gap |
|-----------|-----------------------------|------------------|-----|
| --config | Optional (default config in perf/benchmark) | -c CONFIG (default config.yaml) | Align default and path (workspace vs script-relative). |
| --scenario | scenarioName (e.g. quick_test) | -s SCENARIO | Script supports; pass from pipeline. |
| --vlm-gpus / --llm-gpus | From cfg | -v / -l (also set VIA_VLM_GPUS, VIA_LLM_GPUS) | Script supports; pass from perf config. |
| --output-json | vss-perf-results (pipeline then copies to results/${cfg.id}/) | Not passed (script sets VIA_OUTPUT_DIR only) | Script must accept output base name or path so pipeline can archive; and/or pass --output-json for MinIO. |
| --config-id | cfg.id | Not passed | Required for dashboard/artifacts; script needs to accept and pass through. |
| --triggered-by | ci_pipeline | Not passed | Script needs to accept and pass through. |
| --pipeline-url | BUILD_URL | Not passed | Script needs to accept and pass through (or read from env). |
| --upload | When uploadToMinio | Not passed | Script needs flag/env and MinIO creds to upload. |
| --vlm-model, --llm-model, --vision-input-tokens, --gpu-model | From cfg | Not passed | Script needs to accept and pass through for dashboard. |

### 4.3 Parallelism and structure

| Item | Pipeline | run_benchmark.sh | Gap |
|------|----------|------------------|-----|
| Parallelism | parallel(stageMap): one branch per nodeLabel; per branch, configs run sequentially | Single process, one compose + one benchmark run | No change to parallelism: we keep “parallel by node, sequential per node.” Each invocation is one run_benchmark.sh (one config). |
| Output location | perf/benchmark/vss-perf-report → copied to perf/benchmark/results/${cfg.id}/ then archived | VIA_OUTPUT_DIR under BENCHMARK_DIR | Script should write to a known location or accept --output-dir; pipeline copies to results/${cfg.id}/ and archives. |
| Credentials | MaskPasswordsBuildWrapper; withCredentials for MinIO | ARTIFACTORY_* in env | Pipeline sets ARTIFACTORY_*, NGC_*, etc., before sh; MinIO can be env or script flag. |

---

## 5. Migration plan: switch to run_benchmark.sh

### 5.1 High-level approach

- **Do not replace** the whole of `runPerfConfigsParallel`. Keep:
  - Reading perf-configs.yaml and filtering by selectedIds / enabled.
  - Grouping by nodeLabel and building `stageMap["perf-${label}"]`.
  - `parallel(stageMap)`, node reservation, installInfraOnBareMetal, lock/release.
  - Per-node: unstash, checkout, verifyNvidiaDriver, resolvedTag.
- **Replace**, per config, the current “deploy + benchmark” implementation:
  - Today: `runBareMetalDockerComposePerfTest` → `runBareMetalDockerComposeWorkflow` (pre-clean, prepare, pull, runDockerComposeDeployment, runPerfBenchmark) + cleanup in finally.
  - Target: “prepare once for this config” (image substitution, optional pre-clean, env for compose) then run **one** `compose/run_benchmark.sh` that does “compose up → health wait → benchmark → teardown (-d)”, with all CI/dashboard args and output dir under pipeline control.

So: **same parallel structure** (multiple `run_benchmark.sh` runs on different bare metal nodes in parallel; on each node, multiple configs still sequential). Only the inner “deploy + benchmark” is replaced by the script.

### 5.2 Option A: Script does “benchmark only” (pipeline keeps deploy)

- Pipeline keeps: preCleanup, checkDiskUsage, prepareDockerComposeDeployment (image tag), pull, runDockerComposeDeployment, then **call run_benchmark.sh in “benchmark-only” mode** (no compose up/down), then cleanup.
- Requires run_benchmark.sh to support a “skip launch/teardown, only run benchmark” mode (e.g. `--benchmark-only` and assume LVS is already up). Script would only do “wait optional / run vss_perf_benchmark.py with full args”.
- Pros: Minimal script change; deploy stays in Groovy. Cons: Two ways to “run benchmark” (script with vs without deploy); less unification.

### 5.3 Option B (recommended): Script does “deploy + benchmark + teardown”; pipeline does prep and image tag

- Pipeline (per config) on the bare metal node:
  1. preCleanupDockerCompose (optional but good).
  2. checkDiskUsage (optional).
  3. **Image tag**: Either (a) keep prepareDockerComposeDeployment(ngcApiKey, builtImageTag, useSudo, composeFilePath) so the compose file on disk has the correct image, then pass that file to the script; or (b) add to run_benchmark.sh an option (e.g. `-I IMAGE_TAG` or env `LVS_IMAGE_TAG`) and have the script run sed (or a small helper) to substitute the LVS image before `compose up`.
  4. **Set env** for the script: NGC_API_KEY, ARTIFACTORY_USER, ARTIFACTORY_TOKEN, LOCAL_NIM_CACHE (from getNimCacheDir), NVIDIA_API_KEY, HF_TOKEN, etc., so that when the script runs `docker compose`, the same env is available. (Script may need to export these before `compose up` if it doesn’t today.)
  5. **Optional separate pull**: Either pipeline runs pull with existing helper and then script runs `compose up -d` (no pull), or script does `compose pull` then `compose up -d` (and we add a timeout for pull in script or pipeline).
  6. **Invoke run_benchmark.sh** with:
     - `-f <compose-file>` (path relative to workspace or absolute; script must accept path that matches pipeline’s composeFilePath).
     - `-c` / `-s` for config and scenario.
     - `-p 38111`, `-t $((DEPLOYMENT_TIMEOUT_MINUTES*60))`, `-v` / `-l` from cfg.
     - `-d` so script tears down after benchmark.
     - New flags or env: `--config-id`, `--triggered-by`, `--pipeline-url`, `--upload`, `--output-json` or output dir, and metadata (vlm-model, llm-model, vision-input-tokens, gpu-model). Alternatively, script can pass through env (e.g. CONFIG_ID, PIPELINE_URL, UPLOAD_TO_MINIO) and build vss_perf_benchmark.py args from them.
  7. **After script returns**: Copy script’s output dir (e.g. perf/benchmark/vss-perf-report-* or a fixed dir the script writes to) to `perf/benchmark/results/${cfg.id}/` and archive as today.

- run_benchmark.sh changes:
  - **CLI/env**: Add (or document) options or env for: config-id, triggered-by, pipeline-url, upload (MinIO), output-json or output-dir, vlm-model, llm-model, vision-input-tokens, gpu-model. Pass them through to `vss_perf_benchmark.py`.
  - **Compose env**: Before `compose up`, export NGC_API_KEY, LOCAL_NIM_CACHE, ARTIFACTORY_*, etc., when provided (e.g. from env already set by pipeline).
  - **Image tag (if done in script)**: Optional `-I IMAGE_TAG` or env to substitute LVS image in the compose file before `up`.
  - **Output dir**: Either accept `--output-dir` and write there, or a fixed name (e.g. vss-perf-results) so pipeline can copy to `results/${cfg.id}/` predictably.
  - **Teardown**: Use `-d` from CI so script performs teardown; pipeline does not need to run cleanup after script (unless we want a safety net).

### 5.4 Parallel execution (unchanged)

- **parallel(stageMap)** stays: one branch per nodeLabel.
- Inside each branch: reserve node, installInfraOnBareMetal (if not custom), then **node(jenNode)** with sequential **cfgBatch.each { cfg → ... }**.
- In each `stage("${cfg.id}")` we run **one** `run_benchmark.sh` for that cfg (compose path, scenario, config id, metadata, etc.). So we still run multiple `run_benchmark.sh` in parallel on different bare metal nodes, and multiple sequentially on the same node for different configs.

### 5.5 Concrete steps (summary)

1. **Extend run_benchmark.sh**  
   - Add CLI or env for: config-id, triggered-by, pipeline-url, upload, output-json (or output-dir), vlm-model, llm-model, vision-input-tokens, gpu-model.  
   - Build full `vss_perf_benchmark.py` command including these.  
   - Optionally: accept image tag and substitute in compose; export NGC_*, LOCAL_NIM_CACHE, ARTIFACTORY_*, etc., before compose.

2. **In pipeline-helpers.groovy**  
   - Add a new function, e.g. `runBareMetalPerfViaRunBenchmarkScript(Map config)`, that:  
     - Optionally runs preCleanupDockerCompose, checkDiskUsage.  
     - Runs prepareDockerComposeDeployment (image tag) so the compose file is correct.  
     - Sets env (NGC_API_KEY, ARTIFACTORY_*, LOCAL_NIM_CACHE, etc.) and optionally MINIO_* for upload.  
     - Invokes `compose/run_benchmark.sh` with `-f`, `-c`, `-s`, `-p`, `-t`, `-v`, `-l`, `-d` and the new args/env.  
     - Copies script output to `perf/benchmark/results/${configId}/` and returns (or throws).  
   - Replace the body of `runBareMetalDockerComposePerfTest` so it calls `runBareMetalPerfViaRunBenchmarkScript` instead of `runBareMetalDockerComposeWorkflow` + `runPerfBenchmark`.  
   - Keep the same `runPerfConfigsParallel` structure: parallel by node, sequential configs per node, same staging and artifact copying.

3. **Paths**  
   - Compose path: pass `-f` as the basename or path relative to workspace; run_benchmark.sh should run from workspace root (or compose dir) so `-f` matches. If script assumes SCRIPT_DIR=compose, then `-f` could be the basename and pipeline sets CWD to workspace.

4. **Credentials**  
   - Keep MaskPasswordsBuildWrapper and withCredentials for MinIO when upload is enabled; set MINIO_ACCESS_KEY, MINIO_SECRET_KEY (and any script-specific vars) before calling the script.

5. **Testing**  
   - Run Jenkinsfile.perf with one config and one node; verify artifacts and (if enabled) MinIO upload and dashboard metadata.

---

## 6. Diagram: after migration (parallel unchanged)

```
parallel(stageMap)
    │
    ├── perf-H100 (node A)
    │     ├── stage("config-1") → runBareMetalPerfViaRunBenchmarkScript(cfg1)
    │     │                         → prepareDockerComposeDeployment (image tag)
    │     │                         → run_benchmark.sh -f ... -d ...
    │     │                         → copy results to results/cfg1, archive
    │     └── stage("config-2") → runBareMetalPerfViaRunBenchmarkScript(cfg2)
    │                             → same, then copy/archive
    │
    └── perf-RTX6000 (node B)
          └── stage("config-3") → runBareMetalPerfViaRunBenchmarkScript(cfg3)
                                  → same
```

So: **we do not replace the entire runPerfConfigsParallel stack**. We keep parallel-by-node and sequential-by-config, and only replace the inner “deploy + benchmark” with a single `run_benchmark.sh` invocation per config, with the script extended to support all CI/dashboard parameters and (optionally) image tag and compose env.
