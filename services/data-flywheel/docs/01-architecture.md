# Flywheel Architecture Overview

## Core Components Required for a Flywheel

1. **Instrumented Gen-AI Application**: Your service must tag distinct workloads (routes, nodes, agent steps) and log every prompt/completion pair.
2. **Log Store**: Elasticsearch (or equivalent) captures production traffic so datasets can be built automatically.
3. **Dataset & Model Ops Infra**: The blueprint spins up NeMo Datastore, Evaluator, Customizer, plus local API & workers to orchestrate jobs.
4. **Post-Eval Human Review**: Engineers/researchers validate promising models before promotion; no user feedback collection.

Think of this flywheel as a discovery and promotion service that surfaces promising smaller models rather than a fully autonomous replacement engine.

## Data Flywheel Blueprint Architecture Diagram

The following diagram illustrates the high-level architecture of the Data Flywheel Blueprint:

![Data Flywheel Blueprint Architecture](../docs/images/data-flywheel-blueprint.png)

> **Note**
>
> Version 1 of the Data Flywheel Foundational Blueprint optimizes **cost & latency** via model distillation. Future versions will target **absolute accuracy gains** and **agentic observability** (prompt / template suggestions).

### How Production Logs Flow Into the Flywheel

> **📖 For complete data logging implementation:** See [Data Logging Guide](data-logging.md)

Use a continuous log exportation flow for your production environments:

1. **Application emits JSON**: Every prompt/response is captured by your service (language-agnostic; any HTTP middleware, logger, or side-car works).
2. **Exporter ships records**: A lightweight log exporter forwards those records to Elasticsearch in near real-time.
3. **Flywheel API pulls data**: Workers query Elasticsearch to build *evaluation* and *fine-tune* splits automatically.

```mermaid
sequenceDiagram
    participant App as Application

    box Flywheel
        participant ES as Log store
        participant API as Flywheel API
        participant Worker as Worker
    end

    box NMP
        participant datastore as Datastore
        participant dms as DMS
        participant customizer as Customizer
        participant eval as Evaluator
    end

    App->>ES: Log usage data
    API->>Worker: Start evaluation job
    Worker <<->> ES: Pull data
    Worker ->> datastore: Store eval and<br>FT datasets

    loop For each NIM
        Worker ->> dms: Spin up NIM
        Worker ->> customizer: Fine tune NIM

        Worker->> eval: Base evaluation
        Worker->> eval: ICL evaluation
        Worker->> eval: FT eval

        Worker->>API: Work
    end
    API->>App: Notify of new model
```

## Deployment Architecture

```mermaid
flowchart TD

    subgraph ex["Example Application<br>e.g. AIVA"]
        subgraph AIVA
            agent["Agent Node"]
            LLM
            Exporter

            agent --> LLM
            agent --> Exporter
        end

        subgraph loader_script["load_data.py (Unified Loader)"]
            script_es["ES client"]
        end
    end

    style ex fill:#ddddff

    script_es --> log_store
    Exporter --> log_store

    subgraph Blueprint["docker compose"]
        api["API"]
        workers["Workers"]
        log_store["Elasticsearch"]
        queue["Queue"]
        database["Database"]
    end

    subgraph k8s["K8s cluster"]
        nmp["NeMo microservices"]
    end

    workers --> nmp

    style Blueprint fill:#efe

    admin["Admin app<br>(e.g. notebook)"] --> api
```

## Automatic Resource Cleanup

The Data Flywheel Blueprint includes an **automatic cleanup system** that ensures proper resource management when the system is shut down unexpectedly or when workers are terminated. This prevents resource leaks and ensures clean system state.

### How Automatic Cleanup Works

The `CleanupManager` automatically activates during worker shutdown and performs the following operations:

1. **Detects running resources**: Finds all flywheel runs with `PENDING` or `RUNNING` status
2. **Identifies active NIMs**: Locates all NVIDIA Inference Microservices with `RUNNING` deployment status
3. **Cancels running jobs**: 
   - Cancels active customization jobs through NeMo Customizer
4. **Shuts down deployments**: 
   - Stops all running NIM deployments via NeMo Deployment Manager
   - Shuts down local LLM judge deployments (remote judges are unaffected)
5. **Updates database state**: Marks all resources as `CANCELLED` with appropriate timestamps and error messages

### When Automatic Cleanup Triggers

The cleanup manager activates automatically in these scenarios:

- **Worker shutdown**: When Celery workers receive shutdown signals (SIGTERM, SIGINT)
- **Container termination**: When Docker containers are stopped, triggering Celery worker shutdown
- **System restart**: During planned or unplanned system restarts

### Safety Features

- **Database-driven**: Only cleans up resources marked as running in the database
- **Error resilience**: Continues cleanup even if individual operations fail
- **Comprehensive logging**: Records all cleanup actions and errors for debugging
