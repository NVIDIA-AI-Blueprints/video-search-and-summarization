# CLAUDE.md - AI Assistant Guide for Video Search and Summarization

## Repository Overview

**Project:** NVIDIA AI Blueprint: Video Search and Summarization (VSS)
**Version:** 2.4.0
**License:** NVIDIA Proprietary (SPDX-License-Identifier: LicenseRef-NvidiaProprietary)
**Purpose:** Enterprise-grade AI-powered video analytics platform for video search, summarization, and question-answering using Vision Language Models (VLMs), Large Language Models (LLMs), and Context-Aware Retrieval-Augmented Generation (CA-RAG)

### Key Capabilities
- Video ingestion and processing (file upload and RTSP live streams)
- Dense video captioning using VLMs (Cosmos Nemotron, VILA)
- Video summarization and Q&A via CA-RAG (Vector + Graph RAG)
- Real-time alert detection and review
- Multi-stream concurrent processing
- Audio transcription integration (Riva ASR)
- Computer Vision pipeline with object detection

### Target Platforms
- x86-64 (Ubuntu 22.04)
- NVIDIA Jetson Thor (ARM)
- NVIDIA DGX Spark
- Cloud deployments (Kubernetes/Helm)

---

## Codebase Structure

### Directory Layout

```
video-search-and-summarization/
├── src/                          # Core application source code
│   ├── vss-engine/              # Main VSS application (FastAPI backend)
│   └── video_timeline/          # Gradio custom component for UI
├── deploy/                      # Deployment configurations
│   ├── docker/                  # Docker Compose deployments (5 topologies)
│   ├── helm/                    # Kubernetes Helm charts
│   └── scripts/                 # Deployment utilities
├── examples/                    # Training notebooks and code samples
│   ├── training_notebooks/      # Lab 1 (basics) & Lab 2 (advanced)
│   ├── code_examples/           # Structured output, multi-stream
│   └── cv-event-detector/       # Real-time event detection example
├── eval/                        # Evaluation framework (BYOV - Bring Your Own Videos)
│   ├── byov/                    # Test harness for accuracy evaluation
│   └── scripts/                 # Report generation utilities
├── perf-benchmark/              # Performance benchmarking tools
├── README.md                    # User-facing documentation
├── SECURITY.md                  # Security information
└── LICENSE*                     # License files
```

### Core Module Organization

#### `src/vss-engine/` (Main Application)

**Critical Files:**
- `src/via_server.py` (2,371 lines) - FastAPI REST API server, main entry point
- `src/via_stream_handler.py` (3,569 lines) - Core orchestrator for video processing pipeline
- `src/vss_api_models.py` (2,094 lines) - Pydantic models for API validation
- `src/asset_manager.py` (582 lines) - Video file and asset management
- `src/via_client_cli.py` (1,696 lines) - CLI interface
- `start_via.sh` - Application startup script

**Sub-modules:**
```
src/
├── vlm_pipeline/                # Vision Language Model processing
│   ├── vlm_pipeline.py          # VLM inference orchestrator
│   ├── video_file_frame_getter.py  # Frame extraction
│   ├── embedding_helper.py      # Embedding generation
│   └── ngc_model_downloader.py  # NGC model management
├── cv_pipeline/                 # Computer Vision pipeline
│   ├── cv_pipeline.py           # CV processing orchestrator
│   ├── gsam_pipeline_trt_ds.py  # Grounded SAM + DeepStream
│   └── cv_metadata_fuser.py     # Metadata fusion
├── trt_inference/               # TensorRT inference engine
│   ├── trt_inferencer.py        # TensorRT wrapper
│   └── gdino_inferencer.py      # Grounded DINO detector
├── client/                      # Client applications
│   ├── summarization.py         # Gradio summarization UI
│   ├── rtsp_stream.py           # RTSP stream handler
│   └── ui_utils.py              # UI utilities
├── models/                      # VLM model implementations
│   ├── openai_compat/           # OpenAI-compatible API wrapper
│   ├── cosmos_reason1/          # Cosmos Reason1 VLM
│   ├── vila15/                  # VILA 1.5 implementation
│   ├── nvila/                   # NVIDIA VILA
│   └── custom/                  # Custom model support
└── utils.py                     # Shared utilities
```

