# Automatic Retraining Triggers

The Data Flywheel Blueprint includes an automatic retraining system that monitors your models and triggers evaluation and fine-tuning based on configurable conditions. This enables a true "flywheel" where your models continuously improve without manual intervention.

## Overview

Retraining triggers automate the model improvement cycle:

```text
┌─────────────────────────────────────────────────────────────────┐
│                    Automatic Retraining Flow                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Schedule          New Data           Metric                   │
│   Trigger ────┐    Threshold ────┐    Degradation ────┐         │
│               │    Trigger       │    Trigger         │         │
│               ▼                  ▼                    ▼         │
│           ┌──────────────────────────────────────────────┐      │
│           │             Celery Beat Scheduler            │      │
│           └──────────────────────────────────────────────┘      │
│                              │                                  │
│                              ▼                                  │
│           ┌──────────────────────────────────────────────┐      │
│           │         Data Ingestion (if enabled)          │      │
│           └──────────────────────────────────────────────┘      │
│                              │                                  │
│                              ▼                                  │
│           ┌──────────────────────────────────────────────┐      │
│           │             Model Resolution                 │      │
│           │    (base model or latest fine-tuned model)   │      │
│           └──────────────────────────────────────────────┘      │
│                              │                                  │
│                              ▼                                  │
│           ┌──────────────────────────────────────────────┐      │
│           │          Evaluation & Training Job           │      │
│           └──────────────────────────────────────────────┘      │
│                              │                                  │
│                              ▼                                  │
│           ┌──────────────────────────────────────────────┐      │
│           │      Store Results & Update Trigger State    │      │
│           └──────────────────────────────────────────────┘      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Trigger Types

### 1. Schedule Trigger

Runs evaluation and training unconditionally on a cron schedule.

**Use case:** Regular model updates (weekly, monthly) regardless of performance.

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `trigger_type` | string | ✅ | - | Must be `"schedule"` |
| `eval_schedule` | string | ✅ | - | Cron expression (e.g., `"0 0 * * 0"` for weekly) |
| `name` | string | ✅ | - | Unique trigger name (1-100 characters) |
| `description` | string | | `null` | Optional description (max 500 characters) |
| `workload_id` | string | ✅ | - | Workload identifier for data filtering |
| `client_id` | string | ✅ | - | Client identifier for data filtering |
| `job_type` | string | ✅ | - | `"embedding_job"` or `"llm_job"` |
| `config_name` | string | ✅ | - | Name of the configuration to use |
| `model_source` | string | | `"base"` | `"base"` or `"fine_tuned"` (embedding jobs only) |
| `enable_data_ingestion` | boolean | | `true` | Whether to run data ingestion before evaluation |
| `ingestion_timeout_minutes` | integer | | `30` | Maximum time to wait for ingestion (≥1) |
| `ingestion_config` | object | | `null` | Optional ingestion plugin configuration (includes `dataset_type`) |
| `data_window_months` | int or "all" | | `null` | Time window for training data (embedding jobs only) |
| `reference_job_id` | string | | `null` | Specific job ID from this trigger's history (for `model_source: "fine_tuned"`) |
| `enabled` | boolean | | `true` | Whether trigger is active |
| `max_consecutive_failures` | integer | | `3` | Auto-disable after N consecutive failures (≥1) |

**Example:**

```json
{
  "trigger_type": "schedule",
  "eval_schedule": "0 0 * * 0",
  "config_name": "embedding_training_config",
  "enable_data_ingestion": true,
  "ingestion_config": {
    "plugin_name": "bugnemo",
    "dataset_type": "bug_pairs",
    "batch_size": 1000,
    "config": {
      "server": "env:NVBUGS_DBSERVER",
      "database": "env:NVBUGS_DBNAME",
      "username": "env:NVBUGS_DBUSER",
      "password": "env:NVBUGS_DBPWD",
      "max_pairs": 5000
    }
  }
}
```

The `eval_schedule` uses cron syntax (`0 0 * * 0` = every Sunday at midnight). When `enable_data_ingestion` is true, `ingestion_config` must follow the [Ingestion Configuration](#ingestion-configuration) schema.

### 2. Data Threshold Trigger

Triggers when a minimum number of new records are available for training. The `data_threshold` trigger type requires the `enable_data_ingestion` flag to be explicitly set to `true` and this is validated during trigger creation.

**Use case:** Train only when you have enough new data to make a meaningful improvement.

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `trigger_type` | string | ✅ | - | Must be `"data_threshold"` |
| `eval_schedule` | string | ✅ | - | Cron expression for checking data availability |
| `min_new_records` | integer | ✅ | - | Minimum new records required (≥1) |
| `enable_data_ingestion` | boolean | ✅ | - | **Must be `true`** for data_threshold triggers |
| `name` | string | ✅ | - | Unique trigger name (1-100 characters) |
| `description` | string | | `null` | Optional description (max 500 characters) |
| `workload_id` | string | ✅ | - | Workload identifier for data filtering |
| `client_id` | string | ✅ | - | Client identifier for data filtering |
| `job_type` | string | ✅ | - | `"embedding_job"` or `"llm_job"` |
| `config_name` | string | ✅ | - | Name of the configuration to use |
| `model_source` | string | | `"base"` | `"base"` or `"fine_tuned"` (embedding jobs only) |
| `ingestion_timeout_minutes` | integer | | `30` | Maximum time to wait for ingestion (≥1) |
| `ingestion_config` | object | | `null` | Optional ingestion plugin configuration (includes `dataset_type`) |
| `data_window_months` | int or "all" | | `null` | Time window for training data (embedding jobs only) |
| `reference_job_id` | string | | `null` | Specific job ID from this trigger's history |
| `enabled` | boolean | | `true` | Whether trigger is active |
| `max_consecutive_failures` | integer | | `3` | Auto-disable after N consecutive failures (≥1) |

**Example:**

```json
{
  "trigger_type": "data_threshold",
  "eval_schedule": "0 */6 * * *",
  "min_new_records": 1000,
  "enable_data_ingestion": true,
  "config_name": "embedding_training_config",
  "ingestion_config": {
    "plugin_name": "bugnemo",
    "dataset_type": "bug_pairs",
    "batch_size": 1000,
    "config": {
      "server": "env:NVBUGS_DBSERVER",
      "database": "env:NVBUGS_DBNAME",
      "username": "env:NVBUGS_DBUSER",
      "password": "env:NVBUGS_DBPWD",
      "max_pairs": 5000
    }
  }
}
```

The `eval_schedule` of `0 */6 * * *` checks every 6 hours. For `ingestion_config` schema, see [Ingestion Configuration](#ingestion-configuration). The `enable_data_ingestion` field must be explicitly set to `true` for `data_threshold` triggers.

### 3. Metric Degradation Trigger

Evaluates model performance and only triggers training if metrics fall below thresholds.

**Use case:** Maintain model quality by retraining only when performance drops.

**Behavior for Multi-NIM Jobs:**

- Each NIM is evaluated independently against the thresholds
- Only NIMs that fail thresholds are retrained (efficient resource usage)
- NIMs that pass thresholds continue using their current models

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `trigger_type` | string | ✅ | - | Must be `"metric_degradation"` |
| `eval_schedule` | string | ✅ | - | Cron expression for evaluation runs |
| `metric_thresholds` | object | ✅ | - | Metric name → minimum acceptable value (non-empty, no NaN/Inf) |
| `name` | string | ✅ | - | Unique trigger name (1-100 characters) |
| `description` | string | | `null` | Optional description (max 500 characters) |
| `workload_id` | string | ✅ | - | Workload identifier for data filtering |
| `client_id` | string | ✅ | - | Client identifier for data filtering |
| `job_type` | string | ✅ | - | `"embedding_job"` or `"llm_job"` |
| `config_name` | string | ✅ | - | Name of the configuration to use |
| `model_source` | string | | `"base"` | `"base"` or `"fine_tuned"` (embedding jobs only) |
| `enable_data_ingestion` | boolean | | `true` | Whether to run data ingestion before evaluation |
| `ingestion_timeout_minutes` | integer | | `30` | Maximum time to wait for ingestion (≥1) |
| `ingestion_config` | object | | `null` | Optional ingestion plugin configuration (includes `dataset_type`) |
| `data_window_months` | int or "all" | | `null` | Time window for training data (embedding jobs only) |
| `reference_job_id` | string | | `null` | Specific job ID from this trigger's history |
| `enabled` | boolean | | `true` | Whether trigger is active |
| `max_consecutive_failures` | integer | | `3` | Auto-disable after N consecutive failures (≥1) |

**Example:**

```json
{
  "trigger_type": "metric_degradation",
  "eval_schedule": "0 0 * * *",
  "metric_thresholds": {
    "recall_5": 0.85,
    "map": 0.80
  },
  "config_name": "embedding_training_config",
  "enable_data_ingestion": true,
  "ingestion_config": {
    "plugin_name": "bugnemo",
    "dataset_type": "bug_pairs",
    "batch_size": 1000,
    "config": {
      "server": "env:NVBUGS_DBSERVER",
      "database": "env:NVBUGS_DBNAME",
      "username": "env:NVBUGS_DBUSER",
      "password": "env:NVBUGS_DBPWD",
      "max_pairs": 5000
    }
  }
}
```

The `eval_schedule` of `0 0 * * *` runs daily at midnight. For `ingestion_config` schema, see [Ingestion Configuration](#ingestion-configuration).

## Quick Start: Complete Example with BugNemo Ingestion

This example demonstrates creating a metric degradation trigger with BugNemo data ingestion for automatic embedding model retraining.

### Step 1: Create the Configuration

First, create the embedding training configuration using the example config:

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d @config/embedding_config_example.json
```

