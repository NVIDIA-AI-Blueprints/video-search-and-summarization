# NMP Adapter (NVIDIA Model Platform)

The NMP adapter deploys models to the **NVIDIA Model Platform** using the existing `DMSClient` (Deployment Management Service). It supports both base models and fine-tuned (customized) models. Unlike DataRobot, NMP handles model downloading internally -- the adapter only needs to trigger deployment and wait for readiness.

> **Status:** Available / experimental. The adapter is implemented and registered, but DataRobot is the primary production target.

**Source:** `src/lib/deployment/adapters/nmp/`

---

## Prerequisites

1. For **fine-tuned models**: the model must exist in the customizations collection (a DFW customization/training job has completed).
2. For **base models**: no customization record is needed -- just provide the model name (for example, `nvidia/llama-3.2-nv-embedqa-1b-v2`).
3. NMP services (NeMo Customizer, Entity Store, Deployment Management) must be reachable from the Celery worker.
4. System config must have valid `nmp_config` values for `nemo_base_url`, `nim_base_url`, `nmp_namespace`, and `datastore_base_url`.

---

## Deployment Workflow

When you create a deployment with `platform: "nmp"`, the adapter:

1. **Create deployment config** -- If `base_model_for_image` is provided, creates a deployment configuration via `DMSClient.create_deployment_config()`, specifying the base model image for the customized model.
2. **Deploy model** -- Calls `DMSClient.deploy_model()` which triggers NMP's internal deployment pipeline. NMP handles model download and NIM container provisioning.
3. **Wait for readiness** -- Polls NMP via `DMSClient.wait_for_deployment()` with progress callbacks mapped from NMP status strings (`pending` -> `queued` -> `initializing` -> `deploying` -> `ready`).
4. **Return endpoint** -- Once ready, builds the inference endpoint URL from `nim_base_url` based on the model type.

---

## API Usage

### Create an NMP Deployment (base model)

```bash
curl -X POST 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments' \
  -H 'Content-Type: application/json' \
  -d '{
  "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "deployment_name": "my-embedding-nmp",
  "platform": "nmp",
  "platform_config": {
    "model_type": "embedding",
    "context_length": 32768,
    "gpus": 1,
    "tag": "1.9.0"
  }
}'
```

### Create an NMP Deployment (customized / fine-tuned model)

When deploying a fine-tuned model, `base_model_for_image` is **required**. It tells the adapter which NIM container image to use for the customized model:

```bash
curl -X POST 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments' \
  -H 'Content-Type: application/json' \
  -d '{
  "model_name": "dfwbp/my-model@cust-abc123",
  "deployment_name": "my-embedding-nmp",
  "platform": "nmp",
  "platform_config": {
    "model_type": "embedding",
    "context_length": 32768,
    "gpus": 1,
    "tag": "1.9.0",
    "base_model_for_image": "nvidia/llama-3.2-nv-embedqa-1b-v2"
  }
}'
```

> The `platform_config` fields map directly to `NIMConfig` in `src/config.py` -- the same schema used by the flywheel pipeline. See `config/config.yaml` and `config/embedding_config_example.json` for reference values.

### Platform Config Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `model_name` | No | outer `model_name` | Full model name. If omitted, defaults to the top-level `model_name` from the request. |
| `model_type` | No | `"llm"` | Model type: `"embedding"` or `"llm"`. Determines inference endpoint path. |
| `context_length` | **Yes** | -- | Context length for ICL evaluations and model serving. |
| `gpus` | No | `null` | Number of GPUs to allocate for the deployment. |
| `tag` | No | `null` | NIM container image tag / version (for example, `"1.9.0"`). |
| `pvc_size` | No | `null` | PVC size for deployment storage (for example, `"25Gi"`). |
| `registry_base` | No | `"nvcr.io/nim"` | NIM container image registry base. Rarely overridden. |
| `base_model_for_image` | **Yes** (fine-tuned) | `null` | Base model name used to select the NIM container image. **Required** for fine-tuned models; not needed for base models. |

---

## Configuration

The NMP adapter reads system-level NMP settings from `config/config.yaml`:

```yaml
nmp_config:
  nemo_base_url: "https://dataflywheel-nemo.prd.astra.nvidia.com"
  nim_base_url: "https://dataflywheel-nim.prd.astra.nvidia.com"
  nmp_namespace: "dfwbp"
  datastore_base_url: "https://dataflywheel-data-store.prd.astra.nvidia.com"
```

---

## Inference Endpoints

NMP uses a shared NIM proxy, so the endpoint URL is the same regardless of which model is deployed:

| Model Type | Endpoint |
|------------|----------|
| `embedding` | `{nim_base_url}/v1/embeddings` |
| `llm` | `{nim_base_url}/v1/chat/completions` |

### Embedding Example

```bash
curl -X POST \
  'https://dataflywheel-nim.prd.astra.nvidia.com/v1/embeddings' \
  -H 'Content-Type: application/json' \
  -d '{
    "input": ["What is the capital of France?"],
    "model": "dfwbp/my-model",
    "input_type": "query"
  }'
```

### LLM / Chat Example

```bash
curl -X POST \
  'https://dataflywheel-nim.prd.astra.nvidia.com/v1/chat/completions' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "dfwbp/my-model",
    "messages": [
      {"role": "user", "content": "Explain GPU computing in one sentence."}
    ],
    "max_tokens": 1024
  }'
```

> **Note:** NMP endpoints typically do not require an API token when accessed from within the cluster. External access may require network configuration depending on your environment.

---

## Undeploy

```bash
curl -X DELETE 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>'
```

The adapter calls `DMSClient.shutdown_deployment()` to remove the model from NMP.

---

## Key Differences from DataRobot

| Aspect | DataRobot | NMP |
|--------|-----------|-----|
| Model download | Adapter creates a K8s Job | NMP handles internally |
| Infrastructure | Patches an existing LRS deployment | NMP provisions NIM containers |
| Endpoint | Per-deployment URL via DataRobot API | Shared NIM proxy URL |
| Authentication | DataRobot API token | Typically none (in-cluster) |
| Config file | `config/datarobot_deployment_config.yaml` | System `config/config.yaml` (`nmp_config`) |

---

## See Also

- [Getting Started & API Reference](index.md)
- [DataRobot Adapter](datarobot.md)
- [Architecture](architecture.md)
- [Troubleshooting](troubleshooting.md)