**Configuration:**
```
config/
├── config.yaml                  # Main CA-RAG configuration
├── guardrails/                  # NVIDIA NeMo Guardrails configs
├── riva_asr_grpc_conf.yaml     # Audio transcription settings
├── runtime_stats.yaml           # Performance metrics config
└── overrides/                   # Environment-specific overrides
```

---

## Development Workflows

### 1. Local Development Setup

**Prerequisites:**
- Ubuntu 22.04 (x86) or compatible platform
- NVIDIA driver 580.65.06+ (minimum)
- CUDA 13.0+
- Docker 27.5.1+ with Docker Compose 2.32.4
- NVIDIA Container Toolkit 1.13.5+
- NGC API key for model downloads
- GPU: See README.md hardware requirements table

**Environment Variables:**
Create `.env` file in deployment directory:
```bash
# Required
NVIDIA_API_KEY=your_nvidia_api_key
NGC_API_KEY=your_ngc_api_key
BACKEND_PORT=8000
FRONTEND_PORT=9100

# Model Configuration
VLM_MODEL_TO_USE=openai-compat  # or cosmos-reason1, vila15, nvila
VLM_BATCH_SIZE=8
NUM_VLM_PROCS=1

# Feature Toggles
DISABLE_CA_RAG=false
DISABLE_CV_PIPELINE=true  # CV disabled by default
DISABLE_GUARDRAILS=false
DISABLE_FRONTEND=false
ENABLE_AUDIO=false
ENABLE_DENSE_CAPTION=true

# Database Configuration
MILVUS_DB_HOST=milvus-standalone
GRAPH_DB_HOST=graph-db
GRAPH_DB_USERNAME=neo4j
GRAPH_DB_PASSWORD=your_password

# GPU Allocation
NVIDIA_VISIBLE_DEVICES=all  # or specific GPU IDs (0,1,2)
```

### 2. Docker Compose Deployment

**Available Topologies:**
1. `deploy/docker/local_deployment/` - All models local (8+ GPUs)
2. `deploy/docker/local_deployment_single_gpu/` - Single GPU mode (Llama 3.1 8B)
3. `deploy/docker/remote_vlm_deployment/` - Only VLM local
4. `deploy/docker/remote_llm_deployment/` - Only LLM remote
5. `deploy/docker/launchables/` - Cloud/CSP deployment

**Deployment Steps:**
```bash
cd deploy/docker/local_deployment/  # Choose topology
cp .env.sample .env                 # Copy and edit .env
docker compose up -d                # Start services
docker compose logs -f via-server   # Monitor logs
```

**Service Architecture:**
- `via-server`: Main VSS application (port 8000/9100)
- `milvus-standalone`: Vector database (port 19530)
- `graph-db` (Neo4j): Graph database (port 7474/7687)
- `arango-db` (ArangoDB): Alternative graph store (optional)

### 3. Building from Source

**Docker Build:**
```bash
export NGC_API_KEY=your_key
docker login nvcr.io
cd src/vss-engine/docker/
DOCKER_BUILDKIT=1 docker build \
  --build-arg BASE_IMAGE=nvcr.io/nvidia/blueprint/vss-engine-base:2.4.0 \
  -t vss-engine:2.4.0 \
  -f Dockerfile ..
```

**Component Build (video_timeline):**
```bash
cd src/video_timeline/
pip install build hatch
python -m build
# Output: dist/gradio_videotimeline-1.0.2-py3-none-any.whl
```

### 4. Running Tests and Evaluation

**Evaluation Framework (BYOV):**
```bash
cd eval/
make up        # Start graph database
make byov      # Run evaluation tests
make logs      # View logs
make down      # Stop services
```

**Performance Benchmarking:**
```bash
cd perf-benchmark/
pip install -r requirements.txt
python vss_perf_benchmark.py --config benchmark_scenarios.yaml
```

### 5. Development with Source Mounting

For rapid development without rebuilding:
```bash
export VIA_SRC_DIR=/path/to/video-search-and-summarization/src/vss-engine
docker compose up -d
# Changes to Python files are reflected immediately
```

---

## Coding Conventions

### File Headers

**All Python files must include NVIDIA license header:**
```python
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
```

**Deployment configs use Apache 2.0:**
```yaml
# SPDX-License-Identifier: Apache-2.0
```

### Python Style Guide

