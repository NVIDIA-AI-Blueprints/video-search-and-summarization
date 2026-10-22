# Deployment Architecture

This page describes the internal design of the model deployment feature -- the adapter pattern, the request lifecycle, how Celery tasks drive async execution, and where to find each piece of code.

---

## Code Map

```
src/
├── api/deployment/               # FastAPI layer
│   ├── endpoints.py              # REST endpoints (/api/model-deployments)
│   ├── service.py                # DeploymentService (MongoDB + task queuing)
│   ├── models.py                 # MongoDB document model (ModelDeployment)
│   └── schemas.py                # Pydantic request/response schemas
│
├── lib/deployment/adapters/      # Adapter framework
│   ├── __init__.py               # Auto-imports adapter packages
│   ├── base.py                   # BaseDeploymentAdapter ABC, DeploymentResult, enums
│   ├── factory.py                # DeploymentAdapterFactory (registry + factory)
│   ├── datarobot/                # DataRobot adapter
│   │   ├── adapter.py            # DataRobotAdapter implementation
│   │   └── config.py             # DataRobotConfig (YAML + overrides)
│   ├── custom_nim/               # Custom NIM build-and-push adapter
│   │   ├── adapter.py            # CustomNIMAdapter (docker + crane engines)
│   │   ├── config.py             # CustomNIMConfig, CraneConfig
│   │   └── crane.py              # CraneJobManager (K8s Job lifecycle)
│   └── nmp/                      # NMP adapter
│       ├── adapter.py            # NMPAdapter (wraps DMSClient)
│       └── __init__.py
│
├── tasks/deployment/
│   └── tasks.py                  # Celery tasks: execute_deployment, execute_undeploy
│
└── config.py                     # System config (NMPConfig, NIMConfig, datastore_base_url, etc.)

config/
├── config.yaml                        # System config (nmp_config section)
├── datarobot_deployment_config.yaml   # DataRobot-specific defaults
└── custom_nim_deployment_config.yaml  # Custom NIM build-and-push defaults
```

---

## Adapter Pattern

The system uses a **registry-based adapter pattern** to keep platform-specific logic isolated from the core API.

### Base Interface

Every adapter extends `BaseDeploymentAdapter` and implements four methods:

```python
class BaseDeploymentAdapter(ABC):

    @property
    def platform(self) -> DeploymentPlatform: ...

    def deploy(self, datastore_url, model_name, model_version,
               deployment_name, config=None, progress_callback=None) -> DeploymentResult: ...

    def get_status(self, deployment_id) -> DeploymentResult: ...

    def undeploy(self, deployment_id, progress_callback=None) -> DeploymentResult: ...

    def get_inference_endpoint(self, deployment_id) -> str: ...
```

### Factory & Registration

Adapters register themselves with a decorator:

```python
@DeploymentAdapterFactory.register(DeploymentPlatform.DATAROBOT)
class DataRobotAdapter(BaseDeploymentAdapter):
    ...
```

At import time, `src/lib/deployment/adapters/__init__.py` imports each adapter package, triggering registration. The factory can then create instances:

```python
adapter = DeploymentAdapterFactory.create(platform, config)
```

If a platform enum exists but no adapter is registered, the factory raises `NotImplementedError` with guidance on how to add one.

### Add a new adapter

1. Create `src/lib/deployment/adapters/your_platform/` with `__init__.py` and `adapter.py`.
2. Add a `DeploymentPlatform.YOUR_PLATFORM` enum value in `base.py`.
3. Decorate your class with `@DeploymentAdapterFactory.register(DeploymentPlatform.YOUR_PLATFORM)`.
4. Import the package in `adapters/__init__.py`.

No changes needed to the API, service layer, or Celery tasks.

---

## Request Lifecycle

### Deploy

```
 Client                  FastAPI                 DeploymentService       Celery Worker           Platform
   │                        │                          │                      │                      │
   │  POST /model-deployments                          │                      │                      │
   │ ─────────────────────> │                          │                      │                      │
   │                        │  create_deployment()     │                      │                      │
   │                        │ ───────────────────────> │                      │                      │
   │                        │                          │  lookup model info   │                      │
   │                        │                          │  (customizations DB) │                      │
   │                        │                          │  create record       │                      │
   │                        │                          │  (MongoDB: PENDING)  │                      │
   │                        │                          │  queue Celery task   │                      │
   │                        │                          │ ──────────────────> │                      │
   │  <── 201 Created       │                          │                      │                      │
   │                        │                          │                      │  create adapter      │
   │                        │                          │                      │  adapter.deploy() ──>│
   │                        │                          │                      │                      │ (platform work)
   │                        │                          │                      │  <── DeploymentResult│
   │                        │                          │  update MongoDB      │                      │
   │                        │                          │  (READY or FAILED)   │                      │
```

