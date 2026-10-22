# Model Deployment

Deploy fine-tuned models from the Data Flywheel pipeline to inference platforms with a single API call.

The deployment feature uses a **plugin-based adapter pattern** -- the core API and async task layer are platform-agnostic, while each target platform is implemented as a self-contained adapter. This makes it easy to add new deployment targets without touching the core code.

## Supported Platforms

| Platform | Status | Guide |
|----------|--------|-------|
| **DataRobot** | **Functional** | [DataRobot Adapter](datarobot.md) |
| **Custom NIM** | **Functional** | [Custom NIM Adapter](custom-nim.md) |
| **NMP** | Available (experimental) | [NMP Adapter](nmp.md) |
| Dynamo | Placeholder (not yet implemented) | -- |

## Quick Start

### 1. Deploy a model

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

> **Note:** The `deployment_id` is the **DataRobot Deployment ID** visible in the DataRobot UI, not the internal LRS ID. See [Find your DEPLOYMENT_ID](datarobot.md#find-your-deployment_id) for details.

The API returns immediately with a `201` response. The actual deployment runs asynchronously.

### 2. Poll for status

```bash
curl -X GET 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>/status'
```

### 3. Use the inference endpoint

Once `status` is `ready`, the `endpoint_url` field contains the full inference URL. See the platform-specific guides for request examples.

---

## API Reference

All endpoints live under **`/api/model-deployments`**.

### Create Deployment

`POST /api/model-deployments`

```json
{
  "model_name": "namespace/model@version",
  "deployment_name": "human-readable-name",
  "platform": "datarobot",
  "platform_config": { ... }
}
```

**Request Fields**

| Field | Required | Description |
|-------|----------|-------------|
| `model_name` | Yes | Customized model name in `namespace/model@version` format. Must exist in the customizations collection. |
| `deployment_name` | Yes | Human-readable name for this deployment. |
| `platform` | Yes | Target platform: `"datarobot"`, `"custom_nim"`, `"nmp"`. |
| `platform_config` | Conditional | Platform-specific configuration. Required for DataRobot (must include `deployment_id`). See platform guides. |
| `datastore_url` | No | Override the datastore base URL. Defaults to `datastore_base_url` from system config. |
| `job_id` | No | DFW job ID that produced the model (reference only). |

**Response:** `201 Created` with the deployment record in `pending` status.

### Get Deployment Details

`GET /api/model-deployments/{deployment_record_id}`

Returns the full deployment record including status, endpoint URL, timestamps, and platform metadata.

### Check Status

`GET /api/model-deployments/{deployment_record_id}/status`

Returns a lightweight status object:

```json
{
  "id": "<DEPLOYMENT_RECORD_ID>",
  "status": "ready",
  "endpoint_url": "https://...",
  "progress": 1.0,
  "message": "Deployment successful",
  "error": null,
  "updated_at": "2026-02-13T12:00:00Z"
}
```

### Get Logs

`GET /api/model-deployments/{deployment_record_id}/logs?limit=100`

Returns timestamped log entries for the deployment lifecycle. Query parameter: `limit` (default: 100, max: 1000).

### List Deployments

`GET /api/model-deployments`

Query parameters:

| Parameter | Description |
|-----------|-------------|
| `platform` | Filter by platform (`datarobot`, `custom_nim`, `nmp`) |
| `status` | Filter by status (`pending`, `deploying`, `ready`, `failed`, ...) |
| `job_id` | Filter by source DFW job ID |
| `page` | Page number (default: 1) |
| `page_size` | Items per page (default: 20, max: 100) |

### Delete / Undeploy

`DELETE /api/model-deployments/{deployment_record_id}`

Initiates an async undeploy. The platform adapter handles cleanup (for example, removing volume mounts, shutting down NMP deployments).

### List Available Platforms

`GET /api/model-deployments/platforms`

Returns platforms that have registered adapters in the current build.

---

## Deployment Statuses

| Status | Description |
|--------|-------------|
| `pending` | Record created, Celery task queued. |
| `downloading` | (reserved) Model download in progress. |
| `deploying` | Adapter is executing the deployment workflow. |
| `ready` | Model is serving traffic. Endpoint URL available. |
| `failed` | Deployment failed. Check `error` and logs. |
| `deleting` | Undeploy in progress. |
| `deleted` | Successfully undeployed. |

---

## Further Reading

| Topic | Link |
|-------|------|
| DataRobot adapter -- config, workflow, inference examples | [DataRobot Adapter](datarobot.md) |
| Custom NIM adapter -- build & push to NGC / JFrog | [Custom NIM Adapter](custom-nim.md) |
| NMP adapter -- config, workflow, inference examples | [NMP Adapter](nmp.md) |
| Architecture -- adapter pattern, request lifecycle, code map | [Architecture](architecture.md) |
| Common issues and debugging | [Troubleshooting](troubleshooting.md) |