**Import Organization:**
```python
# Critical imports first (with isort:skip if needed)
from via_stream_handler import ViaStreamHandler  # isort:skip

# Standard library imports
import asyncio
import json
import os

# Third-party imports
import aiofiles
from fastapi import FastAPI
from pydantic import BaseModel

# Local application imports
from asset_manager import AssetManager
from utils import validate_required_prompts
```

**Pydantic Models:**
```python
from pydantic import BaseModel, ConfigDict, Field

class ViaBaseModel(BaseModel):
    """Base model that forbids extra fields"""
    model_config = ConfigDict(extra="forbid")

class MyRequest(ViaBaseModel):
    field_name: str = Field(
        description="Field description",
        examples=["example_value"],
        max_length=128,
        pattern=r"^[A-Za-z0-9_]*$"
    )
```

**Async/Await Patterns:**
```python
# Prefer async file I/O
import aiofiles
import aiofiles.os

async def read_file(path: str) -> str:
    async with aiofiles.open(path, "r") as f:
        return await f.read()

async def process_video(file_path: str):
    # Use asyncio for concurrent operations
    tasks = [
        process_vlm(file_path),
        process_cv(file_path),
        extract_audio(file_path)
    ]
    results = await asyncio.gather(*tasks)
```

**Error Handling:**
```python
from via_exception import ViaException
from fastapi import HTTPException

# Use ViaException for internal errors
raise ViaException("Descriptive error message")

# Use HTTPException for API errors
raise HTTPException(status_code=400, detail="Invalid request")
```

**Logging:**
```python
from via_logger import logger, TimeMeasure

logger.info("Informational message")
logger.error(f"Error processing file: {error}")

# Performance measurement
with TimeMeasure("operation_name"):
    perform_expensive_operation()
```

### Configuration Patterns

**YAML Configuration:**
```yaml
# Use snake_case for keys
database_config:
  host: localhost
  port: 19530
  collection_name: video_embeddings

# Environment variable substitution
model_path: ${MODEL_PATH:-/default/path}

# Feature toggles
features:
  enable_audio: false
  enable_cv_pipeline: true
```

**Environment Variable Naming:**
- Uppercase with underscores: `NVIDIA_API_KEY`
- Prefix with module: `VLM_MODEL_TO_USE`, `RIVA_ASR_SERVER_URI`
- Boolean flags: `DISABLE_CA_RAG`, `ENABLE_AUDIO`

---

## Key Components and Architecture

### 1. Video Processing Pipeline

**Ingestion Flow:**
```
Video Input (File/RTSP)
    ↓
Stream Handler (via_stream_handler.py)
    ↓
Video Chunking (file_splitter.py)
    ↓
Parallel Processing:
    ├─→ VLM Pipeline (frame extraction → VLM inference → captions)
    ├─→ CV Pipeline (object detection → tracking → metadata)
    └─→ Audio Pipeline (transcription via Riva ASR)
    ↓
Embedding Generation (embedding_helper.py)
    ↓
Database Indexing:
    ├─→ Vector DB (Milvus)
    └─→ Graph DB (Neo4j/ArangoDB)
    ↓
Ready for Query (CA-RAG)
```

### 2. REST API Endpoints

**Core Endpoints (`via_server.py`):**

| Endpoint | Method | Purpose | Key Parameters |
|----------|--------|---------|----------------|
| `/files` | POST | Upload video file | `file`, `purpose`, `metadata` |
| `/files` | GET | List uploaded files | `purpose`, `limit`, `offset` |
| `/files/{file_id}` | DELETE | Delete file | `file_id` |
| `/summarize` | POST | Generate summary | `file_id`, `prompt`, `streaming` |
| `/chat/completions` | POST | Q&A with RAG | `messages`, `model`, `tools` |
| `/generate_vlm_captions` | POST | VLM caption generation | `file_id`, `prompt` |
| `/reviewAlert` | POST | Review detected alert | `stream_id`, `alert_id` |
| `/live-streams` | POST | Add RTSP stream | `url`, `stream_id`, `alert_config` |
| `/health` | GET | Health check | - |
| `/metrics` | GET | Prometheus metrics | - |