> **Note:** For iterative fine-tuning with `model_source: "fine_tuned"`, you must use `finetuning_type: "all_weights"` instead of `"lora_merged"`.

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "embedding_training_config",
    "description": "Example embedding model training configuration for the Data Flywheel Blueprint",
    "config_type": "embedding_job",
    "config_data": {
      "nims": [
        {
          "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "model_type": "embedding",
          "context_length": 32768,
          "gpus": 1,
          "pvc_size": "25Gi",
          "tag": "1.9.0",
          "customization_enabled": true,
          "customizer_configs": {
            "target": "nvidia/llama-3.2-nv-embedqa-1b@v2",
            "gpus": 1,
            "max_seq_length": 8192
          }
        }
      ],
      "data_split_config": {
        "val_ratio": 0.09,
        "eval_size": 100,
        "min_total_records": 50,
        "random_seed": 42
      },
      "training_config": {
        "training_type": "sft",
        "finetuning_type": "all_weights",
        "epochs": 1,
        "batch_size": 64,
        "learning_rate": 0.000025
      },
      "evaluation_config": {
        "embedding_eval_metrics": {
          "metrics": ["recall_5", "map", "P_5", "P_10"]
        }
      }
    }
  }'
```

> **Note:** This configuration uses `finetuning_type: "all_weights"` which supports iterative fine-tuning with `model_source: "fine_tuned"`. LoRA-based fine-tuning (`"lora"` or `"lora_merged"`) cannot be used for iterative training.

### Step 2: Create the Trigger with BugNemo Ingestion

Create a metric degradation trigger that automatically ingests data from BugNemo before evaluation:

```bash
curl -X POST http://localhost:8000/api/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weekly-embedding-retrain-bugnemo",
    "description": "Weekly embedding model retraining with BugNemo data ingestion",
    "workload_id": "my-rag-app",
    "client_id": "production",
    "job_type": "embedding_job",
    "trigger_type": "metric_degradation",
    "eval_schedule": "0 0 * * 0",
    "metric_thresholds": {
      "recall_5": 0.85,
      "map": 0.80
    },
    "config_name": "embedding_training_config",
    "model_source": "fine_tuned",
    "enable_data_ingestion": true,
    "ingestion_timeout_minutes": 60,
    "ingestion_config": {
      "plugin_name": "bugnemo",
      "dataset_type": "bug_pairs",
      "config": {
        "max_pairs": 5000,
        "chunk_size": 500
      },
      "batch_size": 1000,
      "sync_interval_minutes": 60
    },
    "data_window_months": 3,
    "enabled": true,
    "max_consecutive_failures": 3
  }'
