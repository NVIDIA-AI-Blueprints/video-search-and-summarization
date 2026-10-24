# Task Orchestration and Workflow Management

Learn how the Data Flywheel Blueprint orchestrates complex workflows using Celery for task management, job lifecycle control, and resource cleanup.

## Workflow Architecture

The Data Flywheel uses a **Directed Acyclic Graph (DAG)** of Celery tasks to orchestrate the complete flywheel workflow. Each job progresses through multiple stages with automatic error handling and resource cleanup.

### High-Level Workflow Stages

```mermaid
graph TD
    A[Initialize Workflow] --> B[Create Datasets]
    B --> C[Start LLM Judge]
    C --> D[Deploy NIM]
    D --> E[Run Base Evaluations]
    D --> F[Run ICL Evaluations]
    E --> G[Start Customization]
    F --> G
    G --> H[Run Customization Eval]
    H --> I[Shutdown Deployments]
    I --> J[Finalize Results]
```

## Task Definitions and Dependencies

### 1. **`initialize_workflow`**
**Purpose**: Sets up the flywheel run, validates configuration, and prepares the job for execution.

**Source**: `src/tasks/tasks.py:104`

**Key Operations**:
- Validates workload_id and client_id
- Initializes database records
- Sets job status to `RUNNING`

**Dependencies**: None (entry point)

```python
# Example task invocation
run_nim_workflow_dag.delay(
    workload_id="customer-service-v1",
    flywheel_run_id="507f1f77bcf86cd799439011",
    client_id="production-app"
)
```

### 2. **`create_datasets`**
**Purpose**: Extracts data from Elasticsearch and creates training/evaluation datasets.

**Source**: `src/tasks/tasks.py:185`

**Key Operations**:
- Queries Elasticsearch for logged interactions
- Validates data format (OpenAI chat completion format)
- Creates base, ICL, and fine-tuning datasets
- Applies data split configuration
- Uploads datasets to NeMo Data Service
- **Conditionally manages embedding NIM lifecycle for semantic similarity**

**Dependencies**: `initialize_workflow` (and optionally `spin_up_nim` for embedding model)

**Dataset Types Created**:
- **Base Evaluation**: Held-out production data for baseline testing
- **ICL Evaluation**: Dataset augmented with in-context learning examples
- **Fine-tuning**: Training data for model customization

#### ICL Example Selection Methods

The Data Flywheel supports two methods for selecting in-context learning examples:

**1. Uniform Distribution** (`uniform_distribution`)
- **Description**: Distributes examples evenly across different tool types
- **Use Case**: Provides balanced representation of all available tools
- **Behavior**: For tool-calling workloads, ensures each tool gets roughly equal representation in the ICL examples
- **Requirements**: No additional configuration needed
- **Performance**: Fast selection with no additional infrastructure requirements

**2. Semantic Similarity** (`semantic_similarity`)
- **Description**: Selects examples based on semantic similarity using vector embeddings
- **Use Case**: Finds the most relevant examples for each evaluation query
- **Behavior**: 
  - Uses an embedding model to identify semantically similar examples from historical data
  - For tool-calling workloads, applies a relevance-coverage strategy
  - The `relevance_ratio` parameter controls the trade-off:
    - Default 0.7 = 70% examples selected for pure semantic relevance
    - Remaining 30% selected to ensure coverage of different tools
    - Value of 1.0 disables coverage-based selection entirely
- **Requirements**: Requires `similarity_config` with `embedding_nim_config`

#### Embedding NIM Workflow Integration

When using `semantic_similarity` with `deployment_type: "local"`, the workflow includes embedding NIM lifecycle management:

**Workflow Chain** (`src/tasks/tasks.py:1107-1118`):
```python
if (
    icl_config.example_selection == "semantic_similarity"
    and icl_config.similarity_config.embedding_nim_config.deployment_type == "local"
):
    dataset_workflow = chain(
        spin_up_nim.s(embedding_nim_config),  # Deploy embedding NIM
        create_datasets.s()                   # Create datasets + cleanup
    )
else:
    dataset_workflow = create_datasets.s()    # Direct dataset creation
```

**Embedding NIM Lifecycle**:

1. **Spin-Up** (`spin_up_nim` task): Deploys embedding NIM and waits for readiness
2. **Processing** (`create_datasets` task): 
   - Generates embeddings of training dataset in batches of 32
   - Creates similarity index in Elasticsearch  
   - For each record in evaluation dataset; performs vector search and example selection
3. **Spin-Down** (`create_datasets` finally block): Deletes ES index and shuts down embedding NIM

**Performance Impact**:
- Adds ~2-5 minutes for embedding NIM deployment
- Batch processing optimizes embedding generation
- Guaranteed cleanup prevents resource leaks

### 3. **`wait_for_llm_as_judge`**
**Purpose**: Ensures LLM judge service is ready for evaluation tasks.

**Source**: `src/tasks/tasks.py:278`

**Key Operations**:
- Validates judge model configuration
- Waits for judge service readiness (local deployments only)
- Waits for model synchronization (local deployments only)
- Handles both local and remote judge configurations

**Dependencies**: `create_datasets`