**Request Example:**
```python
# File upload
files = {"file": open("video.mp4", "rb")}
data = {"purpose": "assistants", "metadata": json.dumps({"key": "value"})}
response = requests.post("http://localhost:8000/files", files=files, data=data)

# Summarization (streaming)
payload = {
    "file_id": "uuid-here",
    "prompt": "Provide a detailed summary of this video",
    "streaming": True
}
response = requests.post("http://localhost:8000/summarize", json=payload, stream=True)
```

### 3. Context-Aware RAG (CA-RAG)

**Configuration:** `config/config.yaml`

**Key Sections:**
```yaml
tools:
  chat_llm:
    api_url: "https://integrate.api.nvidia.com/v1"
    model_name: "meta/llama-3.1-70b-instruct"

  embedding:
    model_name: "nvidia/llama-3.2-nv-embedqa-1b-v2"

  reranker:
    model_name: "nvidia/llama-3.2-nv-rerankqa-1b-v2"

  vector_db:
    type: milvus
    host: milvus-standalone
    port: 19530

  graph_db:
    type: neo4j  # or arangodb
    host: graph-db
    username: neo4j
    password: password
```

**Chat Workflow:**
```
User Query
    ↓
Context Manager (short-term memory: chat history)
    ↓
Retrieval:
    ├─→ Vector DB (semantic similarity search)
    └─→ Graph DB (temporal/relational reasoning)
    ↓
Reranker (relevance scoring)
    ↓
LLM Generation (with retrieved context)
    ↓
Response with Citations
```

### 4. Model Integration

**VLM Models Supported:**
- `openai-compat`: OpenAI-compatible API (default for remote)
- `cosmos-reason1`: NVIDIA Cosmos Nemotron Reason1 7B
- `vila15`: VILA 1.5 (LLaVA-based)
- `nvila`: NVIDIA VILA (advanced features)
- `custom`: Custom model implementations

**Model Selection:**
```bash
# Environment variable
VLM_MODEL_TO_USE=cosmos-reason1

# For local models, also set:
MODEL_PATH=/path/to/model
TRT_ENGINE_PATH=/path/to/trt/engine  # For TRT-LLM acceleration
```

**Adding Custom Models:**
1. Create new directory: `src/vss-engine/src/models/custom/my_model/`
2. Implement `VLMPipeline` interface
3. Register in model factory
4. Update configuration

### 5. Guardrails Integration

**Configuration:** `config/guardrails/*.yml`

**NVIDIA NeMo Guardrails:**
- Input validation (jailbreak detection, PII filtering)
- Output filtering (hallucination detection, fact-checking)
- Topical rails (restrict to video analysis domain)

**Disable in Development:**
```bash
DISABLE_GUARDRAILS=true
```

---

## Configuration Management

### Hierarchical Configuration System

1. **Base Configuration:** `src/vss-engine/config/config.yaml`
2. **Deployment Overrides:** `deploy/docker/*/config.yaml`
3. **Runtime Overrides:** `config/overrides/*.yaml`
4. **Environment Variables:** Highest priority

### Key Configuration Files

#### `config.yaml` Structure
```yaml
# CA-RAG tools configuration
tools:
  chat_llm:           # LLM for Q&A
  summarization_llm:  # LLM for summarization
  notification_llm:   # LLM for alerts
  embedding:          # Embedding model
  reranker:           # Reranking model
  vector_db:          # Milvus configuration
  graph_db:           # Neo4j/ArangoDB

# Prompts
system_prompts:
  summarization:
    user_prompt: "..."
    system_prompt: "..."

# Ingestion settings
ingestion:
  batch_size: 10
  enable_cv_metadata: true
  enable_audio_transcript: false

# Chat/Q&A configuration
chat:
  context_window: 4096
  max_retrieved_chunks: 10
```

#### Environment-Specific Overrides

**Single GPU Mode:**
```yaml
# config/overrides/single_gpu.yaml
tools:
  chat_llm:
    model_name: "meta/llama-3.1-8b-instruct"  # Smaller model

vlm_pipeline:
  batch_size: 1
  num_processes: 1
```

**CV Pipeline Enabled:**
```yaml
# config/overrides/cv_accuracy_mode.yaml
ingestion:
  enable_cv_metadata: true
  cv_prompt: "person . car . truck"
  gdino_inference_interval: 30  # frames
```

### Feature Toggles via Environment