```

#### Ingestion Configuration

The `ingestion_config` field configures automatic data ingestion before trigger execution. For full details on plugins, deployment, and the BugNemo plugin, see [Data Ingestion Plugins](ingestion-plugin/index.md).

| Field | Type | Description |
|-------|------|-------------|
| `plugin_name` | string | Plugin identifier: `"bugnemo"` for BugNemo database ingestion |
| `dataset_type` | string | Dataset format: `"bug_pairs"` for embedding jobs (or `"triplet"`, `"bug_pairs_separated"`) |
| `config` | object | Plugin-specific parameters (see below) |
| `batch_size` | integer | Number of records to process per batch (1-10000, default: 1000) |
| `sync_interval_minutes` | integer | Sync interval in minutes (≥1, default: 60) |
| `default_start_date` | datetime | Default start date for first-time ingestion (ISO format) |

The same schema is used for **triggers**, **job configs** (embedding/LLM), and **manual job API** requests. In code, all use the shared `IngestionConfig` model from `src.lib.ingestion`. In job configs, **when `ingestion_config` is present, ingestion is enabled** before the job runs; there is no separate `enabled` field.

**BugNemo Plugin Configuration (`config` field):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `server` | string | * | Database server hostname (or use `NVBUGS_DBSERVER` env var) |
| `database` | string | * | Database name (or use `NVBUGS_DBNAME` env var) |
| `username` | string | * | Database username (or use `NVBUGS_DBUSER` env var) |
| `password` | string | * | Database password (or use `NVBUGS_DBPWD` env var) |
| `max_pairs` | integer | | Maximum bug pairs to fetch (default: 1000, ≥1) |
| `chunk_size` | integer | | DB query batch size (default: 500, 100-2000) |
| `from_date` | datetime | | Start date override (ISO format, overrides watermark) |
| `to_date` | datetime | | End date override (ISO format) |
| `default_start_date` | datetime | | Default start date if no watermark exists (ISO format) |

**High-Level Ingestion Flow:**

1. **Before Evaluation:** Trigger runs data ingestion using the configured plugin
2. **Incremental Sync:** Uses watermark tracking to fetch only new records since last ingestion
3. **Data Transformation:** BugNemo plugin transforms database records into `bug_pairs` format
4. **Storage:** Ingested data is stored and filtered by `workload_id` and `client_id`
5. **Training Data:** The ingested data is used for model evaluation and training

**Environment Variables Alternative:**

Instead of providing credentials in the config, you can set environment variables in your deployment:

```bash
export NVBUGS_DBSERVER=your_server.nvidia.com
export NVBUGS_DBNAME=NVBugs
export NVBUGS_DBUSER=your_username
export NVBUGS_DBPWD=your_password
```

If environment variables are set, you can omit `server`, `database`, `username`, and `password` from the `config` object.

### Step 3: Verify the Trigger

```bash
# List all triggers
curl http://localhost:8000/api/triggers

# Get trigger details
curl http://localhost:8000/api/triggers/{trigger_id}

# Check trigger executions (includes ingestion stats)
curl http://localhost:8000/api/triggers/{trigger_id}/executions

