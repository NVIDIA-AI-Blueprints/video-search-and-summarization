<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VIA Engine (Video Intelligence Analytics) is a long video summarization system that processes video content using Vision-Language Models (VLMs). It provides REST APIs for video analysis, summarization, and question-answering across multiple VLM backends including VILA, OpenAI-compatible models, and custom models.

## Build and Development Commands

### Docker Build
```bash
# Build base image and VIA image
make -C docker build

# Build test image (includes test dependencies)
make -C docker build_test_image
```

### Running the Application

**Development Mode** (code mounted from host):
```bash
# Start container with code mounted (requires .env file in repo root)
make -C docker start

# Start in interactive mode (container runs in daemon, opens shell)
make -C docker start INTERACTIVE=1
# Then inside container:
./start_via.sh
```

**Release Mode** (testing packaged container):
```bash
make -C docker start MODE=release
```

**Production Deployment** (using docker compose):
```bash
# Set environment variables or create .env file (see README.md for required vars)
docker compose -f docker/deploy/compose.yaml up

# If .env is not in current directory:
docker compose -f docker/deploy/compose.yaml --env-file=<path/to/.env> up
```

### Linting

```bash
# Check code style and formatting
flake8 src/ tests/

# Auto-format a file
black <file>

# Organize imports
# (Use VSCode "Organize Imports" or run isort via CLI if available)
```

Linting configuration:
- `.flake8`: flake8 rules (max line length 110, ignores E203, W503)
- `pyproject.toml`: black (line length 100) and isort (black profile)

### Testing

```bash
# Enter container in interactive mode
make -C docker start INTERACTIVE=1

# Run all tests
pytest tests/ -vv -s

# Run specific test
pytest tests/ -vv -s -k <test-name>

# Run with coverage
coverage run -m pytest tests/ -vv -s
coverage report -m
```

**Required environment variables for tests:**
- `NGC_API_KEY`
- `OPENAI_API_KEY`
- `NVIDIA_API_KEY`
- `VIA_VLM_API_KEY` (set to same as OPENAI_API_KEY)
- `HF_TOKEN`

### CLI Client

```bash
# Add file
python3 src/via_client_cli.py add-file <file_path> --backend <backend_url>

# Summarize video
python3 src/via_client_cli.py summarize --url <url> --model vila-1.5

# Summarize with custom prompt
python3 src/via_client_cli.py summarize --url <url> --model vila-1.5 --prompt <prompt>

# Use existing file-id
python3 src/via_client_cli.py summarize --id <file-id> --model vila-1.5

# Get help
python3 src/via_client_cli.py -h
```

## Architecture

### Core Components

**REST API Layer** (`src/via_server.py`):
- FastAPI-based REST server implementing the VIA API
- Routes: health checks, file management (RTVI proxy), video summarization, stream captioning/summarization, VLM captions, metrics
- Translates HTTP requests to ViaStreamHandler method calls
- API versioning controlled by `VSS_API_ENABLE_VERSIONING` env var (adds `/v1` prefix)
- `/files` and `/generate_vlm_captions` are dev-only routes behind `VIA_DEV_API=true`
- `POST /summarize` handles file-based summarization only
- `POST /v1/generate_captions` and `POST /v1/stream_summarize` are the dedicated livestream APIs

**Stream Handler** (`src/via_stream_handler.py`):
- Core orchestration layer managing video processing workflows
- Handles file-based summarization (SSE caption consumption + CA-RAG aggregation)
- Handles livestream captioning (`start_stream_captions` — fire-and-forget to RTVI)
- Handles livestream summarization (`summarize_stream` — reads captions from DB via CA-RAG)
- Coordinates between RTVI-VLM client and Context-Aware RAG
- Manages request lifecycle: queuing, processing, completion

**RTVI-VLM Client** (`src/rtvi_vlm_client.py`):
- HTTP client for the RTVI-VLM microservice
- Handles health checks, model info, file upload/delete, caption generation (streaming SSE)
- Configured via `RTVI_VLM_URL` environment variable

**VLM Pipeline** (`src/vlm_pipeline/`):
- `vlm_types.py`: Data types (`VlmChunkResponse`, `VlmRequestParams`, `VlmModelInfo`)
- `__init__.py`: Re-exports from vlm_types
- RTVI is the sole VLM transport; `_build_chunk_response` in `rtvi_vlm_server.py`
  is the source of truth for per-chunk timing fields.

### Configuration System

**Context-Aware RAG** (`config/config.yaml`):
- YAML-based configuration with environment variable substitution
- Defines tools: vector DB (Milvus), Elasticsearch, LLM, embeddings
- Configures functions: summarization, structured inference
- Database backend selection: `LVS_DATABASE_BACKEND` (vector_db or elasticsearch_db)