```bash
# Disable components
DISABLE_CA_RAG=true          # Disable RAG (captions only)
DISABLE_CV_PIPELINE=true     # Disable object detection
DISABLE_GUARDRAILS=true      # Disable safety rails
DISABLE_FRONTEND=true        # API-only mode
DISABLE_AUDIO=true           # No transcription

# Enable features
ENABLE_DENSE_CAPTION=true    # Detailed VLM captions
ENABLE_AUDIO=true            # Riva ASR transcription
FORCE_CA_RAG_RESET=true      # Reset databases on startup
```

---

## Testing and Validation

### 1. Accuracy Evaluation (BYOV)

**Location:** `eval/byov/`

**Test Configuration:** `byov_config.yaml`
```yaml
vss_url: "http://via-server:8000"
vlm_model: "nvila"  # or vila15, custom, remote

test_videos:
  - video_path: "/data/warehouse.mp4"
    ground_truth_summary: "json_gts/warehouse_ground_truth_summary.json"
    ground_truth_qa: "json_gts/warehouse_ground_truth_qa.json"
    ground_truth_dc: "json_gts/warehouse_ground_truth_dc.json"
```

**Running Tests:**
```bash
cd eval/
make byov

# Or manually:
docker compose run --rm -it byov
# Inside container:
pytest test_byov.py -v
```

**Metrics Computed:**
- BLEU score (summary quality)
- ROUGE score (summary coverage)
- Semantic similarity (embedding-based)
- Per-video and aggregate statistics

**Report Generation:**
```bash
python scripts/get_summary_qa_results_into_xlsx.py \
  --input results.json \
  --output report.xlsx
```

### 2. Performance Benchmarking

**Location:** `perf-benchmark/`

**Benchmark Modes:**
```yaml
# vss_perf_benchmark_config.yaml
scenarios:
  - name: single_file_workflow
    mode: single_file
    file_path: "/data/test.mp4"
    iterations: 10

  - name: file_burst_throughput
    mode: file_burst
    num_files: 20
    latency_target_ms: 5000

  - name: max_live_streams
    mode: live_streams
    stream_url: "rtsp://example.com/stream"
    max_streams: 50
```

**Running Benchmarks:**
```bash
python vss_perf_benchmark.py \
  --config benchmark_scenarios.yaml \
  --output results/
```

**Output:**
- `results_TIMESTAMP.xlsx` - Detailed metrics per scenario
- `results_TIMESTAMP.json` - Raw data
- Metrics: E2E latency, VLM/LLM pipeline times, P50/P90/P95/P99

### 3. Health Checks

**Endpoint:** `GET /health`

**Validation:**
```bash
curl http://localhost:8000/health
# Expected: {"status": "healthy"}
```

**Internal Checks (`via_health_eval.py`):**
- GPU availability and CUDA version
- Model availability (NGC downloads)
- Database connectivity (Milvus, Neo4j)
- Decoder availability (GStreamer codecs)
- Disk space and memory

---

## Deployment Procedures

### 1. Production Deployment (Helm)

**Prerequisites:**
- Kubernetes 1.31.2+
- NVIDIA GPU Operator v23.9+
- Helm 3.x
- NGC image pull secret

**Deployment:**
```bash
# Create NGC secret
kubectl create secret docker-registry ngc-docker-reg-secret \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password=$NGC_API_KEY

# Install chart
helm install vss-blueprint deploy/helm/nvidia-blueprint-vss-2.4.0.tgz \
  --set global.ngcImagePullSecretName=ngc-docker-reg-secret \
  --set viaServer.env.NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -f my-values.yaml

# Monitor
kubectl logs -f deployment/vss-blueprint-via-server
```

**Custom Values (`my-values.yaml`):**
```yaml
viaServer:
  replicas: 3
  resources:
    limits:
      nvidia.com/gpu: 2
  env:
    VLM_MODEL_TO_USE: "openai-compat"
    DISABLE_CV_PIPELINE: "true"

milvus:
  persistence:
    enabled: true
    size: 100Gi

neo4j:
  persistence:
    enabled: true
    size: 50Gi
```

### 2. Cloud Deployment (Launchables)

**Brev + Crusoe:**
```bash
# Use provided notebook
deploy/1_Deploy_VSS_docker_Crusoe.ipynb

# Or manually:
cd deploy/docker/launchables/
docker compose up -d
```

**Ephemeral Storage Handling:**
- All data in `/tmp/` or mounted volumes
- Regular backups to persistent storage
- Use external object storage for video files