### Status Check

```
 Client                  FastAPI                 DeploymentService
   │  GET .../status        │                          │
   │ ─────────────────────> │  get_deployment()        │
   │                        │ ───────────────────────> │  read from MongoDB
   │  <── DeploymentStatus  │                          │
```

### Undeploy

```
 Client                  FastAPI                 DeploymentService       Celery Worker           Platform
   │  DELETE .../{id}       │                          │                      │                      │
   │ ─────────────────────> │  delete_deployment()     │                      │                      │
   │                        │ ───────────────────────> │  mark DELETING       │                      │
   │                        │                          │  queue undeploy task │                      │
   │                        │                          │ ──────────────────> │                      │
   │  <── 200 (DELETING)    │                          │                      │  adapter.undeploy()─>│
   │                        │                          │                      │  <── result          │
   │                        │                          │  update DELETED      │                      │
```

---

## Service Layer

`DeploymentService` (`src/api/deployment/service.py`) is the bridge between the API and the async tasks:

- **Model resolution** -- `_get_model_info_by_name()` queries the MongoDB `customizations` collection to resolve `namespace/model@version` into model metadata and the datastore URL.
- **Duplicate prevention** -- `check_ongoing_deployment()` ensures only one active deployment per target (for example, per DataRobot `deployment_id`). A partial MongoDB unique index enforces this at the database level.
- **Record management** -- CRUD operations on the `model_deployments` MongoDB collection.
- **Inference examples** -- `_generate_inference_examples()` produces platform-specific curl/Python examples based on model type for inclusion in API responses.

---

## Celery Tasks

`src/tasks/deployment/tasks.py` contains two main tasks:

### `execute_deployment(deployment_id)`

1. Reads the deployment record from MongoDB.
2. Creates the adapter via `DeploymentAdapterFactory.create(platform, config)`.
3. Calls `adapter.deploy(...)` with a `progress_callback` that writes real-time progress/status updates to MongoDB.
4. On success: updates record to `READY` with endpoint URL and platform metadata.
5. On failure: updates record to `FAILED` with error message.
6. Retries up to 3 times with 60s delay on transient errors.

### `execute_undeploy(deployment_id)`

1. Reads the deployment record from MongoDB.
2. Creates the adapter via `DeploymentAdapterFactory.create(platform, config)`.
3. Resolves the platform deployment ID and calls `adapter.undeploy(platform_id, progress_callback)`.
4. Updates the record to `DELETED`.
5. Retries up to 2 times with 30s delay on transient errors.

---

## Data Model

The `ModelDeployment` MongoDB document (`src/api/deployment/models.py`):

| Field | Type | Description |
|-------|------|-------------|
| `_id` | `str` | Auto-generated deployment record ID. |
| `job_id` | `str?` | Source DFW job ID (optional reference). |
| `model_name` | `str` | Model name without version (`namespace/model`). |
| `model_version` | `str?` | Model version/revision. |
| `datastore_url` | `str?` | DataStore URL for model artifacts. |
| `deployment_name` | `str` | Human-readable deployment name. |
| `platform` | `DeploymentPlatform` | Target platform (`datarobot`, `nmp`). |
| `platform_config` | `dict` | Platform-specific config from the request. |
| `status` | `DeploymentStatus` | Current status. |
| `progress` | `float` | Deployment progress 0.0 - 1.0 (default: 0.0). |
| `status_message` | `str?` | Human-readable status message. |
| `endpoint_url` | `str?` | Inference endpoint (set when `READY`). |
| `error` | `str?` | Error message (set when `FAILED`). |
| `platform_deployment_id` | `str?` | Platform-specific deployment ID. |
| `platform_metadata` | `dict` | Platform-specific metadata (default: empty). |
| `celery_task_id` | `str?` | Celery task ID. |
| `logs` | `list[DeploymentLogEntry]` | Timestamped log entries. |
| `created_at` | `datetime` | Record creation time. |
| `updated_at` | `datetime` | Last update time. |
| `started_at` | `datetime?` | When deployment started. |
| `deployed_at` | `datetime?` | When deployment completed. |
| `deleted_at` | `datetime?` | When deployment was deleted. |

---

## See Also

- [Getting Started & API Reference](index.md)
- [DataRobot Adapter](datarobot.md)
- [Custom NIM Adapter](custom-nim.md)
- [NMP Adapter](nmp.md)
- [Troubleshooting](troubleshooting.md)
