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

# LVS Performance Benchmarking Pipeline — Executive Guide

## Overview

`ci/Jenkinsfile.perf` is a dedicated CI pipeline for running automated performance benchmarks
against the LVS (Long Video Summarization) Docker image on bare metal GPU hardware. It is
intentionally decoupled from the build pipeline (`Jenkinsfile.develop.multiarch`): it **does not
build or push images**. Instead it accepts a pre-built image tag as input, deploys LVS via Docker
Compose onto a real GPU machine, and runs `vss_perf_benchmark.py` against the live service.

This separation means benchmarks can be triggered independently — against any published image tag,
at any cadence — without interfering with the main build/test flow.

---

## Pipeline Parameters

| Parameter | Default | Description |
|---|---|---|
| `LVS_IMAGE_TAG` | `auto` | Image to benchmark. `auto` computes the tag from the current git commit (assumes the image was already built and pushed by `Jenkinsfile.develop.multiarch`). Provide an explicit full NGC tag to target a specific build. |
| `PERF_CONFIG_IDS` | `all` | Which entries from `ci/perf-configs.yaml` to run. `all` runs every enabled entry. Comma-separated IDs run a subset, e.g. `h100-integrated-cr2-nemo3nano-1gpu,rtxpro-integrated-cr2-nemo3nano-1gpu`. |
| `PERF_SCENARIO` | `quick_test` | Benchmark scenario(s) from `perf/benchmark/config.yaml` to execute. Common values: `quick_test`, `single_file_test`, `file_burst_test`. Multiple values are comma-separated. |
| `CUSTOM_NODE_LABEL_H100` | *(empty)* | When non-empty (and not `"null"`), use this Jenkins agent label for all H100 configs instead of the shared pool label `H100`. Enables a pre-provisioned custom node; infra install is skipped for this label. Leave empty to use shared pool. |
| `CUSTOM_NODE_LABEL_RTXPRO6000BW` | *(empty)* | Same as above for RTXPRO6000BW configs. Leave empty to use shared pool. |
| `UPLOAD_TO_MINIO` | `true` | Upload result JSON files to MinIO for dashboarding. |
| `DEPLOYMENT_TIMEOUT_MINUTES` | `30` | How long to wait for Docker Compose services to become healthy. Increase for first-run NIM model downloads (cache miss). |
| `DOCKER_PULL_TIMEOUT_MINUTES` | `25` | How long to wait for Docker Compose image pulls. Increase for cold nodes or large image/cache pulls. |

---

## Pipeline Stages

```
perf-tests (K8s pod: ubuntu-22-04)
├── checkout-source              Clone repo with LFS skip to avoid timeout
├── install-infra-prereqs        Install lightweight orchestration tools
├── get-vault-credentials-perf   Fetch API keys + SSH keys from HashiCorp Vault
└── run-perf-benchmarks          Parallel branches — one per unique nodeLabel
    ├── perf-H100
    │   ├── getNodeIp()          Reserve bare metal node from pool
    │   ├── node(jenNode)
    │   │   ├── gitCheckout()    Clone repo on bare metal
    │   │   ├── verifyNvidiaDriver()  Confirm GPU hardware is ready
    │   │   ├── verifyPreProvisionedBareMetalInfra()  Validate driver + Docker runtime
    │   │   ├── h100-integrated-cr2-nemo3nano-1gpu  (sequential config stages)
    │   │   ├── h100-cr2-nim-nemotron9b-1gpu
    │   │   └── h100-integrated-cr2-nemotron9b-1gpu
    │   └── releaseLock()
    └── perf-RTXPRO6000BW
        ├── getNodeIp()
        ├── node(jenNode)
        │   ├── gitCheckout()
        │   ├── verifyNvidiaDriver()
        │   ├── verifyPreProvisionedBareMetalInfra()
        │   └── rtxpro-integrated-cr2-nemo3nano-1gpu  (sequential config stages)
        └── releaseLock()
```

### Stage Details

**`checkout-source`**
Clones the repository with `GIT_LFS_SKIP_SMUDGE=1` set at the jnlp container level to prevent
git-lfs from downloading large binary objects during checkout (which would hit the 10-minute SCM
timeout). LFS objects are not needed on the K8s orchestrator pod.

**`install-infra-prereqs`**
Runs once on the K8s pod before any parallel work begins. While AAAI-718 is being evaluated,
it installs only the lightweight orchestration tools (`wget`, `curl`, and `jq`). The previous
`nv-one-click` prerequisite and distribution-build commands remain commented in the Jenkinsfile
for quick restoration.