### 3. Edge Deployment (Jetson Thor / DGX Spark)

**Platform-Specific Setup:**
- See `docs.nvidia.com/vss/latest/content/prereqs_thor.html`
- Reduced model sizes (quantization, LoRA)
- Optimized for lower power consumption

**Configuration Adjustments:**
```yaml
# Use smaller models
tools:
  chat_llm:
    model_name: "meta/llama-3.1-8b-instruct"

vlm_pipeline:
  batch_size: 1
  input_resolution: [336, 336]  # Smaller resolution
```

### 4. Multi-GPU Configuration

**GPU Allocation:**
```bash
# Use specific GPUs
NVIDIA_VISIBLE_DEVICES=0,1,2,3

# Distribute workload
NUM_GPUS=4
NUM_VLM_PROCS=2              # VLM processes across GPUs
NUM_CV_CHUNKS_PER_GPU=4      # CV pipeline chunks
```

**Compose Configuration:**
```yaml
services:
  via-server:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 4
              capabilities: [gpu]
```

---

## Common Tasks and Patterns

### 1. Adding a New VLM Model

**Step 1:** Create model implementation
```python
# src/vss-engine/src/models/custom/my_vlm/my_vlm_pipeline.py
from models.common.base_vlm import VLMPipeline

class MyVLMPipeline(VLMPipeline):
    def __init__(self, config):
        super().__init__(config)
        # Initialize model

    async def generate_caption(self, frames, prompt):
        # Implement caption generation
        return caption
```

**Step 2:** Register in factory
```python
# src/vss-engine/src/vlm_pipeline/vlm_pipeline.py
VLM_MODEL_MAP = {
    "openai-compat": OpenAICompatVLM,
    "cosmos-reason1": CosmosReason1VLM,
    "my_vlm": MyVLMPipeline,  # Add here
}
```

**Step 3:** Configure
```bash
VLM_MODEL_TO_USE=my_vlm
MODEL_PATH=/path/to/model/weights
```

### 2. Customizing Summarization Prompts

**Edit:** `config/config.yaml`
```yaml
system_prompts:
  summarization:
    user_prompt: |
      You are a video analysis expert. Analyze the following video content and provide:
      1. A concise overview (2-3 sentences)
      2. Key events in chronological order
      3. Notable objects or people
      4. Any anomalies or important observations

      Video content:
      {context}

    system_prompt: |
      Provide factual, objective analysis. Do not speculate beyond what is visible.
      Use timestamps when referencing events.
```

**Apply Changes:**
```bash
# Mount custom config
export CA_RAG_CONFIG=/path/to/custom/config.yaml
docker compose up -d
```

### 3. Implementing Custom Alert Logic

**Location:** `examples/cv-event-detector/`

**Example Alert Configuration:**
```yaml
# via_tracker_config.yml
alerts:
  - name: "Person in Restricted Area"
    trigger:
      object_class: "person"
      zone: "restricted_zone_1"
      duration_seconds: 5

  - name: "Unattended Object"
    trigger:
      object_class: "bag"
      stationary: true
      duration_seconds: 30
```

**Callback Implementation:**
```python
# Custom callback endpoint
@app.post("/alert_callback")
async def handle_alert(alert: AlertInfo):
    logger.info(f"Alert: {alert.alert_text}")
    # Send notification, log to SIEM, etc.
    return {"status": "acknowledged"}
```

### 4. Structured Output Extraction

**Pattern:** `examples/code_examples/structured_output/`

**Use Case:** Extract structured data from video (e.g., form filling)

```python
from pydantic import BaseModel

class IncidentReport(BaseModel):
    date: str
    time: str
    location: str
    description: str
    people_involved: List[str]

# Query with structured output
response = await vss_client.chat_completion(
    messages=[{"role": "user", "content": "Extract incident details from video"}],
    response_format={"type": "json_object", "schema": IncidentReport.model_json_schema()}
)

report = IncidentReport.parse_raw(response.choices[0].message.content)
```

### 5. Multi-Stream Processing