# Manually execute the trigger for testing
curl -X POST http://localhost:8000/api/triggers/{trigger_id}/execute
```

## API Reference

### Create Trigger

```text
POST /api/triggers
```

Create a new retraining trigger with automatic evaluation and training based on configured conditions.

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | ✅ | Unique trigger name (1-100 characters) |
| `description` | `string` | | Optional description (max 500 characters) |
| `workload_id` | `string` | ✅ | Workload identifier for data filtering |
| `client_id` | `string` | ✅ | Client identifier for data filtering |
| `job_type` | `string` | ✅ | `embedding_job` or `llm_job` |
| `trigger_type` | `string` | ✅ | `schedule`, `data_threshold`, or `metric_degradation` |
| `eval_schedule` | `string` | ✅ | Cron expression (for example, `0 0 * * *` for daily) |
| `metric_thresholds` | `object` | * | Required for `metric_degradation` triggers. Metric name → minimum value (non-empty, no NaN/Inf values) |
| `min_new_records` | `integer` | * | Required for `data_threshold` triggers (≥1) |
| `config_name` | `string` | ✅ | Name of the configuration to use. **Note:** When `model_source="fine_tuned"`, the configuration must have `finetuning_type: "all_weights"` |
| `model_source` | `string` | | `base` (default) or `fine_tuned` (embedding jobs only, requires `all_weights` config) |
| `reference_job_id` | `string` | | **Optional**: Specific job ID from THIS trigger's execution history. Used with `model_source: "fine_tuned"` to pin to a specific training run's models. If not provided, uses the latest fine-tuned models. **Must be from this trigger's history.** |
| `enabled` | `boolean` | | Enable trigger (default: `true`) |
| `enable_data_ingestion` | `boolean` | * | Enable data ingestion before evaluation. **Required (must be `true`)** for `data_threshold` triggers, optional for others (default: `true`) |
| `ingestion_timeout_minutes` | `integer` | | Maximum time to wait for ingestion to complete (≥1, default: 30) |
| `ingestion_config` | `object` | | Ingestion plugin configuration (includes `dataset_type`). See [Ingestion Configuration](#ingestion-configuration) for details |
| `data_window_months` | `int \| "all"` | | Time window for training data. Integer (≥1) for months to look back, or `"all"` for all data (embedding jobs only) |
| `max_consecutive_failures` | `integer` | | Auto-disable after N consecutive failures (≥1, default: 3) |

**Type-Specific Requirements:**

- **`metric_degradation`**: Must include `metric_thresholds` (non-empty object)
- **`data_threshold`**: Must include `min_new_records` (≥1) and `enable_data_ingestion: true`
- **`schedule`**: No additional required fields

**Validation:**

- Configuration must exist and be valid
- If `model_source: "fine_tuned"`, configuration must use `finetuning_type: "all_weights"`
- `name` must be unique across all triggers
- Cron expression must be valid
- `metric_thresholds` values cannot be NaN or Inf

**Response:**

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
  "config_name": "embedding_training_config",
  "model_source": "fine_tuned",
  "latest_fine_tuned_models": {},
  "enabled": true,
  "max_consecutive_failures": 3,
  "created_at": "2026-01-22T07:29:29.916334",
  "updated_at": "2026-01-22T07:29:29.916334",
  "last_execution_time": null,
  "last_execution_status": null,
  "consecutive_failures": 0
}
```

**Example:**

```bash
curl -X POST "http://localhost:8000/api/triggers" \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

### List Triggers

```text
GET /api/triggers
```

List all triggers with optional filtering and pagination.

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

**Response:**

```json
{
  "triggers": [
    {
      "id": "6971d1d90fc225d83080c27c",
      "name": "weekly-embedding-retrain",
      "trigger_type": "metric_degradation",
      "eval_schedule": "0 0 * * 0",
      "config_name": "embedding_training_config",
      "enabled": true,
      "last_execution_time": "2026-01-22T07:40:00.025000",
      "last_execution_status": "triggered_retraining"
    }
  ],
  "total": 1
}
```

Each trigger in the response contains all fields from the full trigger response. The example above shows a subset for brevity.

**Examples:**

```bash
# Get all triggers
curl "http://localhost:8000/api/triggers"

# Get all embedding job triggers
curl "http://localhost:8000/api/triggers?job_type=embedding_job"

# Get all failed triggers
curl "http://localhost:8000/api/triggers?last_execution_status=failure"

# Get all triggers using fine-tuned models
curl "http://localhost:8000/api/triggers?model_source=fine_tuned"

# Get all disabled triggers
curl "http://localhost:8000/api/triggers?enabled=false"

# Combine filters: embedding jobs that failed
curl "http://localhost:8000/api/triggers?job_type=embedding_job&last_execution_status=failure"

# Pagination: get second page (skip first 10, limit to 20)
curl "http://localhost:8000/api/triggers?skip=10&limit=20"
```

### Get Trigger

```text
GET /api/triggers/{trigger_id}
```

Get complete details for a specific trigger by ID.

**Response:**

Returns the full trigger configuration including all fields. See [API Reference](06-api-reference.md#get-trigger) for complete response structure.

**Example:**

```bash
curl "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c"
```

### Update Trigger

```text
PUT /api/triggers/{trigger_id}
```

Update trigger configuration fields. Supports partial updates - only provided fields are updated.

**Updatable Fields:**

All configuration fields except identity and auto-managed fields can be updated:

| Field | Description |
|-------|-------------|
| `name` | Unique trigger name |
| `description` | Optional description |
| `eval_schedule` | Cron expression for evaluation runs |
| `metric_thresholds` | Metric thresholds (for `metric_degradation` triggers) |
| `min_new_records` | Minimum new records (for `data_threshold` triggers) |
| `enable_data_ingestion` | Enable data ingestion before evaluation |
| `ingestion_timeout_minutes` | Ingestion timeout in minutes |
| `ingestion_config` | Ingestion plugin configuration (includes `dataset_type`) |
| `data_window_months` | Time window for training data |
| `config_name` | Configuration name to use |
| `reference_job_id` | Specific job ID from this trigger's history |
| `model_source` | `"base"` or `"fine_tuned"` |
| `enabled` | Enable/disable trigger |
| `max_consecutive_failures` | Auto-disable threshold |

**Non-Updatable Fields:**

These fields cannot be changed after creation:

- `trigger_type` - Immutable (fundamental behavior)
- `workload_id` - Immutable (target workload)
- `client_id` - Immutable (target client)
- `job_type` - Immutable (pipeline type)
- `latest_fine_tuned_models` - Auto-updated by system
- `created_at` - Read-only metadata
- `updated_at` - Auto-updated by system
- `last_execution_time` - Auto-updated by executions
- `last_execution_status` - Auto-updated by executions
- `consecutive_failures` - Auto-updated by executions

**Request Body:**

Only include fields you want to update. All fields are optional:

```json
{
  "metric_thresholds": {"recall_5": 0.90, "map": 0.85},
  "description": "Updated thresholds",
  "enabled": true
}
```

**Validation:**

- The merged configuration (existing + updates) must be valid
- If changing `model_source` to `"fine_tuned"`, the configuration must use `finetuning_type: "all_weights"`
- Type-specific fields (e.g., `metric_thresholds`) must match the current `trigger_type`

**Examples:**

```bash
# Update metric thresholds
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{
    "metric_thresholds": {"recall_5": 0.90, "map": 0.85}
  }'