**`get-vault-credentials-perf`**
Fetches `NGC_API_KEY`, `NVIDIA_API_KEY`, `HF_TOKEN`, and the CI SSH key pair
from HashiCorp Vault. Writes the SSH private key to the workspace so `envbuild.sh` can SSH into
bare metal nodes. Exports credentials to environment variables namespaced with `_PERF` suffix so
they survive the transition from K8s pod to bare metal node context.

**`run-perf-benchmarks`**
The core stage. Described in detail in the next section.

---

## How Parallelization Works

The key function is `runPerfConfigsParallel()` in `ci/pipeline-helpers.groovy`. Its job is to read
all configs, group them by GPU node type, and launch one parallel Jenkins branch per group.

### Step 1 — Filter configs

```
all entries in perf-configs.yaml
  → remove entries where  enabled: false
  → if PERF_CONFIG_IDS != "all", keep only the listed IDs
```

### Step 2 — Group by nodeLabel

Configs that share the same `nodeLabel` are collected into a batch. This is the key efficiency
mechanism: **one bare metal node reservation covers all configs targeting that GPU type**. Reserving
nodes is expensive (minutes of lock-wait time), so batching eliminates redundant reserve/release
cycles.

```
H100         → [h100-integrated-cr2-nemo3nano-1gpu, h100-cr2-nim-nemotron9b-1gpu, h100-integrated-cr2-nemotron9b-1gpu]
RTXPRO6000BW → [rtxpro-integrated-cr2-nemo3nano-1gpu, rtxpro-integrated-cr2-nemotron9b-1gpu, ...]
```

### Step 3 — Launch parallel branches

One Jenkins parallel branch is created per unique `nodeLabel`. All branches execute concurrently.
In the Jenkins stage view this appears as:

```
run-perf-benchmarks
├── perf-H100           ← parallel branch A
└── perf-RTXPRO6000BW   ← parallel branch B (runs at same time as A)
```

### Step 4 — Sequential configs within a branch

Inside each branch, configs run **sequentially** on the same reserved node. This is intentional:
running multiple GPU-heavy workloads simultaneously on one machine would produce meaningless perf
numbers. Each config gets its own named Jenkins stage so pass/fail is tracked individually. A
failure in one config marks that stage failed but does **not** abort the remaining configs for that
node.

```
perf-H100
├── h100-integrated-cr2-nemo3nano-1gpu    ← runs first, completes
├── h100-cr2-nim-nemotron9b-1gpu          ← runs second
└── h100-integrated-cr2-nemotron9b-1gpu   ← runs third
```

---

## `ci/perf-configs.yaml` — Structure and Field Reference

This file is the single source of truth for what gets benchmarked. **No Jenkinsfile changes are
needed to add, remove, or disable a benchmark configuration** — only this file needs editing.

### File structure

```yaml
perf_configs:
  - id: "rtxpro-integrated-cr2-nemo3nano-1gpu"
    composePath: "compose/rtxpro-integrated-cr2-nemo3-nano_1gpu.yaml"
    nodeLabel: "RTXPRO6000BW"
    enabled: true
    description: "RTX Pro 1-GPU: CR2 VLM (integrated, GPU 0) + Nemotron-3-Nano LLM (GPU 0)"
    vlmModel: "cosmos-reason2-8b"
    llmModel: "nemotron-3-nano"
    vlmGpus: "0"
    llmGpus: "0"
```

### Field reference

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Unique identifier. Used as the Jenkins stage name, result subdirectory name, and `PERF_CONFIG_ID` in Metropolis JSON output. Must be filesystem-safe (no spaces or slashes). |
| `composePath` | Yes | Path to the Docker Compose file relative to the repo root. Each compose file defines the complete LVS service topology for that configuration. |
| `nodeLabel` | Yes | Jenkins lockable-resource label for the bare metal GPU node pool. All configs sharing the same label are batched onto one node. Currently active values: `H100`, `RTXPRO6000BW`. |
| `enabled` | No | Set to `false` to skip this config without deleting it. Defaults to `true` if omitted. Use this to temporarily disable configs (e.g., when the required node type is unavailable). |
| `description` | No | Human-readable summary shown in pipeline logs. |
| `vlmModel` | No | VLM model name. Passed to `vss_perf_benchmark.py` as metadata for dashboarding. Does **not** control which model is deployed — that is determined by the compose file. |
| `llmModel` | No | LLM model name. Same metadata-only semantics as `vlmModel`. |
| `vlmGpus` | No | Comma-separated GPU device indices used by the VLM workload. Informational — reflects what is configured inside the compose file. Used for dashboarding and topology inference. |
| `llmGpus` | No | Comma-separated GPU device indices used by the LLM workload. Same informational semantics. |

