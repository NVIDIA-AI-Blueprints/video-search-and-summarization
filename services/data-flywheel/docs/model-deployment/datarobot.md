# DataRobot Adapter

The DataRobot adapter deploys fine-tuned models to a DataRobot **LRS (Live Runtime Service)** deployment backed by NVIDIA NIM on Kubernetes. It is the primary, fully functional deployment adapter.

**Source:** `src/lib/deployment/adapters/datarobot/`

---

## Prerequisites

1. The model must exist in the customizations collection (a DFW customization/training job has completed).
2. You need the **DataRobot Deployment ID** -- the user-facing ID visible in the DataRobot Console. See [Find your DEPLOYMENT_ID](#find-your-deployment_id) below.

---

## Find your DEPLOYMENT_ID

The `DEPLOYMENT_ID` used in the API is the **DataRobot Deployment ID** -- the platform-level identifier visible to users. This is different from the internal LRS ID used by Kubernetes.

### Where to find it

1. Navigate to the [Deployments Dashboard](https://datarobot.prd.astra.nvidia.com/console-nextgen/dashboard) in DataRobot.
2. Select the model deployment you want to use.
3. The `DEPLOYMENT_ID` can be found in two places:
   - In the **URL** of your browser: `.../deployments/<DEPLOYMENT_ID>/overview`
   - On the **Overview** tab, listed under **Deployment ID**

### Example

```
URL:  https://datarobot.prd.astra.nvidia.com/console-nextgen/deployments/69326f63a1d39690a7ed0d4e/overview
                                                                         └──────────────────────────┘
                                                                          This is your DEPLOYMENT_ID
```

### DEPLOYMENT_ID vs LRS ID

Users only need the **DataRobot Deployment ID**. The adapter resolves the internal LRS name automatically at runtime via a Kubernetes label lookup.

| | DataRobot Deployment ID | LRS ID |
|---|---|---|
| **Example** | `69326f63a1d39690a7ed0d4e` | `69326f65a1d39690a7ed0d69` |
| **Who uses it** | You (API callers) | The adapter (internally) |
| **Where it's visible** | DataRobot UI, browser URL | `kubectl` / k9s only |
| **Used for** | Inference endpoint URL, API requests | k8s Deployment name, PVC, Jobs |

How the adapter resolves the LRS ID from your Deployment ID:

```
You pass:  deployment_id = "69326f63a1d39690a7ed0d4e"
                                     │
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │  k8s label lookup:                              │
            │  datarobot-deployment-id = 69326f63...0d4e      │
            │           ↓                                     │
            │  datarobot-lrs-name = lrs-69326f65...0d69       │
            └─────────────────────────────────────────────────┘
                                     │
                      ┌──────────────┴──────────────┐
                      ▼                              ▼
            k8s operations                  Inference endpoint
            lrs-{lrs_id}                    /api/v2/deployments/{deployment_id}/...
```

### Look up the LRS ID (for debugging)

When debugging issues on the DataRobot cluster (for example, pod health, NIM container logs, Triton errors), you need the LRS ID to find the corresponding Kubernetes resources. Use the following command to resolve it from a Deployment ID:

```bash
# Get the LRS name for a given DataRobot Deployment ID
kubectl get deployment -n dr-app \
  -l datarobot-deployment-id=69326f63a1d39690a7ed0d4e \
  -o jsonpath='{.items[0].metadata.labels.datarobot-lrs-name}'
```

This returns the full LRS name (for example, `lrs-69326f65a1d39690a7ed0d69`), which you can then use to inspect pods, logs, and events:

```bash
# List pods for this LRS deployment
kubectl get pods -n dr-app -l datarobot-lrs-name=lrs-69326f65a1d39690a7ed0d69

# View NIM container logs
kubectl logs -n dr-app <POD_NAME> -c lrs-69326f65a1d39690a7ed0d69-additional-container-0

# Describe the pod (events, conditions, resource usage)
kubectl describe pod -n dr-app <POD_NAME>

# Check GPU status on the pod's node
kubectl exec -n dr-app <POD_NAME> -c lrs-69326f65a1d39690a7ed0d69-additional-container-0 -- nvidia-smi
```

---

## Deployment Workflow

When you create a deployment with `platform: "datarobot"`, the adapter executes these steps inside a Celery worker:

1. **Resolve LRS** -- The adapter queries Kubernetes for the Deployment labelled with `datarobot-deployment-id=<DEPLOYMENT_ID>` and extracts the internal LRS name.
2. **Create PVC** -- The adapter creates a `PersistentVolumeClaim` (`nim-sft-model-<lrs_id>`) to store model artifacts.
3. **Download model** -- A Kubernetes `Job` (`download-sft-<lrs_id>`) pulls the model from the DataStore using the HuggingFace Hub CLI. The adapter appends `/v1/hf` to the datastore base URL to form the HF-compatible endpoint.
4. **Patch LRS deployment** -- The adapter patches the existing `lrs-<lrs_id>` Kubernetes deployment with:
   - Volume mounts for `/model-store` and `/dev/shm`
   - NIM environment variables: `NIM_CUSTOM_MODEL`, `NIM_FT_MODEL`, `NIM_SERVED_MODEL_NAME`, `NIM_MODEL_NAME`
5. **Wait for rollout** -- The adapter polls until the patched deployment reports ready replicas.

---

## API Usage

### Create a DataRobot Deployment

```bash
curl -X POST 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments' \
  -H 'Content-Type: application/json' \
  -d '{
  "model_name": "dfwbp/my-model@cust-abc123",
  "deployment_name": "my-embedding-prod",
  "platform": "datarobot",
  "platform_config": {
    "deployment_id": "69326f63a1d39690a7ed0d4e",
    "model_type": "embedding"
  }
}'
```

### Platform Config Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `deployment_id` | **Yes** | -- | DataRobot Deployment ID (the user-facing ID from the DataRobot UI). |
| `model_type` | No | `"embedding"` | Model type: `"embedding"`, `"chat"`, `"reranking"`, or `"vision"`. Controls the inference endpoint path. |

---

## Configuration File

All DataRobot-specific defaults are in **`config/datarobot_deployment_config.yaml`**. Any value can be overridden per-request via `platform_config`.

```yaml
# DataStore Configuration - where models are downloaded from
datastore:
  # DataStore base URL (no path). The adapter appends /v1/hf for HF-compatible download.
  endpoint: "https://dataflywheel-data-store.prd.astra.nvidia.com"
  # Kubernetes secret containing HF_TOKEN for DataStore authentication
  token_secret: "datastore-hf-token"
  # Container image used by the download Job (needs pip + python)
  puller_image: "urm.nvidia.com/nvidia-nvcr-docker-remote/nvidia/pytorch:24.01-py3"

# Kubernetes Deployment Configuration
deployment:
  # Namespace where the NIM/LRS deployments live
  namespace: "dr-app"
  # deployment_id is REQUIRED per request (no default)
  # This is the DataRobot Deployment ID (user-facing).

# Storage Configuration
storage:
  class: "nfs41pnfs-sc"
  size: "100Gi"

# Image Pull Configuration
image_pull:
  secret: "ngc-docker-pullsecret"

# DataRobot API Configuration
datarobot_api:
  url: "https://datarobot.prd.astra.nvidia.com"

# Security Context (ASTRA / OpenShift)
security_context:
  run_as_user: 1000990000
  run_as_group: 1000990000
  fs_group: 1000990000

# Timeouts (seconds)
timeouts:
  pvc_bind: 300      # 5 min  - PVC binding
  download: 3600     # 1 hour - model download
  rollout: 300       # 5 min  - deployment rollout (per attempt)
  rollout_max_pod_retries: 3  # pod restart retries on startup failure
  pod_reschedule_wait: 30     # wait (seconds) for new pod after restart

# Model Type Configuration
# Determines the inference endpoint path
model:
  type: "embedding"
```

### Configuration Reference

| YAML Key | Config Field | Description |
|----------|-------------|-------------|
| `datastore.endpoint` | `datastore_endpoint` | DataStore base URL. Adapter appends `/v1/hf`. |
| `datastore.token_secret` | `datastore_token_secret` | K8s secret with `HF_TOKEN` key. |
| `datastore.puller_image` | `puller_image` | Container image for the download Job. |
| `deployment.namespace` | `k8s_namespace` | K8s namespace for NIM/LRS pods. |
| `storage.class` | `storage_class` | Storage class for model PVC. |
| `storage.size` | `storage_size` | PVC size (for example, `"100Gi"`). |
| `image_pull.secret` | `image_pull_secret` | K8s image pull secret name. |
| `datarobot_api.url` | `datarobot_api_url` | DataRobot API base URL. |
| `security_context.*` | `run_as_user`, `run_as_group`, `fs_group` | Pod security context UIDs. |
| `timeouts.pvc_bind` | `pvc_bind_timeout_seconds` | PVC bind timeout. |
| `timeouts.download` | `download_timeout_seconds` | Download job timeout. |
| `timeouts.rollout` | `rollout_timeout_seconds` | Deployment rollout timeout (per attempt). |
| `timeouts.rollout_max_pod_retries` | `rollout_max_pod_retries` | Max pod restart retries during rollout. |
| `timeouts.pod_reschedule_wait` | `pod_reschedule_wait_seconds` | Wait time (seconds) for new pod after restart. |
| `model.type` | `model_type` | Default model type for endpoint selection. |

---

## Access inference

Once a deployment reaches `ready`, the `endpoint_url` field contains the full inference URL.

### Generate a DataRobot API token

1. Log in to [DataRobot](https://datarobot.prd.astra.nvidia.com) with SSO.
2. Navigate to [Developer Tools](https://datarobot.prd.astra.nvidia.com/account/developer-tools).
3. Click **Create new key** to generate your API token.

### Inference Endpoints by Model Type

| Model Type | Endpoint Path | Full URL |
|------------|--------------|----------|
| `embedding` | `/directAccess/nim/v1/embeddings` | `https://datarobot.prd.astra.nvidia.com/api/v2/deployments/<DEPLOYMENT_ID>/directAccess/nim/v1/embeddings` |
| `chat` | `/chat/completions` | `https://datarobot.prd.astra.nvidia.com/api/v2/deployments/<DEPLOYMENT_ID>/chat/completions` |
| `reranking` | `/directAccess/nim/v1/ranking` | `https://datarobot.prd.astra.nvidia.com/api/v2/deployments/<DEPLOYMENT_ID>/directAccess/nim/v1/ranking` |
| `vision` | `/directAccess/nim/v1/infer` | `https://datarobot.prd.astra.nvidia.com/api/v2/deployments/<DEPLOYMENT_ID>/directAccess/nim/v1/infer` |

### Embedding Example

```bash
curl -X POST \
  'https://datarobot.prd.astra.nvidia.com/api/v2/deployments/69326f63a1d39690a7ed0d4e/directAccess/nim/v1/embeddings' \
  -H "Authorization: Bearer $DATAROBOT_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "input": ["What is the capital of France?"],
    "model": "dfwbp/my-model",
    "input_type": "query"
  }'
```

### Chat Example

```bash
curl -X POST \
  'https://datarobot.prd.astra.nvidia.com/api/v2/deployments/69326f63a1d39690a7ed0d4e/chat/completions' \
  -H "Authorization: Bearer $DATAROBOT_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "dfwbp/my-model",
    "messages": [
      {"role": "user", "content": "Explain GPU computing in one sentence."}
    ],
    "max_tokens": 1024
  }'
```

### Python Example (Embedding)

```python
import requests

DATAROBOT_API_TOKEN = "your-token-here"
DEPLOYMENT_ID = "69326f63a1d39690a7ed0d4e"
MODEL_NAME = "dfwbp/my-model"

url = f"https://datarobot.prd.astra.nvidia.com/api/v2/deployments/{DEPLOYMENT_ID}/directAccess/nim/v1/embeddings"

response = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {DATAROBOT_API_TOKEN}",
        "Content-Type": "application/json",
    },
    json={
        "input": ["What is the capital of France?"],
        "model": MODEL_NAME,
        "input_type": "query",
    },
)

print(response.json())
```

### List models in a deployment

To find the model name currently loaded in a deployment:

```bash
curl -X GET \
  'https://datarobot.prd.astra.nvidia.com/api/v2/deployments/69326f63a1d39690a7ed0d4e/directAccess/nim/v1/models' \
  -H "Authorization: Bearer $DATAROBOT_API_TOKEN"
```

---

## Undeploy

```bash
curl -X DELETE 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>'
```

The adapter:
- Resolves the internal LRS ID from the DataRobot Deployment ID
- Removes the model-store volume mount and NIM env vars from `lrs-<lrs_id>` (the `/dev/shm` mount is preserved)
- Deletes the download Job
- Preserves the PVC (delete manually with `kubectl delete pvc nim-sft-model-<lrs_id> -n dr-app` if needed)

---

## See Also

- [Getting Started & API Reference](index.md)
- [Architecture](architecture.md)
- [Troubleshooting](troubleshooting.md)