# Disable trigger
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Update schedule and description
curl -X PUT "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c" \
  -H "Content-Type: application/json" \
  -d '{
    "eval_schedule": "0 0 * * 1",
    "description": "Weekly retraining on Mondays"
  }'
```

**Response:**

Returns the updated trigger with the same structure as `GET /api/triggers/{trigger_id}`.

### Delete Trigger

```text
DELETE /api/triggers/{trigger_id}
```

Returns `204 No Content` on success. Returns `404` if the trigger is not found.

**Example:**

```bash
curl -X DELETE "http://localhost:8000/api/triggers/6971d1d90fc225d83080c27c"
```

### Enable/Disable Trigger

```text
POST /api/triggers/{trigger_id}/enable
POST /api/triggers/{trigger_id}/disable
```

### Execute Trigger Manually

```text
POST /api/triggers/{trigger_id}/execute
```

Immediately executes the trigger without waiting for the schedule.

**Requirements:**

- Trigger must be enabled (`enabled: true`)
- No other execution must be running or pending for this trigger

**Response:**

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

*409 Conflict - Another execution is already running:*

```json
{
  "detail": "Trigger already has a running execution: 6971d4501caaa3693fad2341"
}
```

### Get Trigger Executions

```text
GET /api/triggers/{trigger_id}/executions
```

Retrieve execution history for a specific trigger with pagination support.

**Query Parameters:**

- `skip`: Pagination offset (default: 0)
- `limit`: Maximum results to return (default: 100)

**Response:**

```json
{
  "executions": [
    {
      "id": "6971d4501caaa3693fad2341",
      "trigger_id": "6971d2ad0fc225d83080c286",
      "trigger_name": "weekly-embedding-retrain",
      "trigger_type": "metric_degradation",
      "execution_time": "2026-01-22T07:40:00.025000",
      "status": "completed",
      "triggered_training": true,
      "nim_results": [
        {
          "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "metrics": {
            "recall_5": 0.82,
            "map": 0.77
          },
          "thresholds_checked": {
            "recall_5": {"threshold": 0.85, "actual": 0.82, "passed": false},
            "map": {"threshold": 0.80, "actual": 0.77, "passed": false}
          },
          "passed_thresholds": false,
          "training_triggered": true,
          "fine_tuned_model": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122",
          "post_training_metrics": {
            "recall_5": 0.91,
            "map": 0.86
          },
          "incomplete_evaluation": false,
          "eval_error": null,
          "customization_error": null,
          "post_eval_error": null
        }
      ],
      "decision_reason": "Metrics below threshold - triggered retraining"
    }
  ],
  "total": 1
}
```

### Get Single Execution

```text
GET /api/triggers/executions/{execution_id}
```

Retrieve complete details for a specific execution by its ID. This endpoint returns the full execution document including all `nim_results` with error tracking.

**Use Cases:**

- Inspect execution details after manual trigger execution
- Debug failed executions with granular error messages
- Track model performance and metrics over time

**Example:**

```bash
# Get execution details
curl "http://localhost:8000/api/triggers/executions/6971d4501caaa3693fad2341"
```

**Response:**

Returns the same structure as a single item from the executions list, but fetched directly by ID. Refer to the [API Reference](06-api-reference.md#get-single-execution) for the full response schema.

#### Error Tracking in Execution Results

Each `nim_result` includes error fields to track failures at different stages:

- **`eval_error`**: Error if pre-training evaluation failed (for example, dataset not found or evaluation timeout)
- **`customization_error`**: Error if training/customization failed (for example, GPU out of memory or training divergence)
- **`post_eval_error`**: Error if post-training evaluation failed (for example, model deployment failed or evaluation dataset corrupted)

**Example with Evaluation Error:**

```json
{
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "eval_error": "Dataset not found: base-dataset",
      "metrics": {},
      "passed_thresholds": false,
      "training_triggered": false
    }
  ],
  "status": "failed"
}
```

**Example with Training Error:**

```json
{
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "metrics": {"recall_5": 0.75, "map": 0.70},
      "passed_thresholds": false,
      "training_triggered": true,
      "customization_error": "GPU out of memory during training",
      "fine_tuned_model": null,
      "post_training_metrics": {},
      "incomplete_evaluation": false
    }
  ],
  "status": "completed"
}
```

**Example with Post-Training Evaluation Error:**

```json
{
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "metrics": {"recall_5": 0.75, "map": 0.70},
      "passed_thresholds": false,
      "training_triggered": true,
      "customization_id": "cust-xyz",
      "fine_tuned_model": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft",
      "post_eval_error": "Evaluation dataset corrupted",
      "post_training_metrics": {},
      "incomplete_evaluation": false
    }
  ],
  "status": "completed"
}
```

These error fields help you:

- **Identify exactly which stage failed** without querying underlying job data
- **Debug issues faster** with clear error messages
- **Monitor system health** by tracking failure patterns across executions

### Execution Status

The trigger system tracks status at two levels:

**Trigger-Level Status** (`trigger.last_execution_status`):

- `null` - No executions yet
- `success` - Evaluation passed, no retraining needed
- `triggered_retraining` - Metrics failed thresholds, training was triggered
- `failure` - Execution failed with an error

**Execution-Level Status** (`execution.status`):

- `pending` - Execution created, waiting to start
- `running` - Currently executing
- `completed` - Finished successfully
- `failed` - Failed with an error
- `skipped` - Skipped due to concurrent execution

## Model Source: Base vs Fine-Tuned

The `model_source` field controls which model is used for evaluation and as the starting point for fine-tuning:

### `model_source: "base"`

Always uses the base model from the configuration's `customizer_configs.target`.

**Use case:** Starting fresh or comparing against the original model.

### `model_source: "fine_tuned"` (Embedding Jobs Only)

Uses the latest fine-tuned model from previous trigger executions for iterative training. Falls back to base model if no fine-tuned model exists yet.

> **Important Requirements:**
> - `model_source: "fine_tuned"` is **only supported for embedding jobs** (not LLM jobs)
> - The configuration **must use `finetuning_type: "all_weights"`**
> - LoRA fine-tuning types (`"lora"`, `"lora_merged"`) are **not supported** for iterative training
> - Validation occurs at trigger creation/update time to prevent runtime failures

**Use case:** Continuous improvement where each training builds on the previous one.

**Configuration Requirements:**

When using `model_source: "fine_tuned"`, your configuration must specify:

```jsonc
{
  "training_config": {
    "training_type": "sft",
    "finetuning_type": "all_weights"  // Required for iterative fine-tuning
  }
}
```

**Why all_weights?** LoRA adapters cannot be iteratively trained—each LoRA training starts from the base model. Full model fine-tuning (`all_weights`) supports true iterative improvement where each generation builds on the previous fine-tuned model.

**Validation:** The API validates that configurations used with `model_source: "fine_tuned"` have `finetuning_type: "all_weights"`. Refer to [Configuration Validation Errors](#error-incompatible-fine-tuning-type) in the Troubleshooting section for details.

**Trigger Example:**

```json
{
  "name": "iterative-embedding-retrain",
  "model_source": "fine_tuned",
  "config_name": "embedding-all-weights-config"  // Must use all_weights
}
```

The `config_name` must reference a configuration that uses `finetuning_type: "all_weights"`.

The trigger automatically tracks the latest fine-tuned model name in `latest_fine_tuned_models`:

```json
{
  "latest_fine_tuned_models": {
    "nvidia/llama-3.2-nv-embedqa-1b-v2": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122"
  }
}
```

**Note:** Only the fine-tuned model **name** (string) is stored. The pipeline constructs the full target configuration (including `model_uri` for weights location) from the configuration and customization records using this model name.

#### Understanding `model_used` in Execution Results

When viewing execution results, each `nim_result` includes two model-related fields:

- **`model_name`**: The base model name (used as a key for grouping/matching results across executions)
- **`model_used`**: The actual model that was used for evaluation/training

**For `model_source: "base"`** (default):
- `model_used` equals `model_name` (both show the base model)

**For `model_source: "fine_tuned"`** (iterative fine-tuning):
- `model_name` shows the base model (for consistent grouping)
- `model_used` shows the fine-tuned model that was actually used (e.g., `"nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122"`)

**Example: Iterative Fine-Tuning Execution Result**

```json
{
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260122",
      "metrics": {"recall_5": 0.88, "map": 0.83},
      "fine_tuned_model": "nvidia/llama-3.2-nv-embedqa-1b-v2-ft-20260125"
    }
  ]
}
```

In this example:
- The execution used the previous fine-tuned model (`model_used`: `20260122`) for evaluation
- A new fine-tuned model was created (`fine_tuned_model`: `20260125`)
- Results are grouped by the base model name (`model_name`) for consistency

#### Using `reference_job_id` to Pin to a Specific Model Version

By default, `model_source: "fine_tuned"` uses the **latest** fine-tuned model. To use a model from a **specific training run**, set `reference_job_id`:

```json
{
  "model_source": "fine_tuned",
  "reference_job_id": "678a1b2c3d4e5f6g7h8i9j0k"
}
```

**Important:**

- ✅ `reference_job_id` must be from **THIS trigger's execution history**
- ✅ The system validates that the job was created by this trigger
- ❌ Using a job ID from another trigger will result in an error

**Use case:** Roll back to a known-good model version if the latest training produced worse results.

**Example workflow:**

1. Trigger runs weekly and trains new models
2. Latest model (week 4) performs poorly
3. Update trigger to use `reference_job_id` from week 3's successful training
4. Future evaluations use the week 3 model until you're ready to train again

## Cron Schedule Examples

The `eval_schedule` field uses standard cron syntax:

```text
┌───────────── minute (0-59)
│ ┌───────────── hour (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month (1-12)
│ │ │ │ ┌───────────── day of week (0-6, Sunday=0)
│ │ │ │ │
* * * * *
```

| Schedule | Cron Expression | Description |
|----------|----------------|-------------|
| Every minute | `* * * * *` | Testing only |
| Every hour | `0 * * * *` | Hourly at minute 0 |
| Every 6 hours | `0 */6 * * *` | 00:00, 06:00, 12:00, 18:00 |
| Daily at midnight | `0 0 * * *` | Every day at 00:00 |
| Weekly on Sunday | `0 0 * * 0` | Sunday at midnight |
| Monthly | `0 0 1 * *` | First day of month |

## Multi-NIM Support

Triggers support configurations with multiple NIMs. Each NIM is evaluated and trained independently:

```json
{
  "config_data": {
    "nims": [
      {
        "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
        "customizer_configs": {
          "target": "nvidia/llama-3.2-nv-embedqa-1b@v2"
        }
      },
      {
        "model_name": "nvidia/nv-embedqa-e5-v5",
        "customizer_configs": {
          "target": "nvidia/nv-embedqa-e5@v5"
        }
      }
    ]
  }
}
```

The execution results show per-NIM metrics:

```json
{
  "nim_results": [
    {
      "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "model_used": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "metrics": {"recall_5": 0.82},
      "training_triggered": true
    },
    {
      "model_name": "nvidia/nv-embedqa-e5-v5",
      "model_used": "nvidia/nv-embedqa-e5-v5",
      "metrics": {"recall_5": 0.75},
      "training_triggered": true
    }
  ],
  "total_nims": 2,
  "nims_passed": 0,
  "nims_failed": 2,
  "nims_retrained": 2
}
```

> **Note:** When NIMs fail metric thresholds in a `metric_degradation` trigger, **only the failed NIMs are retrained**. NIMs that pass thresholds continue using their current models (selective per-NIM retraining for efficient resource usage).

## Celery Beat Scheduler

Triggers are automatically executed by Celery Beat, which:

1. **Loads triggers from MongoDB** every 60 seconds
2. **Checks for due tasks** every 30 seconds
3. **Sends tasks to Celery workers** when schedules match

### Viewing Scheduler Logs

```bash
docker compose logs celery_beat --tail=50
```

Example output:

```text
[INFO] Setting up MongoDB schedule...
[INFO] Loaded 2 enabled triggers from MongoDB
[INFO] Schedule updated with 2 entries
[INFO] Scheduler: Sending due task trigger-6971d2ad0fc225d83080c286 (tasks.execute_trigger)
```

### Dynamic Updates

- **New triggers** are picked up within ~60 seconds
- **Disabled triggers** are removed from the schedule automatically
- **No restart required** when adding/updating/deleting triggers

## Best Practices

### 1. Start with Conservative Schedules

Begin with weekly or monthly schedules, then adjust based on your data volume and model drift patterns.

### 2. Set Meaningful Thresholds

For `metric_degradation` triggers, set thresholds based on your application's requirements:

```jsonc
{
  "metric_thresholds": {
    "recall_5": 0.85,    // Alert if recall drops below 85%
    "map": 0.80          // Alert if MAP drops below 80%
  }
}
```

### 3. Use Data Windows Appropriately

For triggers that should only consider recent data:

```jsonc
{
  "data_window_months": 3  // Only use data from the last 3 months (90 days)
}
```

**Supported Values:**

- **Integer (≥1)**: Number of months to look back (each month = 30 days)
  - `1` = Last month (30 days)
  - `3` = Last quarter (90 days)
  - `6` = Last 6 months (180 days)
  - `12` = Last 12 months (360 days)
  - Any positive integer supported for custom windows (for example, 2, 9, 18, or 24)
- **`"all"`**: Use all available data (no date filtering)
- **`null`** (default): Use config's `from_date`/`to_date` settings

**Note:** Months are treated as fixed 30-day periods for consistent calculation. `data_window_months` is applicable only for embedding jobs.

**Examples:**

```bash
# Weekly retrain using last month of data
curl -X POST http://localhost:8000/api/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weekly-last-month",
    "trigger_type": "schedule",
    "eval_schedule": "0 0 * * 0",
    "data_window_months": 1,
    "config_name": "embedding_config"
  }'