**Note**: For local LLM judge deployments, the actual deployment occurs during application startup (Celery worker initialization), not in this task. This task only waits for the pre-deployed judge to be fully ready.

### 4. **`spin_up_nim`** (Sequential Execution)
**Purpose**: Deploys NVIDIA Inference Microservices (NIMs) for each candidate model.

**Source**: `src/tasks/tasks.py:350`

**Key Operations**:
- Creates NIM deployment configurations
- Deploys NIMs via NeMo Deployment Manager
- Waits for deployment readiness
- Validates endpoint accessibility

**Dependencies**: `wait_for_llm_as_judge`

**Sequential Pattern**: NIMs are deployed one at a time to manage resource allocation and avoid GPU conflicts.

### 5. **`run_base_eval`** and **`run_icl_eval`** (Parallel Execution)
**Purpose**: Runs comprehensive evaluations against deployed NIMs.

**Source**: `src/tasks/tasks.py:474` and `src/tasks/tasks.py:479`

**Key Operations**:
- Executes base evaluations (no in-context learning)
- Executes ICL evaluations (with few-shot examples)
- Supports multiple evaluation types: accuracy, tool-calling
- Stores results in database and MLflow (if enabled)

**Dependencies**: `spin_up_nim`

**Evaluation Types**:
- **Accuracy**: LLM-as-judge scoring against reference responses
- **Tool Calling**: Function calling accuracy for agentic workflows

### 6. **`start_customization`**
**Purpose**: Initiates fine-tuning of candidate models using production data.

**Source**: `src/tasks/tasks.py:727`

**Key Operations**:
- Creates customization jobs in NeMo Customizer
- Configures LoRA training parameters
- Monitors training progress
- Handles training failures and retries

**Dependencies**: None (runs in parallel with `run_base_eval` and `run_icl_eval`)

**Customization Features**:
- **LoRA Fine-tuning**: Parameter-efficient training
- **Multi-GPU Support**: Distributed training across multiple GPUs
- **Progress Monitoring**: Real-time training progress tracking

### 7. **`run_customization_eval`**
**Purpose**: Evaluates fine-tuned models against base evaluation datasets.

**Source**: `src/tasks/tasks.py:874`

**Key Operations**:
- Deploys customized models
- Runs same evaluation suite as base models

**Dependencies**: `start_customization`

### 8. **`shutdown_deployment`**
**Purpose**: Gracefully shuts down NIM deployments to free resources.

**Source**: `src/tasks/tasks.py:925`

**Key Operations**:
- Marks the NIM as completed by updating deployment status in database
- Stops the NIM deployments
- Preserves evaluation results and model artifacts

**Dependencies**: `run_customization_eval`

### 9. **`finalize_flywheel_run`**
**Purpose**: Aggregates results and marks the job as complete.

**Source**: `src/tasks/tasks.py:1004`

**Key Operations**:
- Updates job status to `COMPLETED`

**Dependencies**: `shutdown_deployment`

## Job Lifecycle Management

### Job States and Transitions

**Source**: `src/api/schemas.py` (FlywheelRunStatus enum)

```mermaid
stateDiagram-v2
    [*] --> PENDING: Job Created
    PENDING --> RUNNING: Workflow Started
    RUNNING --> COMPLETED: All Tasks Successful
    RUNNING --> FAILED: Task Error
    RUNNING --> CANCELLED: User Cancellation
    FAILED --> [*]
    COMPLETED --> [*]
    CANCELLED --> [*]
```

**Note**: Both flywheel runs (FlywheelRunStatus) and individual NIM runs (NIMRunStatus) support CANCELLED states.

### Cancellation Mechanism

**Source**: `src/lib/flywheel/cancellation.py:1-47`

The flywheel implements **graceful cancellation** with automatic resource cleanup:

```python
def check_cancellation(flywheel_run_id: str) -> None:
    """Check if the flywheel run has been cancelled and raise exception if so."""
    # Checks database for cancellation status
    # Raises FlywheelCancelledError to stop task execution
```

**Cancellation Process**:
1. User calls `POST /api/jobs/{id}/cancel`
2. Database marks job as `CANCELLED`
3. All running tasks check cancellation status
4. Tasks raise `FlywheelCancelledError` and exit
5. Cleanup manager removes all resources
6. Job remains in `CANCELLED` state

### Automatic Resource Cleanup

**Source**: `src/lib/flywheel/cleanup_manager.py:1-232`

The cleanup manager automatically handles resource management during:
- Normal workflow completion
- Job cancellation
- System shutdown
- Worker crashes

**Cleanup Operations**:
```python
class CleanupManager:
    def cleanup_all_running_resources(self):
        """Main cleanup procedure for all running resources."""
        # 1. Find all running flywheel runs
        # 2. Clean up each flywheel run
        # 3. Shutdown LLM judge
        # 4. Clean up customization configs
        # 5. Report cleanup results
```

## Monitoring and Observability

### Celery Task Monitoring

**Flower Web UI**: Available at `http://localhost:5555` during development

