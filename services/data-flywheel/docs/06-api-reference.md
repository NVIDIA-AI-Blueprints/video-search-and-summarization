# API Reference and Integration Guide

Learn how to integrate with the Data Flywheel Blueprint REST API to programmatically manage flywheel jobs, retraining triggers, monitor progress, and retrieve results.

## Quick Navigation

| Section | Endpoints |
|---------|-----------|
| [Job Endpoints](#core-endpoints) | Create, list, get, cancel, delete jobs |
| [Trigger Endpoints](#retraining-trigger-endpoints) | Create, manage, and execute retraining triggers |

## Base URL and Authentication

The API is served on port 8000 with the `/api` prefix:

```text
Base URL: http://your-host:8000/api

Example (local development):
http://localhost:8000/api
```

**Authentication**: Currently, no authentication is required for the API endpoints. In production deployments, ensure proper network security and access controls.

## Core Endpoints

### Create Flywheel Job

> **`POST`** `/api/jobs` - *Start a new NIM workflow job*

Creates a new flywheel job that runs the complete NIM workflow including data extraction, evaluation, and model customization.

<details>
<summary><strong>Request Details</strong></summary>

**Request Body:**

```json
{
  "workload_id": "customer-service-v1",
  "client_id": "production-app",
  "job_type": "llm_job",
  "config_name": "my-llm-config",
  "data_split_config": {
    "eval_size": 20,
    "val_ratio": 0.1,
    "min_total_records": 50,
    "limit": 10000
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `workload_id` | `string` | Yes | Identifier for the workload type in your logged data |
| `client_id` | `string` | Yes | Identifier for the client application generating the data |
| `job_type` | `string` | No | Type of job to run. Either `llm_job` (default) or `embedding_job`. |
| `config_name` | `string` | No | Optional name of a configuration created using the Configuration API. If provided, the job uses this configuration; if omitted, the default configuration for the selected `job_type` is used. |
| `data_split_config` | `object` | No | Configuration for dataset splitting. If omitted, the default `data_split_config` from the resolved job configuration is used. |

**Job Configuration:**

When using `embedding_job` type, the system expects data to be pre-loaded into Elasticsearch using one of the supported formats:

- Triplet format
- Dictionary format
- CSV format (single file)
- Separated bug pairs format (corpus + mappings files)

> **📖 For data loading details:** Refer to [Embedding Training Guide](14-embedding-training.md#loading-data)

</details>

**Success Response:**

```json
{
  "id": "507f1f77bcf86cd799439011",
  "status": "queued",
  "message": "NIM workflow started"
}
```

> **Note:** There is currently a known inconsistency where the POST endpoint returns `"queued"` but the job is actually stored with `"pending"` status. Subsequent GET requests will show the actual stored status.

**Example cURL:**

```bash
curl -X POST "http://localhost:8000/api/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "workload_id": "customer-service-v1",
    "client_id": "production-app",
    "job_type": "llm_job",
    "config_name": "my-llm-config"
  }'
```

---

### List All Jobs

> **`GET`** `/api/jobs` - *Retrieve all flywheel jobs*

Retrieves a list of all flywheel jobs with their current status and basic information.

**Success Response:**

```json
{
  "jobs": [
    {
      "id": "507f1f77bcf86cd799439011",
      "workload_id": "customer-service-v1", 
      "client_id": "production-app",
      "status": "pending",
      "started_at": "2024-01-15T10:30:00Z",
      "finished_at": null,
      "runtime_seconds": 120.5,
      "datasets": [
        {
          "name": "base-eval-dataset",
          "num_records": 150,
          "nmp_uri": "https://nmp.host/v1/datasets/dataset-123"
        }
      ],
      "error": null
    }
  ]
}
```

**Example cURL:**

```bash
curl "http://localhost:8000/api/jobs"
```

---

### Get Job Details

> **`GET`** `/api/jobs/{job_id}` - *Get comprehensive job information*

Retrieves detailed information about a specific job, including all workflow stages, evaluations, and results.

<details>
<summary><strong>Complete Response Structure</strong></summary>

```json
{
  "id": "507f1f77bcf86cd799439011",
  "workload_id": "customer-service-v1",
  "client_id": "production-app", 
  "status": "completed",
  "started_at": "2024-01-15T10:30:00Z",
  "finished_at": "2024-01-15T12:45:00Z",
  "runtime_seconds": 8100.0,
  "num_records": 1000,
  "llm_judge": {
    "model_name": "gpt-4",
    "type": "remote",
    "deployment_status": "ready",
    "error": null
  },
  "datasets": [
    {
      "name": "base-eval-dataset",
      "num_records": 150,
      "nmp_uri": "https://nmp.host/v1/datasets/dataset-123"
    }
  ],
  "nims": [
    {
      "model_name": "meta/llama-3.2-1b-instruct",
      "status": "completed",
      "deployment_status": "ready",
      "runtime_seconds": 450.5,
      "evaluations": [
        {
          "eval_type": "base-eval",
          "scores": {"accuracy": 0.85},
          "started_at": "2024-01-15T11:00:00Z",
          "finished_at": "2024-01-15T11:30:00Z",
          "runtime_seconds": 1800.0,
          "progress": 100.0,
          "nmp_uri": "https://nmp.host/v1/evaluation/jobs/eval-123",
          "mlflow_uri": "http://localhost:5000/#/experiments/123",
          "error": null
        }
      ],
      "customizations": [
        {
          "started_at": "2024-01-15T11:30:00Z",
          "finished_at": "2024-01-15T12:00:00Z",
          "runtime_seconds": 1800.0,
          "progress": 100.0,  
          "epochs_completed": 2,
          "steps_completed": 100,
          "nmp_uri": "https://nmp.host/v1/customization/jobs/custom-123",
          "customized_model": "customized-llama-3.2-1b-instruct",
          "error": null
        }
      ],
      "error": null
    }
  ],
  "error": null
}
```

</details>

**Example cURL:**

```bash
curl "http://localhost:8000/api/jobs/507f1f77bcf86cd799439011"
```

---

### Cancel Job

> **`POST`** `/api/jobs/{job_id}/cancel` - *Stop a running job*

Cancels a running job, stopping all active tasks and marking the job as cancelled.

**Success Response:**

```json
{
  "id": "507f1f77bcf86cd799439011",
  "message": "Job cancellation initiated successfully."
}
```

**Example cURL:**

```bash
curl -X POST "http://localhost:8000/api/jobs/507f1f77bcf86cd799439011/cancel"
```

> **Note:** Only jobs that have not finished can be cancelled. Attempts to cancel a job that has already finished return a `400` error. Cancelling a job that is already in the `cancelled` state returns a successful response indicating that the job is already cancelled.

---

### Delete Job

> **`DELETE`** `/api/jobs/{job_id}` - *Remove job and cleanup resources*

Deletes a job and all its associated resources from the database. Running jobs must be cancelled first.

**Success Response:**

```json
{
  "id": "507f1f77bcf86cd799439011",
  "message": "Job deletion started. Resources will be cleaned up in the background."
}
```

**Example cURL:**

```bash
curl -X DELETE "http://localhost:8000/api/jobs/507f1f77bcf86cd799439011"
```

## Job Status Values

| Status | Description |
|--------|-------------|
| `pending` | Job is queued and waiting to start |
| `running` | Job is actively executing workflow stages |
| `completed` | Job finished successfully |
| `cancelled` | Job was manually cancelled |
| `failed` | Job encountered an error and stopped |

## Data Split Configuration

The optional `data_split_config` allows you to control how logged data is processed for evaluation. Configuration varies by job type:

### LLM Job Data Split Configuration

By default, LLM jobs use the following values from `config/config.yaml`:

```json
{
  "eval_size": 100,
  "val_ratio": 0.1,
  "min_total_records": 50,
  "limit": 1000,
  "random_seed": null,
  "parse_function_arguments": true
}
```

**Parameters:**

- `eval_size` (int): Size of evaluation set (default: 100)
- `val_ratio` (float): Validation ratio (0.0-1.0, default: 0.1)
- `min_total_records` (int): Minimum total records required to proceed (default: 50)
- `limit` (int|null): Maximum records to pull from Elasticsearch (default: 1000 for LLM, null for embeddings)
- `random_seed` (int|null): Seed for reproducible splits (optional)
- `parse_function_arguments` (bool): Parse function arguments to JSON (default: true, LLM-only)

### Embedding Job Data Split Configuration

Embedding jobs include additional temporal filtering options:

```json
{
  "eval_size": 100,
  "val_ratio": 0.09,
  "min_total_records": 10,
  "random_seed": null,
  "limit": null,
  "from_date": "2021-01-01",
  "to_date": "2021-02-28",
  "min_negatives": 1,
  "max_negatives": 5
}
```

**Additional Embedding Parameters:**

- `from_date` (string|null): **Embedding-only** - Start date for temporal filtering (format: "YYYY-MM-DD"). Must be used with `to_date`
- `to_date` (string|null): **Embedding-only** - End date for temporal filtering (format: "YYYY-MM-DD"). Must be used with `from_date`
- `min_negatives` (int): **Embedding-only** - Minimum negative samples per triplet (default: 1)
- `max_negatives` (int): **Embedding-only** - Maximum negative samples per triplet (default: 5)
- `limit` (int|null): Maximum records to pull (default: **null** = pull all matching records)

> **Note:** Date filtering applies to the `timestamp` field in Elasticsearch records and filters data **before** splitting into train/val/eval sets.
> **📖 For detailed configuration:** Refer to [Embedding Data Split Configuration](02-configuration.md#embedding-data-split-configuration)

## Python Integration Example

```python
import requests
import time

class DataFlywheelClient:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = f"{base_url}/api"
    
    def create_job(
        self,
        workload_id,
        client_id,
        job_type="llm_job",
        config_name=None,
        data_split_config=None,
    ):
        """Create a new flywheel job."""
        payload = {
            "workload_id": workload_id,
            "client_id": client_id,
            "job_type": job_type,
        }
        if config_name is not None:
            payload["config_name"] = config_name
        if data_split_config:
            payload["data_split_config"] = data_split_config
            
        response = requests.post(f"{self.base_url}/jobs", json=payload)
        response.raise_for_status()
        return response.json()
    
    def get_job_status(self, job_id):
        """Get current job status."""
        response = requests.get(f"{self.base_url}/jobs/{job_id}")
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(self, job_id, timeout=3600):
        """Wait for job to complete with polling."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_job_status(job_id)
            job_status = status["status"]
            
            if job_status in ["completed", "failed", "cancelled"]:
                return status
                
            time.sleep(30)  # Poll every 30 seconds
        raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")

# Usage example
client = DataFlywheelClient()

# Create job with custom data split configuration
data_config = {
    "eval_size": 30,
    "val_ratio": 0.15,
    "min_total_records": 100,
    "limit": 500
}

job = client.create_job(
    workload_id="customer-service-v1",
    client_id="production-app",
    job_type="llm_job",
    config_name="my-llm-config",
    data_split_config=data_config
)
job_id = job['id']
print(f"Created job: {job_id}")

# Monitor progress  
result = client.wait_for_completion(job_id)
print(f"Job completed with status: {result['status']}")
```

## Error Handling

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (invalid parameters, business rule violations)
- `404` - Job not found
- `422` - Validation Error (invalid request body structure or data types)
- `500` - Internal server error

### Error Response Format

```json
{
  "detail": "Job not found"
}
```

For validation errors (422), the response includes detailed validation information:

```json
{
  "detail": [
    {
      "loc": ["body", "workload_id"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### Common Error Scenarios

**Job Not Found (404):**

```bash
# Invalid job ID
curl "http://localhost:8000/api/jobs/invalid-id"
```

**Validation Error (422):**

```bash
# Missing required field
curl -X POST "http://localhost:8000/api/jobs" \
  -H "Content-Type: application/json" \
  -d '{"workload_id": "test"}'  # Missing client_id
```

**Cannot Cancel Completed Job (400):**

```json
{
  "detail": "Cannot cancel a job that has already finished."
}
```

## Rate Limiting and Best Practices

1. **Polling Frequency**: When monitoring job status, poll every 30–60 seconds to avoid overwhelming the API
2. **Timeout Handling**: Flywheel jobs can take 1–3 hours depending on data size and model complexity
3. **Error Retry**: Implement exponential backoff for transient errors
4. **Resource Cleanup**: Always delete completed jobs when no longer needed to free up storage

## Integration Patterns

### Webhook Alternative

Since the API does not support webhooks, implement polling with exponential backoff:

```python
import time
import random

def poll_with_backoff(client, job_id, max_retries=10):
    """Poll job status with exponential backoff."""
    for attempt in range(max_retries):
        try:
            status = client.get_job_status(job_id)
            if status["status"] in ["completed", "failed", "cancelled"]:
                return status
        except requests.RequestException:
            # Exponential backoff with jitter
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(min(delay, 300))  # Cap at 5 minutes
    
    raise Exception("Max polling attempts exceeded")
```

### Batch Job Management

```python
def manage_multiple_jobs(client, job_configs):
    """Create and manage multiple flywheel jobs."""
    jobs = []
    
    # Create all jobs
    for config in job_configs:
        job = client.create_job(**config)
        jobs.append(job["id"])
    
    # Monitor all jobs
    completed = []
    while len(completed) < len(jobs):
        for job_id in jobs:
            if job_id not in completed:
                status = client.get_job_status(job_id)
                if status["status"] in ["completed", "failed", "cancelled"]:
                    completed.append(job_id)
                    print(f"Job {job_id} finished: {status['status']}")
        
        time.sleep(60)  # Check every minute
    
    return completed
```

---

## Retraining Trigger Endpoints

The trigger API enables automatic model retraining based on schedules, data thresholds, or performance degradation.

> **📖 For complete trigger documentation:** Refer to [Automatic Retraining Triggers](15-retraining-triggers.md)

### Create Trigger

> **`POST`** `/api/triggers` - *Create a new retraining trigger*

Creates a trigger that automatically runs evaluation and training jobs based on configured conditions.

<details>
<summary><strong>Request Details</strong></summary>

**Request Body:**

```json
{
  "name": "weekly-embedding-retrain",
  "description": "Weekly embedding model retraining",
  "workload_id": "my-rag-app",
  "client_id": "production",
  "job_type": "embedding_job",
  "trigger_type": "metric_degradation",
  "eval_schedule": "0 0 * * 0",
  "metric_thresholds": {"recall_5": 0.85, "map": 0.80},
  "config_name": "embedding_training_config",
  "model_source": "fine_tuned",
  "enabled": true
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `string` | Yes | Unique trigger name |
| `description` | `string` | No | Optional description |
| `workload_id` | `string` | Yes | Workload identifier for data filtering |
| `client_id` | `string` | Yes | Client identifier for data filtering |
| `job_type` | `string` | Yes | `embedding_job` or `llm_job` |
| `trigger_type` | `string` | Yes | `schedule`, `data_threshold`, or `metric_degradation` |
| `eval_schedule` | `string` | Yes | Cron expression (e.g., `0 0 * * *`) |
| `metric_thresholds` | `object` | Conditional | Required for `metric_degradation` triggers |
| `min_new_records` | `integer` | Conditional | Required for `data_threshold` triggers |
| `config_name` | `string` | Yes | Name of the configuration to use |
| `model_source` | `string` | No | `base` (default) or `fine_tuned` (embedding jobs only) |
| `reference_job_id` | `string` | No | Job ID from THIS trigger's history for specific model version (must be created by this trigger) |
| `enabled` | `boolean` | No | Enable trigger (default: true) |
| `enable_data_ingestion` | `boolean` | Conditional | Enable data ingestion before evaluation. **Required (must be `true`)** for `data_threshold` triggers, optional for others (default: true) |
| `data_window_months` | `int` or `"all"` | No | Time window: positive integer for months (≥1), or `"all"` for all data |
| `max_consecutive_failures` | `integer` | No | Auto-disable after N failures (default: 3) |

**Note on `last_execution_status`:** The `trigger.last_execution_status` field indicates the outcome of the most recent execution. Possible values:

- `success` - Execution completed, no retraining needed (metrics passed thresholds)
- `failure` - Execution failed due to an error
- `triggered_retraining` - Execution completed and triggered a retraining job

</details>

**Success Response:**

```json
{
  "id": "6971d1d90fc225d83080c27c",
  "name": "weekly-embedding-retrain",
  "trigger_type": "metric_degradation",
  "eval_schedule": "0 0 * * 0",
  "enabled": true,
  "created_at": "2026-01-22T07:29:29.916334"
}
```

**Example cURL:**

```bash
curl -X POST "http://localhost:8000/api/triggers" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weekly-embedding-retrain",
    "workload_id": "my-rag-app",
    "client_id": "production",
    "job_type": "embedding_job",
    "trigger_type": "schedule",
    "eval_schedule": "0 0 * * 0",
    "config_name": "embedding_training_config",
    "enabled": true
  }'
```

---

### List Triggers

> **`GET`** `/api/triggers` - *List all triggers with optional filtering and pagination*

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `workload_id` | `string` | Filter by workload identifier |
| `client_id` | `string` | Filter by client identifier |
| `job_type` | `string` | Filter by job type: `embedding_job` or `llm_job` |
| `trigger_type` | `string` | Filter by trigger type: `schedule`, `data_threshold`, or `metric_degradation` |
| `config_name` | `string` | Filter by configuration name |
| `model_source` | `string` | Filter by model source: `base` or `fine_tuned` |
| `last_execution_status` | `string` | Filter by last execution status: `success`, `failure`, or `triggered_retraining` |
| `enabled` | `boolean` | Filter by enabled status: `true` or `false` |
| `skip` | `integer` | Pagination offset (≥0, default: 0) |
| `limit` | `integer` | Pagination limit (1–1000, default: 100) |

**Success Response:**

```json
{
  "triggers": [
    {
      "id": "6971d1d90fc225d83080c27c",
      "name": "weekly-embedding-retrain",
      "description": "Weekly embedding model retraining",
      "workload_id": "my-rag-app",
      "client_id": "production",
      "job_type": "embedding_job",
      "trigger_type": "metric_degradation",
      "eval_schedule": "0 0 * * 0",
      "metric_thresholds": {"recall_5": 0.85, "map": 0.80},
      "config_name": "embedding_training_config",
      "model_source": "fine_tuned",
      "enabled": true,
      "last_execution_time": "2026-01-22T07:40:00.025000",
      "last_execution_status": "triggered_retraining",
      "consecutive_failures": 0
    }
  ],
  "total": 1
}
```

**Example cURL:**

```bash
# Get all triggers
curl "http://localhost:8000/api/triggers"

# Get all enabled metric degradation triggers
curl "http://localhost:8000/api/triggers?enabled=true&trigger_type=metric_degradation"

# Get all embedding job triggers
curl "http://localhost:8000/api/triggers?job_type=embedding_job"

# Get all failed triggers
curl "http://localhost:8000/api/triggers?last_execution_status=failure"

# Combine filters: embedding jobs that failed
curl "http://localhost:8000/api/triggers?job_type=embedding_job&last_execution_status=failure"

# Pagination: get second page
curl "http://localhost:8000/api/triggers?skip=10&limit=20"
```

---

### Get Trigger

> **`GET`** `/api/triggers/{trigger_id}` - *Get trigger details*

<details>
<summary><strong>Complete Response Structure</strong></summary>

```json
{
  "id": "6971d1d90fc225d83080c27c",
  "name": "weekly-embedding-retrain",
  "description": "Weekly embedding model retraining",
  "workload_id": "my-rag-app",
  "client_id": "production",
  "job_type": "embedding_job",
  "trigger_type": "metric_degradation",
  "eval_schedule": "0 0 * * 0",
  "metric_thresholds": {"recall_5": 0.85, "map": 0.80},
  "min_new_records": null,
  "enable_data_ingestion": true,
  "ingestion_timeout_minutes": 30,
  "ingestion_config": null,
  "dataset_type": null,
  "data_window_months": null,
  "config_name": "embedding_training_config",
  "reference_job_id": null,
  "model_source": "fine_tuned",
  "latest_fine_tuned_models": {
    "nvidia/llama-3.2-nv-embedqa-1b-v2": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122"
  },
  "enabled": true,
  "max_consecutive_failures": 3,
  "created_at": "2026-01-22T07:29:29.916334",
  "updated_at": "2026-01-22T07:40:00.025000",
  "last_execution_time": "2026-01-22T07:40:00.025000",
  "last_execution_status": "triggered_retraining",
  "consecutive_failures": 0
}
```

</details>

**Example cURL:**

```bash
curl "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c"
```

---

### Update Trigger

> **`PUT`** `/api/triggers/{trigger_id}` - *Update trigger configuration*

Update trigger configuration fields. This endpoint uses `PUT` with partial-update semantics: only fields included in the request body are modified. Omitted fields retain their current values.

**Updatable Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Unique trigger name (1-100 characters) |
| `description` | `string \| null` | Optional description (max 500 characters) |
| `eval_schedule` | `string` | Cron expression for evaluation runs |
| `metric_thresholds` | `object \| null` | Metric name → minimum acceptable value (for `metric_degradation` triggers) |
| `min_new_records` | `integer \| null` | Minimum new records required (for `data_threshold` triggers, ≥1) |
| `enable_data_ingestion` | `boolean \| null` | Whether to run data ingestion before evaluation |
| `ingestion_timeout_minutes` | `integer \| null` | Maximum time to wait for ingestion (≥1) |
| `ingestion_config` | `object \| null` | Optional ingestion plugin configuration (includes `dataset_type`) |
| `data_window_months` | `int \| "all" \| null` | Time window for training data (embedding jobs only) |
| `config_name` | `string \| null` | Name of the configuration to use |
| `reference_job_id` | `string \| null` | Specific job ID from this trigger's history |
| `model_source` | `string \| null` | `"base"` or `"fine_tuned"` (embedding jobs only) |
| `enabled` | `boolean \| null` | Whether trigger is active |
| `max_consecutive_failures` | `integer \| null` | Auto-disable after N consecutive failures (≥1) |

**Non-Updatable Fields:**

The following fields cannot be changed after trigger creation:

- `trigger_type` - Trigger type is immutable (fundamental behavior change)
- `workload_id` - Target workload identifier (immutable)
- `client_id` - Target client identifier (immutable)
- `job_type` - Job type (`embedding_job` or `llm_job`) is immutable
- `latest_fine_tuned_models` - Auto-updated by the system after training
- `created_at` - Read-only metadata
- `updated_at` - Auto-updated by the system
- `last_execution_time` - Auto-updated by executions
- `last_execution_status` - Auto-updated by executions
- `consecutive_failures` - Auto-updated by executions

**Request Body:**

```json
{
  "metric_thresholds": {"recall_5": 0.90, "map": 0.85},
  "description": "Updated thresholds for better performance",
  "enabled": true
}
```

**Validation:**

- The merged configuration (existing values + your updates) must pass validation for the trigger's existing `trigger_type`. For example, removing `metric_thresholds` from a `metric_degradation` trigger causes a validation error.
- If changing `model_source` to `"fine_tuned"`, the configuration must use `finetuning_type: "all_weights"`

**Success Response:**

Returns the updated trigger with the same structure as `GET /api/triggers/{trigger_id}`.

**Example cURL:**

```bash
# Update metric thresholds
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{
    "metric_thresholds": {"recall_5": 0.90, "map": 0.85},
    "description": "Updated thresholds"
  }'

# Disable trigger
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Update schedule
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{"eval_schedule": "0 0 * * 1"}'
```

---

### Delete Trigger

> **`DELETE`** `/api/triggers/{trigger_id}` - *Delete a trigger*

**Success Response:** `204 No Content`

**Example cURL:**

```bash
curl -X DELETE "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c"
```

---

### Enable/Disable Trigger

> **`POST`** `/api/triggers/{trigger_id}/enable` - *Enable a trigger*
> **`POST`** `/api/triggers/{trigger_id}/disable` - *Disable a trigger*

**Success Response:**

```json
{
  "id": "6971d1d90fc225d83080c27c",
  "name": "weekly-embedding-retrain",
  "enabled": true
}
```

**Example cURL:**

```bash
# Disable
curl -X POST "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c/disable"

# Enable
curl -X POST "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c/enable"
```

---

### Execute Trigger Manually

> **`POST`** `/api/triggers/{trigger_id}/execute` - *Immediately execute a trigger*

Executes the trigger without waiting for the scheduled time.

**Requirements:**

- Trigger must be enabled (`enabled: true`)
- No other execution must be running or pending for this trigger

**Success Response:**

```json
{
  "message": "Trigger weekly-embedding-retrain execution started",
  "execution_id": "20783311-bb84-4287-a88f-9ce978356345",
  "status": "pending"
}
```

**Error Responses:**

*400 Bad Request - Trigger is disabled:*

```json
{
  "detail": "Trigger 'my-trigger' is disabled. Enable it first via POST /api/triggers/{id}/enable"
}
```

*404 Not Found - Trigger does not exist:*

```json
{
  "detail": "Trigger not found: {trigger_id}"
}
```

*409 Conflict - Another execution is already running:*

```json
{
  "detail": "Trigger already has a running execution: 6971d4501caaa3693fad2341"
}
```

**Example cURL:**

```bash
curl -X POST "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c/execute"
```

---

### Get Trigger Executions

> **`GET`** `/api/triggers/{trigger_id}/executions` - *Get execution history for a trigger*

**Query Parameters:**

| Parameter | Description |
|-----------|-------------|
| `skip` | Pagination offset |
| `limit` | Pagination limit (default: 100) |

<details>
<summary><strong>Complete Response Structure</strong></summary>

```json
{
  "executions": [
    {
      "id": "6971d4501caaa3693fad2341",
      "trigger_id": "6971d2ad0fc225d83080c286",
      "trigger_name": "weekly-embedding-retrain",
      "trigger_type": "metric_degradation",
      "execution_time": "2026-01-22T07:40:00.025000",
      "completed_at": "2026-01-22T07:40:00.027000",
      "new_records_ingested": 1200,
      "ingestion_duration_seconds": 0.000292,
      "eval_job_id": "6971d4501caaa3693fad2342",
      "eval_job_status": null,
      "training_job_id": "6971d4501caaa3693fad2343",
      "training_job_status": null,
      "nim_results": [
        {
          "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "metrics": {"recall_5": 0.82, "map": 0.77, "ndcg_cut_5": 0.79},
          "thresholds_checked": {
            "recall_5": {"threshold": 0.85, "actual": 0.82, "passed": false},
            "map": {"threshold": 0.80, "actual": 0.77, "passed": false}
          },
          "passed_thresholds": false,
          "needs_retraining": true,
          "training_triggered": true,
          "customization_id": "6971d4501caaa3693fad2343",
          "fine_tuned_model": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122",
          "post_training_metrics": {"recall_5": 0.91, "map": 0.86, "ndcg_cut_5": 0.88},
          "incomplete_evaluation": false,
          "eval_error": null,
          "customization_error": null,
          "post_eval_error": null,
          "skip_reason": null
        }
      ],
      "total_nims": 1,
      "nims_passed": 0,
      "nims_failed": 1,
      "nims_retrained": 1,
      "all_thresholds_met": false,
      "triggered_training": true,
      "decision_reason": "Metrics below threshold - triggered retraining",
      "skip_reason": null,
      "data_window_used": null,
      "status": "completed",
      "error": null,
      "eval_duration_seconds": null,
      "total_duration_seconds": 0.002339
    }
  ],
  "total": 1
}
```

</details>

**Example cURL:**

```bash
curl "http://localhost:8000/api/triggers/6971d2ad0fc225d83080c286/executions?limit=10"
```

#### NIM Result Error Fields

Each entry in `nim_results` includes error tracking fields to capture failures at different stages:

| Field | Type | Description |
|-------|------|-------------|
| `eval_error` | `string \| null` | Error message if pre-training evaluation failed (e.g., "Dataset not found: base-dataset for workload 'my-app'", "Evaluation timeout after 300s") |
| `customization_error` | `string \| null` | Error message if training/customization failed (e.g., "GPU out of memory during training epoch 1", "Training diverged: loss became NaN") |
| `post_eval_error` | `string \| null` | Error message if post-training evaluation failed (e.g., "Model deployment failed: insufficient resources", "Evaluation dataset corrupted") |

**Behavior:**

- These fields are set to `null` when the corresponding stage succeeds
- When an error occurs, the field contains the specific error message from the underlying evaluation or customization job
- Errors are extracted from the `evaluations` and `customizations` collections in the database
- If evaluation fails, `metrics` will be empty (`{}`) and `passed_thresholds` will be `false`
- If training fails, `fine_tuned_model` will be `null` and `post_training_metrics` will be empty (`{}`)

**Example with Evaluation Error:**

```json
{
  "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "metrics": {},
  "thresholds_checked": {},
  "passed_thresholds": false,
  "incomplete_evaluation": true,
  "eval_error": "Dataset not found: base-dataset for workload 'my-app'",
  "customization_error": null,
  "post_eval_error": null
}
```

**Example with Training Error:**

```json
{
  "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "metrics": {"recall_5": 0.82, "map": 0.77},
  "passed_thresholds": false,
  "training_triggered": true,
  "fine_tuned_model": null,
  "post_training_metrics": {},
  "incomplete_evaluation": false,
  "eval_error": null,
  "customization_error": "GPU out of memory during training epoch 1",
  "post_eval_error": null
}
```

#### Complete NIM Result Field Reference

Each `nim_results` entry contains the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `model_name` | `string` | Base model name (NIM identifier) used for grouping/matching results across executions |
| `model_used` | `string \| null` | **Model that was actually used for evaluation/training** (base model or previous fine-tuned model). For iterative fine-tuning with `model_source: "fine_tuned"`, this will differ from `model_name` and show the fine-tuned model that was used as input. When `model_source: "base"`, `model_used` equals `model_name` |
| `metrics` | `object` | Pre-training evaluation metrics (e.g., `{"recall_5": 0.82, "map": 0.77}`) |
| `thresholds_checked` | `object` | Per-metric threshold comparison results |
| `passed_thresholds` | `boolean` | `true` if all configured thresholds passed |
| `needs_retraining` | `boolean` | `true` if this NIM failed thresholds and needs retraining |
| `training_triggered` | `boolean` | `true` if training was actually triggered for this NIM |
| `customization_id` | `string \| null` | Customization job ID if training was triggered |
| `fine_tuned_model` | `string \| null` | Name/URI of the fine-tuned model produced (e.g., `"dfwbp/2v2@cust-abc123"`) |
| `post_training_metrics` | `object` | Post-training evaluation metrics from customized-eval |
| `incomplete_evaluation` | `boolean` | `true` if evaluation is incomplete (e.g., configured metrics missing from results) |
| `eval_error` | `string \| null` | **Error message if pre-training evaluation failed** |
| `customization_error` | `string \| null` | **Error message if training/customization failed** |
| `post_eval_error` | `string \| null` | **Error message if post-training evaluation failed** |
| `skip_reason` | `string \| null` | Reason if this NIM's processing was skipped |

---

### Get All Executions

> **`GET`** `/api/triggers/executions/all` - *Get executions across all triggers*

**Query Parameters:**

| Parameter | Description |
|-----------|-------------|
| `skip` | Pagination offset |
| `limit` | Pagination limit (default: 100) |

**Success Response:**

```json
{
  "executions": [...],
  "total": 15
}
```

**Example cURL:**

```bash
curl "http://localhost:8000/api/triggers/executions/all?limit=20"
```

---

### Get Single Execution

> **`GET`** `/api/triggers/executions/{execution_id}` - *Get a specific execution by ID*

Returns the full details of a single trigger execution, including complete `nim_results` with error tracking.

**Path Parameters:**

| Parameter | Description |
|-----------|-------------|
| `execution_id` | MongoDB ObjectId of the execution |

<details>
<summary><strong>Complete Response Structure</strong></summary>

```json
{
  "id": "6971d4501caaa3693fad2341",
  "trigger_id": "6971d2ad0fc225d83080c286",
  "trigger_name": "weekly-embedding-retrain",
  "trigger_type": "metric_degradation",
  "execution_time": "2026-01-22T07:40:00.025000",
  "completed_at": "2026-01-22T07:42:15.341000",
  "new_records_ingested": 1200,
  "ingestion_duration_seconds": 0.000292,
  "eval_job_id": "6971d4501caaa3693fad2342",
  "eval_job_status": null,
  "training_job_id": "6971d4a01caaa3693fad2350",
  "training_job_status": null,
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "metrics": {"recall_5": 0.82, "map": 0.77, "ndcg_cut_5": 0.79},
      "thresholds_checked": {
        "recall_5": {"threshold": 0.85, "actual": 0.82, "passed": false},
        "map": {"threshold": 0.80, "actual": 0.77, "passed": false}
      },
      "passed_thresholds": false,
      "needs_retraining": true,
      "training_triggered": true,
      "customization_id": "cust-abc123def456",
      "fine_tuned_model": "dfwbp/2v2@cust-abc123def456",
      "post_training_metrics": {"recall_5": 0.91, "map": 0.86, "ndcg_cut_5": 0.88},
      "incomplete_evaluation": false,
      "eval_error": null,
      "customization_error": null,
      "post_eval_error": null,
      "skip_reason": null
    }
  ],
  "total_nims": 1,
  "nims_passed": 0,
  "nims_failed": 1,
  "nims_retrained": 1,
  "all_thresholds_met": false,
  "triggered_training": true,
  "decision_reason": "NIMs below threshold: nvidia/llama-3.2-nv-embedqa-1b-v2",
  "skip_reason": null,
  "data_window_used": null,
  "status": "completed",
  "error": null,
  "eval_duration_seconds": 125.5,
  "total_duration_seconds": 135.316
}
```

</details>

**Error Responses:**

*400 Bad Request - Invalid execution ID format:*

```json
{
  "detail": "Invalid execution ID format: not-a-valid-id"
}
```

*404 Not Found - Execution does not exist:*

```json
{
  "detail": "Execution not found"
}
```

**Example cURL:**

```bash
curl "http://localhost:8000/api/triggers/executions/6971d4501caaa3693fad2341"
```

**Use Cases:**

- Retrieve complete execution details after a manual trigger execution
- Inspect `nim_results` with full error tracking information
- Debug failed executions by examining error messages at each stage
- Track metrics and model performance over time

---

## Trigger Status Values

These values correspond to the `trigger.last_execution_status` field.

| Status | Description |
|--------|-------------|
| `success` | Evaluation passed, no retraining needed |
| `triggered_retraining` | Metrics failed thresholds, training was triggered |
| `failure` | Execution failed with an error |

## Trigger Types Summary

| Type | Condition | Training Triggered |
|------|-----------|-------------------|
| `schedule` | Always on schedule | Always |
| `data_threshold` | When `min_new_records` reached | Always (if threshold met) |
| `metric_degradation` | On schedule | Only if metrics below thresholds |

---

This API reference provides the foundation for integrating any application with the Data Flywheel Blueprint. For advanced workflow configuration, refer to the [Configuration Guide](./02-configuration.md).