# Quarterly retrain using last 6 months
curl -X POST http://localhost:8000/api/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "quarterly-6month-window",
    "trigger_type": "schedule",
    "eval_schedule": "0 0 1 */3 *",
    "data_window_months": 6,
    "config_name": "embedding_config"
  }'

# Use all historical data
curl -X POST http://localhost:8000/api/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "full-history-retrain",
    "trigger_type": "schedule",
    "eval_schedule": "0 0 1 * *",
    "data_window_months": "all",
    "config_name": "embedding_config"
  }'
```

### 4. Monitor Consecutive Failures

Triggers track consecutive failures and can be automatically disabled to prevent repeated failed executions:

```jsonc
{
  "max_consecutive_failures": 3,  // Auto-disable after 3 consecutive failures
  "consecutive_failures": 0       // Current count (read-only, managed by system)
}
```

**How it works:**

1. **Failure Tracking:** When an execution completes with `status: "failed"`, `consecutive_failures` is incremented by 1
2. **Reset on Success:** When an execution succeeds (`execution.status: "completed"`) or triggers training (`trigger.last_execution_status: "triggered_retraining"`), `consecutive_failures` resets to 0
3. **Skip Ignored:** Skipped executions (`status: "skipped"`) do not affect the failure count
4. **Auto-Disable:** When `consecutive_failures >= max_consecutive_failures` (and `max_consecutive_failures > 0`), the trigger is automatically disabled (`enabled: false`)
5. **Notification:** A warning is logged when auto-disable occurs: `"Trigger '<name>' auto-disabled after N consecutive failures"`

**Example:** With `max_consecutive_failures: 3`:

- Execution 1 fails → `consecutive_failures: 1`
- Execution 2 fails → `consecutive_failures: 2`
- Execution 3 fails → `consecutive_failures: 3`, trigger auto-disabled ⚠️
- Manual re-enable required via API: `POST /api/triggers/{id}/enable`

> **Note:** The minimum value for `max_consecutive_failures` is `1`. To re-enable a trigger after auto-disable, use `POST /api/triggers/{id}/enable`.

### 5. Use Fine-Tuned Model Source for Continuous Improvement

For production systems, use `model_source: "fine_tuned"` to build on previous improvements:

```jsonc
{
  "model_source": "fine_tuned",
  "config_name": "config-with-all-weights"  // Must use finetuning_type: "all_weights"
}
```

> **Important Notes on Model Source:**
>
> **Embedding Jobs Only:** `model_source="fine_tuned"` is **currently supported only for embedding jobs**. LLM triggers must use `model_source="base"` (the default).
>
> **Configuration Requirements:** When using `model_source="fine_tuned"`, the configuration **must have `finetuning_type: "all_weights"`**. This is validated at trigger creation/update time.
>
> **Why all_weights?** LoRA fine-tuning types (`"lora"` or `"lora_merged"`) cannot be used for iterative training because LoRA adapters always start from the base model. Full model fine-tuning allows true iterative improvement where each generation builds on the previous fine-tuned model.

## Troubleshooting

### Trigger Not Executing

1. **Check if enabled:** `GET /api/triggers/{id}` should show `"enabled": true`
2. **Verify schedule:** Ensure cron expression is valid
3. **Check Beat logs:** `docker compose logs celery_beat`
4. **Manual test:** `POST /api/triggers/{id}/execute`

### Configuration Validation Errors

When creating or updating a trigger with `model_source: "fine_tuned"`, you may encounter validation errors:

#### Error: Configuration Not Found

```json
{
  "detail": "Configuration 'my-config' not found. Please create the configuration before creating a trigger."
}
```

**Solution:** Create the configuration first using `POST /api/configs`

#### Error: Incompatible Fine-Tuning Type

```json
{
  "detail": "Configuration 'my-config' has finetuning_type='lora_merged'. Iterative fine-tuning (model_source='fine_tuned') currently only supports finetuning_type='all_weights'. Please update the configuration or use model_source='base' instead."
}
```

#### Solutions

1. Update your configuration to use `finetuning_type: "all_weights"` in `training_config`
2. **OR** Change the trigger to use `model_source: "base"` for LoRA-based training

#### Example: Updating Configuration for Iterative Training

```bash
# Update existing configuration to support iterative fine-tuning
curl -X PUT http://localhost:8000/api/configs/my-config \
  -H "Content-Type: application/json" \
  -d '{
    "config_data": {
      "training_config": {
        "finetuning_type": "all_weights"
      }
    }
  }'
