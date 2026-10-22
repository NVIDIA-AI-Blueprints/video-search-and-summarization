# Custom NIM Adapter

The Custom NIM adapter builds a customized NVIDIA Inference Microservice (NIM) Docker image from fine-tuned model weights and pushes it to a container registry. It does **not** deploy a running service -- the output is a ready-to-pull Docker image.

**Source:** `src/lib/deployment/adapters/custom_nim/`

---

## Overview

After a Data Flywheel embedding customization job completes, the trained model checkpoint is stored in the DataStore. The Custom NIM adapter:

1. Downloads the checkpoint from the DataStore
2. Layers it onto a base NIM image
3. Pushes the resulting image to **NGC** (`nvcr.io`) or **JFrog Artifactory** (`artifactory.nvidia.com`)

The pushed image is a fully self-contained NIM that can be deployed anywhere -- Kubernetes, OpenShift, Docker Compose, or bare metal.

### Supported Registries

| Registry | Hostname | Use Case |
|----------|----------|----------|
| **NGC** (default) | `nvcr.io` | Production NIM images in NVIDIA GPU Cloud |
| **JFrog Artifactory** | `artifactory.nvidia.com` | Internal Artifactory, `sw-blueprint-dfw-docker-local` repo |

### Build Engine

Uses [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane) (Google's go-containerregistry) to append model layers via HTTP registry API calls. Truly rootless — no Docker daemon, no root, no user namespaces. Works on restricted OpenShift clusters with `MustRunAsRange` SCC.

---

## Prerequisites

1. A completed customization job with model weights in the DataStore.
2. Registry credentials:
   - **NGC**: `$oauthtoken` / `NGC_API_KEY` (default, no extra config needed)
   - **JFrog**: Artifactory username and API key
3. A Kubernetes cluster with the Helm chart deployed (`crane.enabled: true`), which provisions the ServiceAccount, PVC, and RBAC.

---

## Build Workflow

### Crane Engine (Production)

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Celery Worker   │     │   Crane K8s Job   │     │ Container Registry│
│                  │     │                  │     │  (nvcr.io / JFrog)│
│ 1. Download model│     │                  │     │                  │
│    from DataStore│     │                  │     │                  │
│ 2. Write to PVC  │────>│ 3. tar model     │     │                  │
│                  │     │ 4. crane append  │────>│ 5. Image pushed  │
│                  │     │    (add layer)   │     │    with model    │
│                  │     │ 5. crane mutate  │     │    layer + ENV   │
│                  │     │    (set ENV vars)│     │                  │
│ 6. Cleanup       │     │                  │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

Key details:

- **No Dockerfile needed.** Crane appends a tar layer directly onto the base image manifest via the registry HTTP API.
- **No root or user namespaces.** Works on restricted OpenShift clusters with `MustRunAsRange` SCC.
- The model checkpoint is placed at `/model-store/` inside the image (separate from the base image's `/opt/nim/model-store/`).
- `NIM_CUSTOM_MODEL=/model-store`, `NIM_FT_MODEL=/model-store`, `NIM_SERVED_MODEL_NAME`, and `NIM_MODEL_NAME` are baked in as environment variables.

---

## API Usage

### Push to NGC (default)

```bash
curl -X POST 'https://dataflywheel-api-rc.stg.astra.nvidia.com/api/model-deployments' \
  -H 'Content-Type: application/json' \
  -d '{
  "model_name": "dfwbp/3ju@cust-8LczXgMhrAAsQRaB4q5uAo",
  "deployment_name": "my-custom-embedqa",
  "platform": "custom_nim",
  "platform_config": {
    "base_nim_image": "nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.9.0",
    "registry_type": "ngc",
    "target_registry": "nvcr.io",
    "target_repo": "nvstaging/blueprint",
    "target_image_name": "dfw-llama-3.2-nv-embedqa-1b-customized",
    "target_tag": "0.0.1"
  }
}'
```

The resulting image is pushed to:

```
nvcr.io/nvstaging/blueprint/dfw-llama-3.2-nv-embedqa-1b-customized:0.0.1
```

### Push to JFrog Artifactory (`sw-blueprint-dfw-docker-local`)

```bash
curl -X POST 'https://dataflywheel-api-rc.stg.astra.nvidia.com/api/model-deployments' \
  -H 'Content-Type: application/json' \
  -d '{
  "model_name": "dfwbp/3ju@cust-8LczXgMhrAAsQRaB4q5uAo",
  "deployment_name": "my-custom-embedqa-jfrog",
  "platform": "custom_nim",
  "platform_config": {
    "registry_type": "jfrog",
    "target_registry": "artifactory.nvidia.com",
    "target_repo": "sw-blueprint-dfw-docker-local",
    "target_image_name": "dfw-llama-3.2-nv-embedqa-1b-customized",
    "target_tag": "0.0.1",
    "jfrog_username": "<your-artifactory-username>",
    "jfrog_password": "<your-artifactory-api-key>"
  }
}'
```

The resulting image is pushed to:

```
artifactory.nvidia.com/sw-blueprint-dfw-docker-local/dfw-llama-3.2-nv-embedqa-1b-customized:0.0.1
```

> **Note:** The `sw-blueprint-dfw-docker-local` repo is a **local** Docker repository on NVIDIA Artifactory, owned by the Blueprint DFW team. A read-only CI/CD service account (`svc-sw-blueprint-dfw-docker-cicd`) is available for pull-only access by external consumers like DataRobot.

---

## Platform Config Reference

All fields can be set in `config/custom_nim_deployment_config.yaml` (defaults) or overridden per-request via `platform_config`.

### Registry & Image

| Field | Default | Description |
|-------|---------|-------------|
| `registry_type` | `"ngc"` | Informational: `"ngc"` or `"jfrog"`. |
| `target_registry` | `"nvcr.io"` | Registry hostname to push to. |
| `target_repo` | `"nvstaging/blueprint"` | Repository path within the registry. |
| `target_image_name` | `"dfw-llama-3.2-nv-embedqa-1b-customized"` | Image name. |
| `target_tag` | `"0.0.1"` | Image tag. |
| `base_nim_image` | `"urm.nvidia.com/.../llama-3.2-nv-embedqa-1b-v2:1.9.0"` | Base NIM image to layer the model onto. |
| `nim_model_name` | `"nvidia/llama-3.2-nv-embedqa-1b-v2"` | Model name baked into `NIM_MODEL_NAME` env var. |

### Credentials

There are **two ways** to provide registry push credentials. They are mutually exclusive -- only one is needed:

| | `crane.registry_secret` | `docker_credentials` |
|---|---|---|
| **What it is** | Name of an **existing** K8s secret (`kubernetes.io/dockerconfigjson`) in the namespace | Username + password from which the adapter **creates** a temporary secret at runtime |
| **Who creates the secret** | Cluster admin / Helm chart / external secret manager | The adapter, automatically |
| **Lifecycle** | Permanent -- persists across deployments | Ephemeral -- created before the crane Job, deleted after |
| **When to use** | **Production / OpenShift** -- use a pre-provisioned secret with push permissions (e.g. `nvstaging-image-pull`) | **Local dev / testing** -- when you don't have a pre-existing secret |

**How the adapter decides:**

1. If `crane.registry_secret` is set (non-empty), use that existing secret directly.
2. Otherwise, build a `dockerconfigjson` secret from `docker_credentials` and create it as a temporary K8s secret.

**Helm wiring:** When `crane.registrySecret` is set in Helm `values.yaml`, it is injected
as `CUSTOM_NIM_CRANE_REGISTRY_SECRET` into the celery worker pod. The Python config reads
this env var automatically, so **no per-request `platform_config` override is needed**:

```
Helm values.yaml              celery worker env                     Python CraneConfig
crane.registrySecret   ──▶   CUSTOM_NIM_CRANE_REGISTRY_SECRET  ──▶  crane.registry_secret
"nvstaging-image-pull"        "nvstaging-image-pull"                  "nvstaging-image-pull"
```

#### `docker_credentials` (NGC — default)

| Field | Default | Description |
|-------|---------|-------------|
| `docker_username` | `$CUSTOM_NIM_DOCKER_USERNAME` → `"$oauthtoken"` | NGC registry username. |
| `docker_password` | `$CUSTOM_NIM_DOCKER_PASSWORD` → `$NGC_API_KEY` | NGC registry password. |

These are always used to **pull the base NIM image from `nvcr.io`**, and also used to **push** when the target is NGC.

> **Important:** The `NGC_API_KEY` injected into the celery worker pod by Helm may
> only have **pull** permissions. If the crane Job fails with `DENIED: Access Denied`
> when pushing, set `crane.registry_secret` to a secret that has push access
> (e.g. `nvstaging-image-pull`) instead of relying on `docker_credentials`.

#### `jfrog_credentials` (JFrog Artifactory)

| Field | Default | Description |
|-------|---------|-------------|
| `jfrog_username` | `$CUSTOM_NIM_JFROG_USERNAME` → `""` | JFrog Artifactory username. |
| `jfrog_password` | `$CUSTOM_NIM_JFROG_PASSWORD` → `""` | JFrog Artifactory password / API token. |

Only needed when `registry_type` is `"jfrog"`. The adapter automatically builds a combined
secret with NGC creds (for pulling the base image from `nvcr.io`) + JFrog creds (for pushing).

**Helm wiring for JFrog:**

```yaml
crane:
  jfrog:
    username: "your-jfrog-user"
    secretRef:
      name: "jfrog-api"        # K8s secret containing the JFrog API key
      key: "JFROG_API_KEY"     # key within the secret
```

### DataStore

| Field | Default | Description |
|-------|---------|-------------|
| `datastore_endpoint` | `"http://data-store.test"` | DataStore base URL (no path). The adapter appends `/v1/hf` internally. |
| `datastore_token` | `$HF_TOKEN` | Auth token for the DataStore. |

### Build Options

| Field | Default | Description |
|-------|---------|-------------|
| `cleanup_after_build` | `true` | Remove build context after completion. |
| `skip_download` | `false` | Skip model download (use pre-populated weights). |
| `download_max_retries` | `3` | Max download retry attempts. |
| `download_retry_delay` | `10` | Delay between retries (seconds). |

### Crane Engine Settings

| Field | Default | Description |
|-------|---------|-------------|
| `crane.image` | `"gcr.io/go-containerregistry/crane:debug"` | Crane container image. |
| `crane.namespace` | `""` (auto-detect) | K8s namespace for crane Jobs. |
| `crane.service_account` | `"dfw-crane-builder"` | ServiceAccount for crane pods. |
| `crane.pvc_name` | `"crane-build-context"` | PVC shared between celery worker and crane pods. |
| `crane.build_context_dir` | `"/crane-workspace"` | Mount path of the PVC. |
| `crane.registry_secret` | `$CUSTOM_NIM_CRANE_REGISTRY_SECRET` → `""` | Existing `dockerconfigjson` secret. Helm injects this env var from `crane.registrySecret`. Empty = auto-created from credentials. |
| `crane.job_timeout_seconds` | `3600` | Max crane Job duration (1 hour). |
| `crane.job_ttl_seconds_after_finished` | `300` | Auto-cleanup finished Jobs (5 min). |
| `crane.poll_interval_seconds` | `10` | Status polling interval. |

### Timeouts

| Field | Default | Description |
|-------|---------|-------------|
| `download_timeout_seconds` | `3600` | Model download timeout (the crane Job itself has `crane.job_timeout_seconds`). |

---

## Configuration File

Default configuration lives in `config/custom_nim_deployment_config.yaml`. Any value can be overridden per-request via `platform_config` in the API call.

### NGC Configuration (default)

```yaml
registry:
  type: "ngc"
  target_registry: "nvcr.io"
  target_repo: "nvstaging/blueprint"
  target_image_name: "dfw-llama-3.2-nv-embedqa-1b-customized"
  target_tag: "0.0.1"

base_image:
  image: "nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.9.0"

# NGC credentials resolve automatically:
#   username: "$oauthtoken"
#   password: $NGC_API_KEY env var
docker_credentials:
  username: ""
  password: ""
```

### JFrog Configuration

```yaml
registry:
  type: "jfrog"
  target_registry: "artifactory.nvidia.com"
  target_repo: "sw-blueprint-dfw-docker-local"
  target_image_name: "dfw-llama-3.2-nv-embedqa-1b-customized"
  target_tag: "0.0.1"

# Base image is always pulled from nvcr.io using NGC creds (docker_credentials)
base_image:
  image: "nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.9.0"

# NGC creds for pulling base image (defaults to $oauthtoken / NGC_API_KEY)
docker_credentials:
  username: ""
  password: ""

# JFrog creds for pushing the customized image
jfrog_credentials:
  username: "<your-artifactory-username>"
  password: "<your-artifactory-api-key>"
```

---

## Helm Chart Setup (Kubernetes / OpenShift)

The crane engine requires Kubernetes resources provisioned by the Helm chart. Enable it in your values file:

```yaml
crane:
  enabled: true
  image: "gcr.io/go-containerregistry/crane:debug"
  serviceAccount: "dfw-crane-builder"
  buildContextDir: "/crane-workspace"
  pvc:
    name: "crane-build-context"
    size: "50Gi"
    storageClassName: "your-storage-class"  # e.g., "pdx02-cdot03-ainfs01-nfsv3"
  registrySecret: "nvstaging-image-pull"  # pre-existing secret with push access
```

This provisions:

| Resource | Template | Purpose |
|----------|----------|---------|
| ServiceAccount + Role + RoleBinding | `crane-rbac.yaml` | Allows the celery worker to create Jobs and Secrets |
| PersistentVolumeClaim | `crane-pvc.yaml` | Shared storage for model weights between celery worker and crane pods |
| Secret (`dockerconfigjson`) | `secrets.yaml` | Registry credentials (only created when `registrySecret` is empty) |

When `registrySecret` is set (e.g. `"nvstaging-image-pull"`), Helm does **not** create its own `dockerconfigjson` secret — the adapter uses the pre-existing secret directly. When empty, Helm creates a `custom-nim` secret from `imagePullSecrets`.

The celery worker deployment is automatically updated with:
- The crane ServiceAccount
- The PVC volume mount at `buildContextDir`
- `CUSTOM_NIM_CRANE_REGISTRY_SECRET` — injected when `registrySecret` is set, so the Python adapter picks up the secret name automatically
- `NGC_API_KEY` and `HF_TOKEN` from Kubernetes secrets

### OpenShift

Add the crane ServiceAccount to the SCC `serviceAccounts` list:

```yaml
openshift:
  createSCC: true
  serviceAccounts:
    - default
    - dfw-crane-builder   # Required for crane builds
    # ... other service accounts
```

No special SCC privileges are needed -- crane runs as a regular non-root user.

---

## Polling & Status

After triggering a build, poll for status:

```bash
# Check status
curl -s 'https://dataflywheel-api-rc.stg.astra.nvidia.com/api/model-deployments/<ID>/status'

# Get full details (includes platform_metadata with image_tag)
curl -s 'https://dataflywheel-api-rc.stg.astra.nvidia.com/api/model-deployments/<ID>'
```

### Success Response

```json
{
  "id": "3153ac18-7149-4407-91ef-b85cd41d1a68",
  "deployment_name": "my-custom-embedqa",
  "platform": "custom_nim",
  "status": "ready",
  "error": null,
  "platform_metadata": {
    "image_tag": "nvcr.io/nvstaging/blueprint/dfw-llama-3.2-nv-embedqa-1b-customized:0.0.1",
    "base_nim_image": "nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.9.0",
    "registry_type": "ngc",
    "target_registry": "nvcr.io",
    "build_engine": "crane",
    "crane_job": "crane-my-custom-embedqa"
  }
}
```

The `image_tag` in `platform_metadata` is the full image reference you can use to pull and deploy the customized NIM.

---

## Using the Built Image

Once the image is pushed, deploy it using standard NIM methods:

### Docker

```bash
docker run --rm --gpus all \
  -p 8000:8000 \
  nvcr.io/nvstaging/blueprint/dfw-llama-3.2-nv-embedqa-1b-customized:0.0.1
```

### Kubernetes (NIM Operator)

```yaml
apiVersion: apps.nvidia.com/v1alpha1
kind: NIMService
metadata:
  name: custom-embedqa
spec:
  image:
    repository: nvcr.io/nvstaging/blueprint/dfw-llama-3.2-nv-embedqa-1b-customized
    tag: "0.0.1"
  resources:
    gpu: 1
```

### JFrog Image

```bash
docker run --rm --gpus all \
  -p 8000:8000 \
  artifactory.nvidia.com/sw-blueprint-dfw-docker-local/dfw-llama-3.2-nv-embedqa-1b-customized:0.0.1
```

---

## Undeploy

Custom NIM is build-and-push only. There is no running deployment to tear down. The `DELETE` endpoint marks the record as deleted but does not remove the pushed image from the registry.

```bash
curl -X DELETE 'https://dataflywheel-api-rc.stg.astra.nvidia.com/api/model-deployments/<ID>'
```

To remove the image, delete it directly from the container registry (NGC UI or Artifactory UI).

---

## Troubleshooting

### `Permission denied: '/crane-workspace'`

The crane PVC is not mounted on the celery worker pod. Ensure `crane.enabled: true` in your Helm values and the deployment has been synced.

### `DENIED: Access Denied` when pushing

The crane Job authenticated successfully but the credentials don't have **push** permissions. This commonly happens when the `NGC_API_KEY` in the celery worker pod only has pull access.

**Fix:** Set `crane.registrySecret` in Helm to a pre-existing K8s secret with push access:

```yaml
# values.yaml (this is already the default)
crane:
  registrySecret: "nvstaging-image-pull"
```

Helm injects this as `CUSTOM_NIM_CRANE_REGISTRY_SECRET` into the celery worker, so the adapter uses it automatically. If you cannot change Helm values, override per-request:

```json
{
  "platform_config": {
    "crane": {
      "registry_secret": "nvstaging-image-pull"
    }
  }
}
```

### `401 Unauthorized` pulling base image

The registry credentials in the auto-created secret don't cover the base image registry. Fix:
- Use a base image from the same registry as your target (for example, `nvcr.io/nim/...` when pushing to `nvcr.io`)
- Or provide credentials that cover both registries via `docker_username` / `docker_password`

### `Bad credentials` for JFrog

JFrog Artifactory uses different auth than NGC. Provide explicit credentials:

```json
{
  "platform_config": {
    "docker_username": "<artifactory-username>",
    "docker_password": "<artifactory-api-key>"
  }
}
```

### Crane Job timeout

Large models may exceed the default 1-hour timeout. Override per-request:

```json
{
  "platform_config": {
    "crane": {
      "job_timeout_seconds": 7200
    }
  }
}
```

### Build context disk full

The shared PVC stores model weights temporarily. Increase the PVC size in Helm values:

```yaml
crane:
  pvc:
    size: "100Gi"
```

---

## See Also

- [Getting Started & API Reference](index.md)
- [Architecture](architecture.md)
- [Troubleshooting](troubleshooting.md)