### Current config inventory

| Config ID | Node | GPUs | Status |
|---|---|---|---|
| `h100-integrated-cr2-nemo3nano-1gpu` | H100 | 1 | **enabled** |
| `h100-cr2-nim-nemotron9b-1gpu` | H100 | 1 | **enabled** |
| `h100-integrated-cr2-nemotron9b-1gpu` | H100 | 1 | **enabled** |
| `h100-integrated-cr2-nemo3nano-4gpu` | H100 | 4 | disabled (multi-GPU) |
| `h100-cr2-nim-nemo3nano-4gpu` | H100 | 4 | disabled (multi-GPU) |
| `h100-integrated-cr2-nemo3nano-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-cr2-nim-nemo3nano-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-integrated-cr2-nemotron9b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-nim-cr2-nemotron9b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-integrated-qwen3-nemotron9b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-integrated-qwen3-gptoss120b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-cr2-nim-gptoss120b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `h100-integrated-cr2-gptoss120b-8gpu` | H100 | 8 | disabled (multi-GPU) |
| `rtxpro-integrated-cr2-nemo3nano-1gpu` | RTXPRO6000BW | 1 | **enabled** |
| `rtxpro-integrated-cr2-nemotron9b-1gpu` | RTXPRO6000BW | 1 | **enabled** |
| `rtxpro-integrated-cr2-nemo3nano-4gpu` | RTXPRO6000BW | 4 | disabled (multi-GPU) |
| `rtxpro-cr2-nim-nemo3nano-4gpu` | RTXPRO6000BW | 4 | disabled (multi-GPU) |
| `rtxpro-cr2-nim-nemo3nano-4gpu-2x1gpu` | RTXPRO6000BW | 4 | disabled (multi-GPU) |
| `rtxpro-integrated-cr2-nemo3nano-8gpu` | RTXPRO6000BW | 8 | disabled (multi-GPU) |
| `rtxpro-integrated-qwen3-nemo3nano-8gpu` | RTXPRO6000BW | 8 | disabled (multi-GPU) |
| `rtxpro-integrated-qwen3-gptoss120b-8gpu` | RTXPRO6000BW | 8 | disabled (multi-GPU) |
| `rtxpro-nim-cr2-nemo3nano-8gpu` | RTXPRO6000BW | 8 | disabled (multi-GPU) |

Multi-GPU configs are disabled because the current nodes in the pool lack NVLink. Without NVLink,
the vLLM V1 engine fails to initialize for large NIM models due to an NCCL multi-GPU
initialization deadlock during PCIe peer-to-peer setup. Re-enable these configs once NVLink-capable
nodes are added to the pool. See the internal NVLink multi-GPU perf discussion with the CI team.

---

## Bare Metal Node Lifecycle

Each parallel branch manages the full lifecycle of its node reservation:

```
1. getNodeIp(label, sshPublicKey)
     └─ Calls the lock-job remote trigger to reserve a node from the pool
     └─ SSHes to the node, injects the CI public key into authorized_keys
     └─ Sets env.BM_SSH_HOST, env.BM_SSH_USER
     └─ Records lock-wait time in build description (e.g., "lock_wait_minutes=4.28")

2. node(jenNode)                           [execution moves to bare metal node]
     └─ unstash + gitCheckout()            Clone the repo on the bare metal node
     └─ verifyNvidiaDriver()               Run nvidia-smi; log driver version, topology,
                                           NVLink/NVSwitch status, Fabric Manager
     └─ verifyPreProvisionedBareMetalInfra()
          • Require NVIDIA driver 580.105.08 or newer
          • Verify Docker, Docker Compose, and the NVIDIA container runtime
     └─ getImageTag('amd64') if LVS_IMAGE_TAG=auto
          └─ Computes tag from git commit: IMAGE_NAME:<version>-amd64
     └─ [sequential config loop]

4. releaseLock()                           [always, in finally block]
     └─ Calls lock-job API to release the node back to the pool