```

### Metrics Not Being Compared

Ensure `metric_thresholds` keys match the metrics in your `evaluation_config`:

```jsonc
// In your evaluation configuration
{
  "evaluation_config": {
    "embedding_eval_metrics": {
      "metrics": ["recall_5", "map"]  // These metrics are evaluated
    }
  }
}
```

```jsonc
// In the trigger — keys must match the metric names above
{
  "metric_thresholds": {
    "recall_5": 0.85,
    "map": 0.80
  }
}
```

### Training Not Triggered

For `metric_degradation` triggers, training only happens when thresholds are **not met**.

**Important:** In multi-NIM configurations, only the NIMs that fail thresholds are retrained. This optimizes resource usage by not retraining models that are performing well.

**Example:** If you have 3 NIMs and only 1 fails its threshold:

- All 3 NIMs are evaluated
- Only the 1 failing NIM is retrained
- The other 2 NIMs continue using their current models
- In `nim_results`, only the failing NIM has `training_triggered: true`

Check execution results:

```bash
curl http://localhost:8000/api/triggers/{id}/executions
```

Look at `thresholds_checked` to see actual vs. threshold values for each NIM.

### Execution Errors

When an execution fails, check the `nim_results` for detailed error information:

```bash
# Get execution details
curl http://localhost:8000/api/triggers/{trigger_id}/executions