**Configuration:**
```python
streams = [
    {
        "stream_id": "camera_1",
        "url": "rtsp://192.168.1.10/stream1",
        "alert_config": {
            "enabled": True,
            "prompt": "Alert if person detected in Zone A"
        }
    },
    {
        "stream_id": "camera_2",
        "url": "rtsp://192.168.1.11/stream1",
    }
]

for stream in streams:
    response = requests.post(
        "http://localhost:8000/live-streams",
        json=stream
    )
```

**Monitoring:**
```bash
# Check all active streams
curl http://localhost:8000/live-streams

# Review alerts
curl http://localhost:8000/alerts?stream_id=camera_1
```

### 6. Database Management

**Reset Vector Database:**
```bash
FORCE_CA_RAG_RESET=true docker compose up -d
```

**Backup Milvus:**
```bash
docker compose exec via-server bash
milvus-backup create --backup-name backup_$(date +%Y%m%d)
```

**Export Graph Data (Neo4j):**
```bash
docker compose exec graph-db cypher-shell -u neo4j -p password \
  "MATCH (n) RETURN n LIMIT 100"
```

---

## Troubleshooting and Best Practices

### Common Issues

#### 1. GPU Out of Memory (OOM)

**Symptoms:**
```
CUDA out of memory. Tried to allocate X MiB
```

**Solutions:**
```bash
# Reduce batch size
VLM_BATCH_SIZE=4  # Default: 8

# Reduce number of processes
NUM_VLM_PROCS=1

# Use smaller model
VLM_MODEL_TO_USE=openai-compat  # Use remote inference

# Reduce input resolution
VLM_INPUT_WIDTH=336
VLM_INPUT_HEIGHT=336

# Single GPU mode
cd deploy/docker/local_deployment_single_gpu/
```

#### 2. Slow Video Processing

**Check Performance:**
```bash
# View metrics
curl http://localhost:8000/metrics | grep vlm_pipeline

# Enable performance logging
VSS_LOG_LEVEL=DEBUG
```

**Optimizations:**
```bash
# Increase parallelism
NUM_VLM_PROCS=4

# Use TensorRT acceleration
TRT_LLM_MODE=fp16
TRT_ENGINE_PATH=/path/to/engines

# Reduce frame sampling
# Edit config.yaml:
ingestion:
  frames_per_chunk: 10  # Default: 30
```

#### 3. Database Connection Errors

**Milvus Connection Failed:**
```bash
# Check service status
docker compose ps milvus-standalone

# Check logs
docker compose logs milvus-standalone

# Reset database
docker compose down -v  # WARNING: Deletes data
docker compose up -d
```

**Neo4j Authentication:**
```bash
# Ensure credentials match
GRAPH_DB_USERNAME=neo4j
GRAPH_DB_PASSWORD=your_password

# Reset Neo4j password
docker compose exec graph-db cypher-shell -u neo4j -p neo4j
# Then: ALTER USER neo4j SET PASSWORD 'new_password';
```

#### 4. Model Download Failures

**NGC Download Errors:**
```bash
# Verify NGC API key
echo $NGC_API_KEY

# Manual download
ngc registry model download-version nvidia/models:my-model:1.0

# Mount pre-downloaded models
MODEL_PATH=/path/to/local/model
```

#### 5. Video Codec Issues

**GStreamer Decoder Errors:**
```bash
# Install proprietary codecs (H.264, H.265)
INSTALL_PROPRIETARY_CODECS=true

# Force software decoder for AV1
FORCE_SW_AV1_DECODER=true

# Check available decoders
docker compose exec via-server bash
gst-inspect-1.0 | grep decoder
```

### Best Practices

#### Code Modifications

1. **Always test with small videos first** (< 1 minute)
2. **Use source mounting for development** (avoid rebuilds)
3. **Check logs frequently:**
   ```bash
   docker compose logs -f --tail=100 via-server
   ```
4. **Monitor GPU usage:**
   ```bash
   watch -n 1 nvidia-smi
   ```

#### Configuration Management

1. **Version control .env files** (exclude secrets)
2. **Use override files for local changes:**
   ```yaml
   # docker-compose.override.yml (not tracked in git)
   services:
     via-server:
       volumes:
         - ./local_config.yaml:/opt/nvidia/via/config.yaml
   ```
3. **Document environment variables in .env.sample**

#### Performance Optimization

1. **Profile before optimizing:**
   ```bash
   python perf-benchmark/vss_perf_benchmark.py --mode single_file
   ```
