# LVS Server Standalone Docker Setup

This directory contains Docker and Docker Compose configurations for running the LVS (Long Video Summarization) Server as a standalone container.

## Files

- `docker-compose.yml` - Docker Compose configuration
- `docker-run-lvs-server3.sh` - Standalone docker run script (legacy)
- `config.yaml` - Application configuration file (mounted into container)
- `.env.lvs-server-standalone` - Environment variables (create this file)

## Quick Start with Docker Compose

### 1. Create Environment File

Create a `.env.lvs-server-standalone` file with your configuration:

```bash
# Container Configuration
CONTAINER_IMAGE=nvcr.io/nvidia/vss-core/vss-long-video-summarization:3.1.0
GPU_DEVICES=2,3

# Port Configuration
BACKEND_PORT=38111
LVS_MCP_PORT=38112

# Model Cache Directory (optional)
MODEL_ROOT_DIR=/path/to/model/cache

# Database Configuration - Milvus
MILVUS_DB_HOST=localhost
MILVUS_DB_GRPC_PORT=19530

# Database Configuration - Elasticsearch
ES_HOST=localhost
ES_PORT=9200

# Database Backend Selection (vector_db or elasticsearch_db)
LVS_DATABASE_BACKEND=vector_db

# LLM Configuration
LVS_LLM_MODEL_NAME=meta/llama-3.1-70b-instruct
LVS_LLM_BASE_URL=http://localhost:9233/v1
NVIDIA_API_KEY=nvapi-xxxxx

# Embedding Configuration
LVS_EMB_ENABLE=true
LVS_EMB_MODEL_NAME=nvidia/nv-embedqa-e5-v5
LVS_EMB_BASE_URL=http://localhost:9232/v1
```

### 2. Start the Service

```bash
docker compose up -d
```

### 3. View Logs

```bash
docker compose logs -f lvs-server
```

### 4. Stop the Service

```bash
docker compose down
```

## Configuration Details

### Config File Mounting

The `config.yaml` file is automatically mounted into the container at `/app/config.yaml`. The environment variable `CA_RAG_CONFIG_PATH=/app/config.yaml` is set to point to this location.

### Port Mappings

The following ports are exposed:
- `BACKEND_PORT` (default: 38111) - Backend API
- `LVS_MCP_PORT` (default: 38112) - LVS MCP service

### Model Cache Directory

If `MODEL_ROOT_DIR` is set in your `.env` file, that directory will be mounted into the container for model caching. This speeds up subsequent starts by avoiding re-downloading models.

## Network Configuration

By default, the compose file uses bridge networking to enable port mapping. If you need to use host networking instead:

1. Uncomment the `network_mode: host` line in `docker-compose.yml`
2. Comment out the `ports:` section (host mode ignores port mappings)

## Database Backends

The LVS server supports two database backends:

### Milvus (vector_db)
```bash
LVS_DATABASE_BACKEND=vector_db
MILVUS_DB_HOST=localhost
MILVUS_DB_GRPC_PORT=19530
```

### Elasticsearch
```bash
LVS_DATABASE_BACKEND=elasticsearch_db
ES_HOST=localhost
ES_PORT=9200
```

## Troubleshooting

### Container won't start
- Check GPU availability: `nvidia-smi`
- Verify environment file exists: `ls -la .env.lvs-server-standalone`
- Check logs: `docker compose logs lvs-server`

### Port conflicts
- Modify port values in `.env.lvs-server-standalone`
- Ensure ports are not already in use: `netstat -tuln | grep <port>`

### Configuration not loading
- Verify `config.yaml` exists in the same directory as `docker-compose.yml`
- Check that `CA_RAG_CONFIG_PATH` is set correctly in the container:
  ```bash
  docker compose exec lvs-server env | grep CA_RAG_CONFIG_PATH
  ```

## Alternative: Shell Script

You can also use the legacy shell script instead of Docker Compose:

```bash
./docker-run-lvs-server3.sh
```

This script provides the same functionality but uses `docker run` directly.