### Key Data Flow

**File Summarization** (`POST /summarize`):
1. Client sends request with `url`, `model`
2. ViaStreamHandler sends request to RTVI-VLM via SSE streaming
   - RTVI downloads video, chunks it, extracts frames, runs VLM inference
   - Returns per-chunk captions via SSE events
3. CA-RAG aggregates captions using LLM
4. Response: structured output with timestamps, captions, and aggregated summary

**Livestream Summarization** (two-phase dedicated APIs):

*Phase 1 — `POST /v1/generate_captions` (fire-and-forget):*
1. Client sends stream ID (from RTVI `stream/add`), model, chunk_duration, scenario/events
2. `ViaStreamHandler.start_stream_captions()` builds VLM prompt and calls `RtviVlmClient.start_captions()`
3. RTVI-VLM receives the request, starts captioning in background, publishes raw_events to Kafka
4. VIA immediately returns `{id, status: "accepted", model}` — does not wait for captions

*Phase 2 — `POST /v1/stream_summarize` (synchronous):*
1. Client sends stream ID, model, optional `start_time`/`end_time` window
2. `ViaStreamHandler.summarize_stream()` dispatches based on `KAFKA_ENABLED`:
   - **`KAFKA_ENABLED=false`**: `_summarize_stream_online()` calls CA-RAG `summarization_online` which reads captions from **Milvus** by UUID + time window
   - **`KAFKA_ENABLED=true`**: `_summarize_stream_kafka()` calls CA-RAG `summarization` which reads captions from **Elasticsearch** (populated by Kafka → Logstash → ES pipeline), then publishes `structured_events` + `aggregated_summary` back to Kafka
3. Response: `CompletionResponse` with `object: "summarization.completion"`, choices, and usage

Note: Livestream summarization uses the dedicated two-phase APIs above, not `POST /summarize`.

### Docker Architecture

**Multi-stage build**:
1. Base image (`docker/base/Dockerfile`): Dependencies only (apt + Python packages)
2. VIA image (`docker/Dockerfile`): Application code on top of base

**Development vs Release**:
- Dev: Source mounted from `VIA_SRC_DIR`, runs `src/via_server.py`
- Release: Code packaged in image at `/opt/nvidia/via`, runs `via-engine/via_server.py`

**Key startup script** (`start_via.sh`):
- Sources `.env`, validates `BACKEND_PORT`
- Launches VIA server with `--port` and `--ca-rag-config`

### Dependencies

**Python packages**: Managed in `docker/base/py_deps/` via uv (see pyproject.toml + uv.lock)

**APT packages**: Listed in `docker/base/requirements_apt.txt`

**Key dependencies**:
- FastAPI + Uvicorn for REST API
- vss-ctx-rag for Context-Aware RAG (installed from NVIDIA PyPI)
- Milvus/Elasticsearch for vector storage
- OpenTelemetry for tracing

## Development Patterns

### Adding New Source Files

When adding new Python files to be included in release containers, update `docker/package_file_list.txt`.

### Environment Variables

VIA uses extensive environment variable configuration. Key patterns:
- Boolean flags use lowercase strings: `"true"`, `"false"`
- Optional features controlled by `DISABLE_*` or `ENABLE_*` flags
- `RTVI_VLM_URL`: URL of the RTVI-VLM backend (required)
- `VIA_DEV_API=true`: Enables `/files` and `/generate_vlm_captions` dev routes
- Database backends selected via `LVS_DATABASE_BACKEND`
- `KAFKA_ENABLED`: Enables Kafka integration for livestream and file paths
- `LVS_CAPTION_SOURCE`: Controls caption source for file-path Kafka aggregation (`sse` default, or `db` for Elasticsearch)

### Logging

Uses custom logger (`src/via_logger.py`) with:
- Standard Python logging levels
- Custom `LOG_PERF_LEVEL` for performance metrics
- `TimeMeasure` context manager for timing code blocks
- Logs written to `/tmp/via-logs/` by default (configurable via `VIA_LOG_DIR`)

### Metrics

Prometheus metrics exposed at `/metrics` endpoint (see `via_server.py`).

## MCP (Model Context Protocol) Server

VIA includes an optional MCP server that exposes the same functionality as the REST API through MCP tools.

Enable with:
```bash
export LVS_ENABLE_MCP=true
export LVS_MCP_PORT=38112
```

MCP server runs on SSE transport at `http://<host>:<LVS_MCP_PORT>/sse` and provides tools for:
- Health checks (ready/live)
- Model listing
- Video summarization
- VLM caption generation
- Configuration recommendations
- Metrics retrieval

Implementation in `src/lvs_mcp.py`.