# Look for error fields in nim_results
```

**Common Error Types:**

| Error Field | Typical Causes | Resolution |
|-------------|----------------|------------|
| `eval_error` | Dataset not found, evaluation timeout, or insufficient data | Check dataset availability, verify workload_id/client_id, ensure data ingestion completed |
| `customization_error` | GPU out of memory, training divergence, or invalid hyperparameters | Reduce batch size, adjust learning rate, check GPU resources |
| `post_eval_error` | Model deployment failed or evaluation dataset corrupted | Check model artifacts, verify evaluation dataset integrity |

**Example: Debugging an Evaluation Error**

```bash
# Get executions
curl http://localhost:8000/api/triggers/6971d2ad0fc225d83080c286/executions | jq '.executions[0].nim_results[].eval_error'

# Output: "Dataset not found: base-dataset for workload 'my-app'"
# → Fix: Ensure data ingestion is enabled and data exists for the workload
```

**Example: Debugging a Training Error**

```bash
# Check nim_results for customization errors
curl http://localhost:8000/api/triggers/6971d2ad0fc225d83080c286/executions | jq '.executions[0].nim_results[]'

# Output shows: "customization_error": "GPU out of memory during epoch 1"
# → Fix: Reduce batch_size in training_config from 64 to 32
```

## Related Documentation

- [Configuration Guide](02-configuration.md) - Complete configuration reference
- [API Reference](06-api-reference.md) - Full API documentation
- [Embedding Training](14-embedding-training.md) - Embedding model training details
- [Workflow Orchestration](07-workflow-orchestration.md) - Understanding the job workflow