```

---

## Per-Config Benchmark Execution

For each config in the batch, `runBareMetalDockerComposePerfTest()` orchestrates:

```
1. Docker Compose up
     └─ Pulls the LVS image from NGC (or uses local cache), up to DOCKER_PULL_TIMEOUT_MINUTES
     └─ Starts all services defined in composePath
     └─ Waits up to DEPLOYMENT_TIMEOUT_MINUTES for health checks to pass

2. vss_perf_benchmark.py --scenario <PERF_SCENARIO>
     └─ Connects to the LVS REST API at localhost:38111
     └─ Submits video files and measures:
          • End-to-end latency per video
          • Throughput (videos/hour)
          • GPU utilization (sampled at 1s intervals via pynvml)
          • First-token latency, total processing time

3. Result collection
     └─ JSON + Excel reports written to perf/benchmark/vss-perf-report/
     └─ If UPLOAD_TO_MINIO=true: uploads JSON to MinIO for the Metropolis dashboard
     └─ Results copied to perf/benchmark/results/<config-id>/ to prevent
        successive configs from overwriting each other

4. Docker Compose down
     └─ Stops and removes all containers
     └─ Frees GPU memory for the next config

5. Jenkins archiveArtifacts
     └─ perf/benchmark/results/** archived as build artifacts
```

### Benchmark scenarios

Scenarios are defined in `perf/benchmark/config.yaml` and selected via the `PERF_SCENARIO`
pipeline parameter:

| Scenario | Description |
|---|---|
| `quick_test` | Fast smoke-test with minimal video samples. Used as the default for CI. |
| `single_file_test` | Processes videos one at a time, measures per-video latency and throughput. |
| `file_burst_test` | Submits multiple videos concurrently, measures burst throughput and queue behaviour. |

---

## Infrastructure Setup — Pre-provisioned Lockable Resources

Under AAAI-718, lockable resources provide the NVIDIA driver, Docker, and NVIDIA Container Toolkit.
The perf pipeline validates this contract and does not mutate the host before running benchmarks.

The previous `nv-one-click` preparation and installation calls remain commented in
`ci/Jenkinsfile.perf` and `ci/pipeline-helpers.groovy` during the evaluation period.

---

## Credential and Secret Handling

All secrets are fetched from HashiCorp Vault in `get-vault-credentials-perf` and never hardcoded:

| Secret | Use |
|---|---|
| `NGC_API_KEY` | Pull LVS Docker image from NGC registry |
| `NVIDIA_API_KEY` | NIM microservice authentication |
| `HF_TOKEN` | Hugging Face model downloads |
| `SSH_PRIVATE_KEY` / `SSH_PUBLIC_KEY` | SSH access to bare metal nodes via nv-one-click |

Credentials are wrapped with `MaskPasswordsBuildWrapper` during benchmark execution so they do not
appear in console output. The SSH private key file is deleted from the workspace in the pipeline's
`post { always { ... } }` block.

---

## Adding a New Benchmark Configuration

No Jenkinsfile changes are required. Only `ci/perf-configs.yaml` needs updating:

1. Create or identify the Docker Compose file under `compose/` for the desired topology.
2. Append an entry to `perf-configs.yaml`:

```yaml
- id: "my-new-config-1gpu"
  composePath: "compose/my-new-config_1gpu.yaml"
  nodeLabel: "RTXPRO6000BW"      # must match an active Jenkins node label
  enabled: true
  description: "My topology: VLM on GPU 0 + LLM on GPU 0"
  vlmModel: "my-vlm-model"
  llmModel: "my-llm-model"
  vlmGpus: "0"
  llmGpus: "0"
```

3. If `nodeLabel` matches an existing label already used by other configs, the new config will
   automatically be batched onto the same reserved node with no extra cost.
4. If `nodeLabel` is a new label, a new parallel branch will be created and an additional node will
   be reserved concurrently.

To temporarily skip a config without removing it, set `enabled: false`.

---

## Using a custom node

To run benchmarks on your own pre-provisioned node (e.g. driver and Docker already installed) instead of the shared pool, add the host to the Jenkins node pool, then point the perf pipeline at it.

### Adding your host to the node pool

Do this once per host. The host will appear as a **Lockable resource, JNLP Agent** and can be reserved via `getNode(label)` from pipelines.

1. **SSH into the target host** that will become the Jenkins agent.

2. **Install or upgrade Ansible** on that host (required version > 2.14.17):

   ```bash
   sudo apt-get update
   sudo apt install -y python3-pip
   pip3 install --upgrade ansible   # or: pip install --upgrade ansible
   git config --global credential.helper store
   ```

3. **Clone the internal `jenkins-node-pool` repository and run the Ansible playbook** with the Jenkins instance and a unique label:

   ```bash
   git clone <internal-jenkins-node-pool-repo-url>
   cd jenkins-node-pool
   ansible-playbook ansible-add-lockable-resource.yaml -e "instance_name=met-vss-cicd" -e "new_node_label=<ASSIGN_LABEL>"
   ```

   Replace `<ASSIGN_LABEL>` with a unique label (e.g. `a4u8g-0141_H100`). Git clone and the playbook may prompt for Git username/password for cloning and pushing changes.

4. **If the playbook completes successfully**, it opens a Merge Request in the jenkins-node-pool repo. **Notify the Jenkins node-pool maintainers** to review and merge the MR. After the MR is merged, automation adds the host as a node for the Jenkins instance you specified (e.g. `met-vss-cicd`).

    ```bash
    $ git status
    On branch feature/add-jenkins-node-<host>
    nothing to commit, working tree clean
    $ git log -1
    Update Jenkins nodes configuration - Add/Update node - Add node <host>-<ASSIGN_LABEL> to met-vss-cicd
    ```

5. **Outcome:** The node appears in Jenkins as a **Lockable resource, JNLP Agent** with a name like `<host>-<ASSIGN_LABEL>`. In your pipeline you call `getNode("<ASSIGN_LABEL>")` (from `jenkins-shared-library/vars/getNode.groovy`) with that label; it returns the agent name, which you use with the Jenkins built-in `node(agentName) { ... }` to run steps on that host.

6. **Verify:**
   - **Jenkins node list:** check the target Jenkins instance (e.g. `met-vss-cicd`).
   - **Lockable resources dashboard:** use your team's internal lockable-resources dashboard.

**What the Ansible playbook does (brief):** It runs on the host you SSH into. It loads the existing `config/agent-node-configs.yaml` from the repo, resolves the target Jenkins instance(s) (e.g. `met-vss-cicd`), gathers the machine's IP and current user, creates `~/.ssh` and `authorized_keys` and adds the shared Jenkins SSH public key so the node can be used by the pool. It then builds a new node entry (name like `<host>-<ASSIGN_LABEL>`, label, host, sshUser, remoteFS for that instance), merges it into the config for the specified instance(s) only, writes the updated YAML, and commits and pushes a branch and creates a GitLab Merge Request. After that MR is merged, downstream jobs add the node as a Lockable resource / JNLP agent on Jenkins.

### Pointing the perf pipeline at your node

Use the pipeline parameters (no changes to `ci/perf-configs.yaml`):

- Set **`CUSTOM_NODE_LABEL_H100`** to your label (e.g. `a4u8g-0141_H100`) to use that node for all H100 configs; leave empty or `"null"` to use the shared pool.
- Set **`CUSTOM_NODE_LABEL_RTXPRO6000BW`** similarly for RTXPRO6000BW configs.

When a custom label is set, the pipeline replaces that GPU type’s `nodeLabel` with your label for `getNode()`, skips `installInfraOnBareMetal()` for that label, and (when only custom nodes are selected) skips the nv-one-click prereqs stage.

---

## Key Design Decisions

**One node reservation per GPU type, not per config.**
Node reservation is the most expensive operation (minutes of queue wait). Batching all configs for
a given node label onto a single reservation minimises this cost significantly.

**Infrastructure supplied by the lockable-resource pool.**
The pipeline validates the pre-provisioned driver and container runtime before starting benchmarks.
It fails early rather than attempting to repair or mutate a node that does not meet the contract.

**Configs run sequentially within a node.**
Concurrent GPU workloads on the same machine would produce unreliable performance numbers.
Sequential execution ensures clean, isolated measurements for each configuration.

**Benchmark results are config-scoped.**
Each config's results are copied into `perf/benchmark/results/<config-id>/` before the next config
runs. This prevents later configs from overwriting earlier results and ensures every config's
artifacts are independently archived and uploaded to MinIO.

**`enabled` flag for temporary disablement.**
Rather than deleting configs that cannot currently run (e.g., multi-GPU configs when only
single-GPU nodes are available), they are kept in the file with `enabled: false`. This preserves
the configuration for easy re-enabling and documents the intended future test matrix.