**Key Metrics to Monitor**:
- **Active Tasks**: Currently executing tasks
- **Task Queue Length**: Pending tasks waiting for workers
- **Task Success/Failure Rate**: Overall workflow reliability
- **Resource Utilization**: Worker CPU and memory usage

### Database Monitoring

**Job Progress Tracking**:
```python
# Query job status
db.flywheel_runs.find_one({"_id": ObjectId(job_id)})

# Monitor NIM deployments and evaluations
db.nims.find({"flywheel_run_id": job_id})

# Check evaluation results
db.evaluations.find({"nim_id": nim_id})
```

**Key Collections**:
- `flywheel_runs`: Overall job status and metadata
- `nims`: NIM deployment and evaluation status
- `evaluations`: Individual evaluation results and metrics
- `customizations`: Model customization job tracking
- `llm_judge_runs`: LLM judge execution tracking

### Logging Configuration

**Source**: `src/log_utils.py`

Structured logging with configurable levels:
```python
logger = setup_logging("data_flywheel.tasks")
logger.info(f"Starting workflow for job {flywheel_run_id}")
logger.error(f"Task failed: {error_message}")
```

## Troubleshooting Common Issues

### Task Failure Scenarios

#### 1. **Data Validation Failures**
**Symptoms**: Job fails during `create_datasets` stage
**Causes**: 
- Insufficient data in Elasticsearch
- Invalid OpenAI format in logged data
- Missing required fields (workload_id, client_id)

**Solution**:
```bash
# Check data quality
curl "http://localhost:9200/flywheel/_search" | jq '.hits.hits[0]._source'

# Validate data format by loading (validation happens automatically)
# Note: Place your data files in the data/ directory
uv run python src/scripts/load_data.py --type llm \
  --workload-id test-validation \
  --client-id test-client \
  --file aiva-test.jsonl
```

> **💡 Data File Location:** The `--file` parameter accepts relative paths (resolved from `data/` directory) or absolute paths.

> **⭐ Tip:** Always provide `workload-id` and `client-id` for proper data organization and job tracking.

#### 2. **NIM Deployment Failures**
**Symptoms**: Job fails during `spin_up_nim` stage
**Causes**:
- Insufficient GPU resources
- Network connectivity issues
- Invalid model configurations

**Solution**:
```bash
# Check GPU availability
nvidia-smi

# Verify NeMo connectivity
curl "http://nemo.test/v1/models"

# Check Kubernetes resources
kubectl get pods -n dfwbp
```

> **💡 Tip**: When checking pods, ensure READY shows `1/1` AND STATUS shows `Running` for service pods to be truly operational. See [Understanding Pod Readiness](./faq-troubleshooting.md#pods-not-starting) for details.

#### 3. **Evaluation Timeouts**
**Symptoms**: Tasks hang during evaluation stages
**Causes**:
- Large dataset size
- Slow model inference
- Network latency to remote services

**Solution**:
```yaml
# Increase task timeout in config
celery_config:
  task_time_limit: 7200  # 2 hours
  task_soft_time_limit: 6600  # 1.8 hours
```

### Recovery Procedures

#### Manual Task Recovery
```python
# Cancel stuck job (use actual job_id)
POST /api/jobs/${job_id}/cancel

# Clean up resources manually
from src.lib.flywheel.cleanup_manager import CleanupManager
cleanup = CleanupManager(db_manager)
cleanup.cleanup_all_running_resources()
```

#### Database Consistency Check
```python
# Find orphaned resources
db.nims.find({"status": "running", "flywheel_run_id": {"$nin": active_jobs}})

# Reset stuck jobs
db.flywheel_runs.update_many(
    {"status": "running", "started_at": {"$lt": cutoff_time}},
    {"$set": {"status": "failed", "error": "Timeout recovery"}}
)
```

## Performance Optimization

### Parallel Execution Patterns

**Source**: `src/tasks/tasks.py:1063` - DAG execution logic

```python
# Parallel execution within each NIM's workflow chain
for nim in settings.nims:
    nim_chain = chain(
        spin_up_nim.s(nim_config=nim.model_dump()),
        group(
            run_base_eval.s(),
            run_icl_eval.s(),
            chain(
                start_customization.s(),
                run_customization_eval.s(),
            ),
        ),
        shutdown_deployment.s(),
    )
    nim_chains.append(nim_chain)

# Multiple NIM chains execute in sequence
workflow = chain(
    initialize_workflow.s(...),
    dataset_workflow,
    wait_for_llm_as_judge.s(),
    chain(*nim_chains),
    finalize_flywheel_run.s(),
)
```

**Execution Pattern**: Each NIM processes through its own sequential chain, but within each NIM's workflow, evaluations and customization run in parallel using Celery's `group()` primitive. Multiple NIMs are processed sequentially to prevent GPU resource conflicts.

### Resource Allocation Strategies

**Worker Scaling**:
```bash
# Scale Celery workers based on load
celery -A src.tasks.tasks worker --loglevel=info --concurrency=4

# Monitor worker utilization
celery -A src.tasks.tasks inspect active
```

**GPU Resource Management**:
- NIMs deploy to separate GPU pools
- Customization jobs use dedicated training GPUs
- Automatic GPU cleanup after task completion