2. **Use TensorRT for VLM inference** (10x speedup)
3. **Enable dense captions only when needed** (higher accuracy, slower)
4. **Disable CV pipeline if not using object detection**
5. **Use remote inference for non-GPU development machines**

#### Security

1. **Never commit API keys** (.env files in .gitignore)
2. **Use guardrails in production** (DISABLE_GUARDRAILS=false)
3. **Validate input files** (check file size, duration limits)
4. **Restrict RTSP stream sources** (firewall rules)
5. **Use HTTPS for API endpoints in production**

#### Database Hygiene

1. **Regular backups** (Milvus + Neo4j)
2. **Monitor disk usage** (video files accumulate)
3. **Clean old assets:**
   ```bash
   # Delete files older than 30 days
   find /tmp/assets -mtime +30 -delete
   ```
4. **Index maintenance** (reindex after major config changes)

---

## API Reference Quick Guide

### File Management

```python
# Upload
POST /files
  file: UploadFile
  purpose: "assistants"
  metadata: JSON

# List
GET /files?purpose=assistants&limit=10&offset=0

# Delete
DELETE /files/{file_id}
```

### Video Analysis

```python
# Summarization (streaming)
POST /summarize
  file_id: str
  prompt: str
  streaming: bool = True
  batch_size: int = 10

# Q&A (OpenAI compatible)
POST /chat/completions
  messages: List[Message]
  model: str = "default"
  tools: Optional[List[Tool]]
  stream: bool = False

# VLM Captions
POST /generate_vlm_captions
  file_id: str
  prompt: str = "Describe this video frame in detail"
  callback_url: Optional[str]
```

### Live Streaming

```python
# Add stream
POST /live-streams
  stream_id: str
  url: str (rtsp://)
  alert_config: AlertConfig

# List streams
GET /live-streams

# Stop stream
DELETE /live-streams/{stream_id}

# Review alert
POST /reviewAlert
  stream_id: str
  alert_id: str
  review_prompt: str
```

### Monitoring

```python
# Health check
GET /health
  -> {"status": "healthy"}

# Prometheus metrics
GET /metrics
  -> text/plain (Prometheus format)

# Recommended config
GET /recommended-config
  -> GPU recommendations, model suggestions
```

---

## Additional Resources

### Documentation
- Official Docs: https://docs.nvidia.com/vss/latest/
- API Catalog: https://build.nvidia.com/nvidia/video-search-and-summarization
- NGC Catalog: https://catalog.ngc.nvidia.com/

### Training
- Lab 1: `examples/training_notebooks/lab_1/01_VSS_Summarization_Tutorial.ipynb`
- Lab 2: `examples/training_notebooks/lab_2/02_VSS_QnA_Tutorial.ipynb`

### Support
- GitHub Issues: (for public release, TBD)
- Security: See SECURITY.md
- License: See LICENSE

---

## Version History

**Current Version: 2.4.0**

Key Changes:
- Cosmos Reason1 7B VLM support
- Enhanced CA-RAG with graph reasoning
- Multi-stream alert review
- Jetson Thor and DGX Spark support
- Performance optimizations (TRT-LLM)

For older versions, see git tags and release notes.

---

## AI Assistant Guidelines

When working on this codebase:

1. **Always check NVIDIA license headers** in new files
2. **Use Pydantic models** for API request/response validation
3. **Prefer async/await** for I/O operations
4. **Log performance metrics** with `TimeMeasure` context manager
5. **Test with small videos first** (< 1 min) before full-length
6. **Document environment variables** in compose.yaml and .env.sample
7. **Use feature toggles** (DISABLE_*) for conditional functionality
8. **Follow existing patterns** for model integration (see models/)
9. **Add guardrails** for user-facing features (NeMo Guardrails)
10. **Consider GPU memory** - profile before adding features

**Code Review Checklist:**
- [ ] License header present
- [ ] Pydantic models for new API endpoints
- [ ] Error handling with ViaException/HTTPException
- [ ] Logging at appropriate levels
- [ ] Async I/O where applicable
- [ ] Environment variables documented
- [ ] Configuration via YAML (not hardcoded)
- [ ] GPU memory impact assessed
- [ ] Tests added (if applicable)
- [ ] Documentation updated

---

**Last Updated:** 2025-11-16
**Blueprint Version:** 2.4.0
**Maintainer:** NVIDIA Corporation
