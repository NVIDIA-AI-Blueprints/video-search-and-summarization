# VSS RTVI VLM Microservice

VSS RTVI VLM Microservice generates text or incidents for given live video stream input, sends the output in nvschema format over Kafka.
The video is segmented into chunks as per requested chunk duration and overlap intervals.
Frames are sampled and sent for VLM inference. Text output is generated at the end of inference.
If Yes/No questions are asked, it also generates incidents based on set prompts.

Default Docker Compose model: Cosmos Reason2 with `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8` (configurable; see [Model Configuration](#model-configuration)).

## Prerequisites
- **NGC API key** to download the base container and any NGC-hosted model.

### Validated GPUs

The RTVI VLM Microservice has been validated and tested on the following NVIDIA GPUs:

- NVIDIA H100
- NVIDIA RTX PRO 6000 Blackwell
- NVIDIA L40S
- NVIDIA DGX Spark
- NVIDIA IGX Thor
- NVIDIA AGX Thor

### Software Requirements
- **OS**: Ubuntu 24.04 or compatible Linux distribution (x86); DGX OS 7.4.0 (DGX Spark); Jetson Linux BSP Rel 38.4/38.5 (Jetson Thor)
- **Docker**: Version 28.2+ and earlier than 29.5.0
- **Docker Compose**: Version 2.36+
- **NVIDIA Driver**: 580+
- **NVIDIA Container Toolkit**: Latest version
- **Git LFS**: For large file handling

### Installation

- **Docker & Docker Compose**: Follow the [official Docker installation guide](https://docs.docker.com/engine/install/)
- **NVIDIA Container Toolkit**: Follow the [NVIDIA Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

## Quick Start

Clone the blueprint repository and use the deployment assets shipped with the RT-VLM service.

### Docker Compose Deployment

#### 1. Clone the repository and change into the Docker directory

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/services/rtvi/rt-vlm/docker
```

The Docker artifacts are shipped under [`docker/`](docker/):

| File | Purpose |
|------|---------|
| [`docker/compose.yaml`](docker/compose.yaml) | Standalone Compose stack: `rtvi-server` + Kafka + Redis. Common Compose/Helm variables are listed in [Docker Compose and Helm Variables](#docker-compose-and-helm-variables) |
| [`docker/Dockerfile`](docker/Dockerfile) | (Optional) layers your local `src/` edits onto the shipped image |

#### 2. Create a `.env` file

Create `.env` with your configuration:

```bash
cat > .env << EOF
BACKEND_PORT=8000
RTVI_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0
# For DGX Spark/SBSA platforms:
#RTVI_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0-sbsa
VLM_MODEL_TO_USE=cosmos-reason2
MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8
KAFKA_ENABLED=true
#KAFKA_BOOTSTRAP_SERVERS=<Kafka_server_ip:port>
KAFKA_TOPIC=mdx-vlm-captions
KAFKA_INCIDENT_TOPIC=mdx-vlm-incidents
NGC_API_KEY=nvapi-XXXXXX
VLM_BATCH_SIZE=128
NVIDIA_VISIBLE_DEVICES=0

# Omni audio support (set true for Nemotron Nano Omni and similar models)
#VLM_MODEL_SUPPORTS_AUDIO=false
#VLM_TRUST_REMOTE_CODE=false
#INSTALL_PROPRIETARY_CODECS=false
EOF
```

`compose.yaml` provides defaults for every other Compose variable except `BACKEND_PORT`, which must be set. See [Docker Compose and Helm Variables](#docker-compose-and-helm-variables) for variables common to the standalone Compose stack and Helm override.

#### 3. Start the service

```bash
docker compose up
```

The standalone Compose stack starts RT-VLM, Kafka, and Redis. To use a different Kafka server, set `KAFKA_BOOTSTRAP_SERVERS`. To use a different Redis server, set `REDIS_HOST`. Use `KAFKA_PORT` / `REDIS_HOST_PORT` to change the host-side port mappings if `9092` / `6379` are already taken.

**Troubleshooting:** If launch fails with an out-of-memory error, adjust `VLLM_GPU_MEMORY_UTILIZATION`, reduce `VLM_MAX_MODEL_LEN`, or set `NVIDIA_VISIBLE_DEVICES=<gpuid>` to a free GPU.

**Troubleshooting:** Workaround for `error from registry: Incorrect Repository Format` on `docker pull`.
Newer Docker versions may return `error from registry: Incorrect Repository Format`. This occurs on hosts using Docker's containerd image store. Disable the containerd snapshotter so Docker reverts to the classic `overlay2` image store.

Edit `/etc/docker/daemon.json` and add:
```json
"features": { "containerd-snapshotter": false }
```

Then restart Docker (this stops every running container — close any running tmux sessions first):
```bash
sudo systemctl restart docker
```

- Pros: Restores plain `docker pull` for private `nvcr.io` paths.
- Cons: Stops every running container. Images already in the containerd store are hidden until you switch back (they are not deleted).

The APIs are available at `http://<host_ip>:<BACKEND_PORT>/docs`.

### Source Code Updates and Custom Container Build

Use this workflow only when you have changed RT-VLM source files under [`src/`](src/) and need those changes inside the runtime container. Custom container testing is supported with the standalone Docker Compose deployment.

Build the custom image from the RT-VLM service directory:

```bash
cd video-search-and-summarization/services/rtvi/rt-vlm
docker build -f docker/Dockerfile -t <registry>/<repo>/vss-rt-vlm:3.2.0-custom .
```

To test the custom image with Docker Compose, set `RTVI_IMAGE` in `docker/.env`:

```bash
#RTVI_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0
RTVI_IMAGE=<registry>/<repo>/vss-rt-vlm:3.2.0-custom
```

Then restart the service:

```bash
cd docker
docker compose down
docker compose up
```

For DGX Spark/SBSA Docker Compose testing, build an ARM64/SBSA image from the `rt-vlm/` directory and load it into the local Docker image store. Export `IS_SBSA=true` on the host shell before the build command so the Dockerfile resolves the `-sbsa` base image automatically:

```bash
export IS_SBSA=true
docker buildx build --platform linux/arm64 \
  --build-arg IS_SBSA \
  -f docker/Dockerfile \
  -t <registry>/<repo>/vss-rt-vlm:3.2.0-custom-sbsa \
  --load .
```

For Jetson AGX Thor / IGX Thor (ARM64 but not SBSA), do **not** set `IS_SBSA`. The default base image (`nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0`) is multi-arch, so a `linux/arm64` build pulls the Thor-compatible arm64 variant automatically:

```bash
docker buildx build --platform linux/arm64 \
  -f docker/Dockerfile \
  -t <registry>/<repo>/vss-rt-vlm:3.2.0-custom-thor \
  --load .
```

### Standalone Helm Chart Deployment

Use the standalone Helm chart when running only RT-VLM on Kubernetes. The chart is in `deploy/helm/services/rtvi/charts/rtvi-vlm` in the `video-search-and-summarization` repository.

Prerequisites:
- Kubernetes cluster with NVIDIA GPU Operator installed
- Helm 3
- NGC image pull secret for `nvcr.io`
- Generic secret containing `NGC_API_KEY`
- Optional generic secret containing `HF_TOKEN` for Hugging Face-hosted models

#### 1. Clone the repository and change into the chart directory

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/services/rtvi/charts/rtvi-vlm
```

#### 2. Create the namespace and secrets

```bash
kubectl create namespace vss-rtvi
kubectl create secret docker-registry ngc-image-pull-secret \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password="$NGC_API_KEY" \
  -n vss-rtvi
kubectl create secret generic ngc-api \
  --from-literal=NGC_API_KEY="$NGC_API_KEY" \
  -n vss-rtvi

# Required when MODEL_PATH points to a gated Hugging Face checkpoint.
kubectl create secret generic hf-token-secret \
  --from-literal=HF_TOKEN="$HF_TOKEN" \
  -n vss-rtvi
```

#### 3. Install the chart with the standalone override

```bash
helm upgrade --install vss-rtvi-vlm . \
  -n vss-rtvi \
  -f overrides_rtvi_vlm.yaml
```

When using the `hf-token-secret` secret, set `hfTokenSecret.name=hf-token-secret` and `hfTokenSecret.key=HF_TOKEN` in your values file or with `--set`.

The standalone override sets `enabled=true`, `useSharedNim=false`, `modelPath=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8`, disables Kafka publishing with `KAFKA_ENABLED=false`, and uses loopback placeholders for Kafka and Redis.

#### 4. Expose the API for local testing

```bash
kubectl port-forward -n vss-rtvi svc/vss-rtvi-vlm 8000:8000
```

The API is available at `http://localhost:8000/docs`.

Common service variables and chart values are listed in [Docker Compose and Helm Variables](#docker-compose-and-helm-variables).

## Supported Models

RT-VLM supports local vLLM-compatible checkpoints, NGC model artifacts, and remote OpenAI-compatible endpoints. Use `MODEL_PATH=git:<Hugging Face URL>` for Hugging Face checkpoints, `MODEL_PATH=ngc:<org>/<team>/<model>:<version>` for NGC model artifacts, or `VLM_MODEL_TO_USE=openai-compat` with `VIA_VLM_ENDPOINT` for a remote endpoint.

### Cosmos Reason2 Family (Default)

| Model or checkpoint | Example selector |
|---------------------|------------------|
| Cosmos Reason2 8B, `0303-fp8-dynamic-kv8` | `VLM_MODEL_TO_USE=cosmos-reason2`, `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8` |
| [Cosmos Reason2 8B, hf-0303](https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/cosmos-reason2-8b?version=hf-0303) | `VLM_MODEL_TO_USE=cosmos-reason2`, `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-0303` |
| [Cosmos Reason2 8B, 0303-fp4-dynamic-kv8](https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/cosmos-reason2-8b?version=0303-fp4-dynamic-kv8) | `VLM_MODEL_TO_USE=cosmos-reason2`, `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp4-dynamic-kv8`. Do not use this FP4/NVFP4 variant on GB200. |

### Cosmos3 Family

| Model or checkpoint | Example selector |
|---------------------|------------------|
| [Cosmos3 Nano Reasoner, modelopt-nvfp4-full-quantize](https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/cosmos3-nano-reasoner/files?version=modelopt-nvfp4-full-quantize-final_format_fix) | `VLM_MODEL_TO_USE=cosmos-reason3`, `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix` |
| [Cosmos3 Nano Reasoner, modelopt-fp8-full-quantize](https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/cosmos3-nano-reasoner/files?version=modelopt-fp8-final_format_fix) | `VLM_MODEL_TO_USE=cosmos-reason3`, `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix` |

### Nemotron Omni Family

| Model or checkpoint | Example selector |
|---------------------|------------------|
| [Nemotron-3-Nano-Omni-30B-A3B-Reasoning](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning) | `VLM_MODEL_TO_USE=vllm-compatible`, `VLM_TRUST_REMOTE_CODE=true`, `VLM_MODEL_SUPPORTS_AUDIO=true` for audio |
| [Nemotron-Nano-V3-Omni-GA0420-FP8](https://huggingface.co/nvidia/Nemotron-Nano-V3-Omni-GA0420-FP8) | `VLM_MODEL_TO_USE=vllm-compatible`, `VLM_TRUST_REMOTE_CODE=true`, `VLM_MODEL_SUPPORTS_AUDIO=true` for audio |

### Qwen Family

| Model or checkpoint | Example selector |
|---------------------|------------------|
| [Qwen3-VL-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct) | `VLM_MODEL_TO_USE=vllm-compatible`, `MODEL_PATH=git:https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct` |
| [Qwen3-Omni-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct) | `VLM_MODEL_TO_USE=vllm-compatible`, `MODEL_PATH=git:https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct` |
| [Qwen3.5-27B](https://huggingface.co/Qwen/Qwen3.5-27B) | `VLM_MODEL_TO_USE=vllm-compatible`, `MODEL_PATH=git:https://huggingface.co/Qwen/Qwen3.5-27B` |

### Cosmos Reason1

| Model or checkpoint | Example selector |
|---------------------|------------------|
| [Cosmos Reason1 7B, 1.1-fp8-dynamic](https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/cosmos-reason1-7b?version=1.1-fp8-dynamic) | `VLM_MODEL_TO_USE=cosmos-reason1`, `MODEL_PATH=ngc:nim/nvidia/cosmos-reason1-7b:1.1-fp8-dynamic` |

### Test Client and Commands

Download the Python client file: [rtvi_client_cli.py](src/cli/rtvi_client_cli.py)

#### Create virtual environment
```bash
sudo apt install python3-venv
mkdir ~/python_venv
python3 -m venv  ~/python_venv
source ~/python_venv/bin/activate
pip install sseclient-py requests tabulate tqdm pyyaml protobuf
```

#### Use Python client to generate captions for video stream

```bash
LIVE_STREAM="rtsp://camera.example.com:554/stream"
BACKEND="http://<host_ip>:<backend_port>"
MODEL=$(curl $BACKEND/v1/models | jq -r '.data[0].id')

# Add live stream with sensor name
STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream \
   $LIVE_STREAM \
  --description "Camera 1" \
  --place-name "Main Warehouse Entrance" \
  --place-type "warehouse-bay" \
  --place-lat 37.3706 \
  --place-lon -121.9672 \
  --place-alt 10.5 \
  --place-coordinate-x 25.0 \
  --place-coordinate-y 8.5 \
  --sensor-name "Camera_123" \
  --backend $BACKEND | grep -oP 'id: \K[^,]+') &&\
   [ -n "$STREAM_ID" ] && echo "Stream added successfully. Stream ID: $STREAM_ID" ||\
   { echo "Error: Failed to add live stream. Please check the stream URL and backend connection.";  }

# Trigger VLM generation request for video stream
python3 rtvi_client_cli.py generate-captions \
    --chunk-duration 60 --chunk-overlap-duration -10  \
    --prompt "You are a warehouse monitoring system. Describe the events in this warehouse and look for any anomalies. Start each sentence with start and end timestamp of the event." \
    --system-prompt "Answer the user's question correctly" \
     --file-start-offset 0 \
     --model-temperature 0.4 \
     --model-top-p 1 \
     --model-top-k 100 \
     --model-max-tokens 512 \
     --model-seed 1 \
     --response-format json_object \
     --num-frames-per-second-or-fixed-frames-chunk 0.05  \
     --use-fps-for-chunking  \
     --vlm-input-width 0 \
     --vlm-input-height 0 \
     --model  $MODEL \
     --backend $BACKEND \
     --stream \
     --id  $STREAM_ID

# To stop the generate-captions request from another terminal, set the appropriate stream ID
python3 rtvi_client_cli.py stop-live-stream-processing $STREAM_ID \
    --backend $BACKEND

# To delete the above live stream
python3 rtvi_client_cli.py delete-live-stream $STREAM_ID --backend $BACKEND

# To list available live streams
python3 rtvi_client_cli.py list-live-streams --backend $BACKEND

# To print the curl request, append --print at the end of the command
python3 rtvi_client_cli.py list-live-streams --backend $BACKEND --print

```

### Kafka Consumer

Download [test_kafka_consumer.py](tests/kafka/test_kafka_consumer.py),
[ext_pb2.py](src/server/protos/ext_pb2.py) and [nv_pb2.py](src/server/protos/nv_pb2.py)

```bash
source ~/python_venv/bin/activate

python3 test_kafka_consumer.py \
    --topic mdx-vlm-captions \
    --bootstrap-servers <host_ip>:9094 \
    --verbose
```

### Using Redis for Error Messages
By default, error messages are sent to Kafka. To use Redis instead, set the following environment variables in your `.env` file:

```bash
ENABLE_REDIS_ERROR_MESSAGES=true
REDIS_HOST=redis.example.com
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=your_password  # Optional, only if Redis requires authentication
ERROR_MESSAGE_TOPIC=vision-llm-errors  # Redis channel name for error messages
```

Error messages will be published to the Redis channel specified in `ERROR_MESSAGE_TOPIC`. The message format remains the same as Kafka (JSON with streamId, timestamp, type, source, event fields).

#### Enabling Redis Password Authentication

To enable password authentication for Redis in the Docker Compose deployment, modify the `compose.yaml` file:

1. **Update the Redis service command** to conditionally include `--requirepass` only when `REDIS_PASSWORD` is set:

```yaml
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"
  volumes:
    - redis-data:/data
  environment:
    REDIS_PASSWORD: ${REDIS_PASSWORD:-}
  command: >
    sh -c "
    if [ -n \"$${REDIS_PASSWORD}\" ]; then
      redis-server --appendonly yes --requirepass $${REDIS_PASSWORD}
    else
      redis-server --appendonly yes
    fi
    "
  healthcheck:
    test: >
      sh -c "
      if [ -n \"$${REDIS_PASSWORD}\" ]; then
        redis-cli --no-auth-warning -a $${REDIS_PASSWORD} ping
      else
        redis-cli ping
      fi
      "
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 10s
  restart: unless-stopped
```

2. **Set the password in your `.env` file**:

```bash
REDIS_PASSWORD=your_secure_password_here
```

**Note**: If `REDIS_PASSWORD` is not set in the `.env` file (or is empty), Redis will start with no password set (no authentication). For production deployments, always set a strong password.

#### Redis Error Consumer

To monitor error messages from Redis, download and use the Redis consumer script:

Download [test_redis_consumer.py](tests/redis/test_redis_consumer.py)

```bash
source ~/python_venv/bin/activate
pip install redis

# Subscribe to Redis error channel
python3 test_redis_consumer.py \
    --channel vision-llm-errors \
    --host <redis_host> \
    --port 6379 \
    --verbose
```

#### Testing Redis Error Messages

To send test error messages to Redis, download and use the publisher script:

Download [test_redis_publisher.py](tests/redis/test_redis_publisher.py)

```bash
# Send a test error message
python3 test_redis_publisher.py \
    --channel vision-llm-errors \
    --host <redis_host> \
    --port 6379 \
    --message "Test error message" \
    --type functional
```


## API Endpoints

RTVI VLM provides REST endpoints for media assets, live streams, caption generation, health checks, and OpenAI/NIM-compatible workflows. This overview mirrors the OpenAPI reference used by the VSS documentation.

### Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/metrics` | GET | Get Prometheus-format RTVI metrics |
| `/v1/ready` | GET | Get RTVI VLM Microservice readiness status |
| `/v1/live` | GET | Get RTVI VLM Microservice liveness status |
| `/v1/startup` | GET | Get RTVI VLM Microservice startup status |
| `/v1/assets/stats` | GET | Get asset storage statistics |
| `/v1/metadata` | GET | Get RTVI VLM Microservice metadata |
| `/v1/files` | POST | Upload a media file or register media by URL/path |
| `/v1/files` | GET | List uploaded files |
| `/v1/files/{file_id}` | DELETE | Delete a file |
| `/v1/files/{file_id}` | GET | Get file information |
| `/v1/files/{file_id}/content` | GET | Get file content |
| `/v1/streams/add` | POST | Add one or more live streams |
| `/v1/streams/get-stream-info` | GET | List live streams |
| `/v1/streams/delete/{stream_id}` | DELETE | Remove a live stream |
| `/v1/streams/delete-batch` | DELETE | Remove multiple live streams |
| `/v1/stream/add` | POST | Add a stream using the CV-compatible format |
| `/v1/stream/remove` | POST | Remove a stream using the CV-compatible format |
| `/v1/stream/get-stream-info` | GET | List streams using the CV-compatible format |
| `/v1/models` | GET | List available models |
| `/v1/generate_captions` | POST | Generate VLM captions and audio transcripts |
| `/v1/generate_captions/{stream_id}` | DELETE | Stop live stream caption generation |
| `/v1/chat/completions` | POST | OpenAI-compatible chat completion endpoint |
| `/v1/completions` | POST | OpenAI-compatible completions endpoint |
| `/v1/version` | GET | Get release and API versions |
| `/v1/license` | GET | Get license information |
| `/v1/manifest` | GET | Get service manifest information |
| `/v1/health/live` | GET | NIM-compatible liveness check |
| `/v1/health/ready` | GET | NIM-compatible readiness check |

### Text-Only Chat (No Video/Image Required)

The `/v1/chat/completions` endpoint supports text-only conversations without any video or image input. Simply omit the `id` field and do not include `video_url`/`image_url` in messages. Multi-turn conversation history (system/user/assistant roles) is fully supported with token-level SSE streaming.

```bash
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')

# Text-only non-streaming
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "What is NVIDIA?"}]
  }'

# Text-only with streaming
curl -N -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "What is NVIDIA?"}],
    "stream": true
  }'

# Multi-turn conversation
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is CUDA?"},
      {"role": "assistant", "content": "CUDA is a parallel computing platform by NVIDIA."},
      {"role": "user", "content": "What GPUs support it?"}
    ]
  }'
```

CLI usage (text-only — omit `--id`):
```bash
python3 rtvi_client_cli.py chat-completions \
  --model $MODEL \
  --messages "user:What is NVIDIA?" \
  --stream \
  --backend $BACKEND

# Multi-turn
python3 rtvi_client_cli.py chat-completions \
  --model $MODEL \
  --messages "system:You are a helpful assistant." "user:What is CUDA?" "assistant:CUDA is a parallel computing platform." "user:What GPUs support it?" \
  --backend $BACKEND
```

### Chat Completions Usage

The `/v1/chat/completions` endpoint supports two modes for providing video/image input.

All examples below use the `$MODEL` shell variable — set it once with:

```bash
BACKEND="http://<host_ip>:<backend_port>"
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')
```

#### Option 1: Direct Video URL (OpenAI Multimodal Format)

Pass `video_url` or `image_url` directly in the message content - no pre-upload required:

```bash
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "What is in this video?"},
          {"type": "video_url", "video_url": {"url": "https://example.com/video.mp4"}}
        ]
      }
    ],
    "max_tokens": 256
  }'
```

You can also use `image_url` for images:

```bash
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Describe this image."},
          {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
        ]
      }
    ],
    "max_tokens": 256
  }'
```

#### Option 2: Pre-uploaded File ID

First upload a video, then reference it by ID:

```bash
# Upload a video
FILE_ID=$(python3 rtvi_client_cli.py add-file /path/to/video.mp4 --backend $BACKEND | grep -oP 'id: \K[^,]+')

# Upload a video with sensor name (optional)
FILE_ID=$(python3 rtvi_client_cli.py add-file /path/to/video.mp4 --sensor-name "Camera_123" --backend $BACKEND | grep -oP 'id: \K[^,]+')

# Non-streaming request
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "Describe what you see in this video."}],
    "id": "'$FILE_ID'",
    "stream": false
  }'

# Streaming request
curl -X POST "$BACKEND/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "Describe the events in this video."}],
    "id": "'$FILE_ID'",
    "stream": true
  }'
```

### Using CLI for NIM Endpoints

```bash
# Get version
python3 rtvi_client_cli.py get-version --backend $BACKEND

# Get license
python3 rtvi_client_cli.py get-license --backend $BACKEND

# Get manifest
python3 rtvi_client_cli.py get-manifest --backend $BACKEND

# Chat completions (non-streaming)
python3 rtvi_client_cli.py chat-completions \
  --id $FILE_ID \
  --model $MODEL \
  --messages "user:Describe what you see in this video." \
  --backend $BACKEND

# Chat completions (streaming)
python3 rtvi_client_cli.py chat-completions \
  --id $FILE_ID \
  --model $MODEL \
  --messages "user:Describe this video." \
  --stream \
  --backend $BACKEND
```

### RTSP Live Stream Support

The NIM endpoints fully support RTSP live streams. When processing live streams:
- `chunk_duration` is automatically set to 60 seconds if not provided
- Streaming responses show real-time processing with ping keep-alive messages

```bash
# Add RTSP stream with sensor name
STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream \
  "rtsp://camera.example.com:554/stream" \
  --description "Security Camera 1" \
  --sensor-name "Camera_123" \
  --backend $BACKEND | grep -oP 'id: \K[^,]+')

# Process with chat completions
python3 rtvi_client_cli.py chat-completions \
  --id $STREAM_ID \
  --model $MODEL \
  --messages "user:Monitor this camera feed for any unusual activity." \
  --stream \
  --backend $BACKEND
```

### Sensor Name Feature

The RTVI VLM service supports associating a user-defined sensor name with uploaded files. The sensor name is used to identify the source sensor/camera in Kafka messages and can be used for filtering and routing messages.

#### Adding Files with Sensor Name

When uploading files using the CLI, you can specify a sensor name:

```bash
# Upload a file with sensor name
python3 rtvi_client_cli.py add-file /path/to/video.mp4 \
  --sensor-name "Camera_123" \
  --backend $BACKEND

# Upload a file without sensor name (sensor_name defaults to empty string)
python3 rtvi_client_cli.py add-file /path/to/video.mp4 \
  --backend $BACKEND
```

#### Adding Live Streams with Sensor Name

When adding live streams using the CLI, you can also specify a sensor name:

```bash
# Add live stream with sensor name
python3 rtvi_client_cli.py add-live-stream \
  "rtsp://camera.example.com:554/stream" \
  --description "Security Camera 1" \
  --sensor-name "Camera_123" \
  --backend $BACKEND

# Add live stream without sensor name (sensor_name defaults to empty string)
python3 rtvi_client_cli.py add-live-stream \
  "rtsp://camera.example.com:554/stream" \
  --description "Security Camera 1" \
  --backend $BACKEND
```

#### Sensor Name Usage in Kafka Messages

When a sensor name is provided (for both files and live streams):
- The `sensor_id` field in Kafka messages (VisionLLM protocol buffer) will be set to the sensor name
- If sensor name is not provided or empty, `sensor_id` falls back to the stream ID or asset ID
- This allows downstream consumers to filter and route messages based on sensor identity

### Frame Selection Modes
RTVI VLM supports two frame selection modes for sampling frames from video chunks:

**FPS-based Selection:**
- Enable `--use-fps-for-chunking` flag
- Set `--num-frames-per-second-or-fixed-frames-chunk` to the desired frames per second (e.g., `0.05` for 0.05 FPS)
- The system will sample frames at the specified rate based on chunk duration

**Fixed Frame Selection (default):**
- Do not set `--use-fps-for-chunking` flag, this flag is disabled by default
- Set `--num-frames-per-second-or-fixed-frames-chunk` to the desired number of frames per chunk (e.g., `8` for 8 frames)
- The system will sample a fixed number of equally-spaced frames from each chunk


### EVS (Efficient Video Sampling)

EVS prunes redundant video tokens at the vLLM engine level to reduce computation while retaining caption accuracy. This significantly speeds up inference for video-heavy workloads.

**Supported models:** Nemotron Nano VL, Qwen 2.5 VL (not supported on Cosmos Reason1/2)

**Enable EVS:**
```bash
# In .env — prune 50% of redundant video tokens
VLM_VIDEO_PRUNING_RATE=0.5
```

Set to `0` or remove to disable (default). Valid range: greater than 0.0 and less than 1.0.

**Performance impact (Nemotron Nano 12B VL, 30s chunk, 30 frames):**

| Metric | EVS=0 (disabled) | EVS=0.5 | Reduction |
|--------|:---:|:---:|:---:|
| Prompt tokens/chunk | 8193 | 4353 | 47% |
| Inference time | 26.7s | 14.2s | 47% faster |

**Testing EVS with Nemotron Nano 12B VL:**

1. Set `.env`:
```bash
VLM_MODEL_TO_USE=vllm-compatible
MODEL_PATH=ngc:nim/nvidia/nemotron-nano-12b-v2-vl:nvfp4-refresh
VLM_TRUST_REMOTE_CODE=true
VLM_VIDEO_PRUNING_RATE=0.5
```

2. Start the service:
```bash
cd docker && docker compose up -d
```

3. Verify EVS is enabled in the logs:
```bash
docker logs rtvi-vlm 2>&1 | grep "EVS"
# Expected: EVS enabled: video_pruning_rate=0.50
```

4. Run inference:
```bash
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')
FILE_ID=$(curl -s -X POST $BACKEND/v1/files \
  -F "file=@video.mp4" -F "purpose=vision" -F "media_type=video" | jq -r '.id')

curl -s -X POST $BACKEND/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "Describe this video."}],
    "id": "'"$FILE_ID"'",
    "chunk_duration": 30,
    "max_tokens": 256
  }' | jq '.choices[0].message.content'
```

5. Check token counts in server logs:
```bash
docker logs rtvi-vlm 2>&1 | grep "VLM result:" | tail -1
# Shows total_prompt_tokens (text+visual) — compare with/without EVS
```

### Disabling Multimodal Preprocessor Cache

By default, the multimodal preprocessor cache is disabled (`VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true`) to avoid a system memory leak. To re-enable caching (trades higher memory for lower latency):

```bash
# In .env
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=false
```

When disabled (default), each request reprocesses video frames from scratch. This uses less memory but increases latency.

### Troubleshooting System Memory Growth

If host system memory or container RSS grows over time during long-running workloads, especially with many distinct videos or streams, keep the multimodal preprocessor cache disabled.

Docker Compose `.env`:

```bash
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true
```

Helm chart `env` values:

```yaml
env:
  - name: VLLM_DISABLE_MM_PREPROCESSOR_CACHE
    value: "true"
```

Restart the service after changing these values. Re-enable the multimodal preprocessor cache only when you have validated memory behavior for your workload.

### Remote NIM Video Input (REMOTE_VIDEO_INPUT)

When using `openai-compat` with a remote NIM endpoint, `REMOTE_VIDEO_INPUT` defaults to `true` and encodes multi-frame chunks as a single MP4 video instead of individual JPEG images. This avoids the NIM "at most 10 images" limit. Set `REMOTE_VIDEO_INPUT=false` to restore image-per-frame payloads.

```bash
# Optional: disable MP4 video payloads
REMOTE_VIDEO_INPUT=false
```

**Image fallback**: If MP4 encoding fails (e.g., raw tensors instead of JPEG-encoded frames), the system automatically falls back to sending up to 10 uniformly sampled base64 JPEG images. A warning is logged:
```
MP4 encoding failed for chunk 0 — sending 10/50 frames as images
```

## Environment Variables Reference

The shared list below covers variables common to the standalone Docker Compose stack and standalone Helm override.

### Docker Compose and Helm Variables

The table lists variables in the standalone Docker Compose stack and the standalone Helm override.

| Variable | Description | Standalone default |
|----------|-------------|--------------------|
| `MODEL_PATH` (Helm: `modelPath`) | Model source | `ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8` |
| `MODEL_IMPLEMENTATION_PATH` | Custom model implementation path | Empty |
| `NGC_API_KEY` | NGC API key | Compose: Empty; Helm: `ngc-api/NGC_API_KEY` secret |
| `HF_TOKEN` | Hugging Face token | Compose: Empty; Helm: `hf-token-secret/HF_TOKEN` secret |
| `NVIDIA_API_KEY` | NVIDIA API key for hosted endpoints | `NOAPIKEYSET` |
| `NVIDIA_VISIBLE_DEVICES` | GPU device IDs exposed to the container | `all` |
| `OPENAI_API_KEY` | OpenAI-compatible API key | `NOAPIKEYSET` |
| `OPENAI_API_VERSION` | Azure OpenAI API version | Empty |
| `VIA_VLM_API_KEY` | OpenAI-compatible VLM API key | Compose: Empty; Helm: `ngc-api/NGC_API_KEY` secret |
| `VLM_MODEL_TO_USE` | Backend selector | `cosmos-reason2` |
| `VLM_BATCH_SIZE` | VLM inference batch size | Empty |
| `NUM_VLM_PROCS` | Number of VLM inference processes | Empty |
| `NUM_GPUS` | Number of GPUs to use | Compose: Empty; Helm: `1` |
| `VSS_NUM_GPUS_PER_VLM_PROC` | Number of GPUs per VLM process | Empty |
| `VLM_INPUT_WIDTH` | Input frame width | Empty |
| `VLM_INPUT_HEIGHT` | Input frame height | Empty |
| `VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK` | Frame sampling rate or fixed frames per chunk | `30` |
| `VLM_SYSTEM_PROMPT` | Default system prompt | Empty |
| `VLM_PROMPT_MAX_LENGTH` | Maximum user-prompt length in characters | `10240` |
| `RTVI_VLM_MAX_GENERATION_TOKENS` (Helm env: `VLM_MAX_GENERATION_TOKENS`) | Maximum generated tokens | `16384` |
| `VLM_MODEL_SUPPORTS_AUDIO` | Enable native audio support for Omni models | `false` |
| `VLM_TRUST_REMOTE_CODE` | Enable trust of model-supplied remote code | `false` |
| `INSTALL_PROPRIETARY_CODECS` | Install proprietary codecs at container startup | `false` |
| `FORCE_SW_AV1_DECODER` | Force software AV1 decode | Empty |
| `LOG_LEVEL` | Service logging verbosity | Compose: Empty; Helm: `INFO` |
| `RTVI_EXTRA_ARGS` | Additional RT-VLM runtime arguments | Empty |
| `RTVI_RTSP_LATENCY` | RTSP latency override | Empty |
| `RTVI_RTSP_TIMEOUT` | RTSP timeout override | Empty |
| `RTVI_RTSP_RECONNECTION_INTERVAL` | Time to wait between RTSP reconnection attempts | `5` |
| `RTVI_RTSP_RECONNECTION_WINDOW` | RTSP reconnection window in seconds | `60` |
| `RTVI_RTSP_RECONNECTION_MAX_ATTEMPTS` | Maximum RTSP reconnection attempts | `10` |
| `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY` | GStreamer jitterbuffer drop-on-latency setting | `false` |
| `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS` | GStreamer jitterbuffer fast-start packet threshold | `2` |
| `RTVI_ENABLE_LIVE_TIMESTAMP_FILTER` | Enable timestamp filtering for live streams | `false` |
| `RTVI_ENABLE_FILE_TIMESTAMP_FILTER` | Enable timestamp filtering for file streams | `true` |
| `RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT` | Add timestamp metadata to VLM prompts | Empty |
| `RTVI_EMPTY_CUDA_CACHE_ON_RESULT` | Empty CUDA cache after result handling | `false` |
| `RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC` | Blocking timeout for stream deletion cleanup | `300` |
| `RTVI_ENABLE_GOP_DECODE_OPT` | Enable GOP-aligned decode optimization | `true` |
| `VSS_SKIP_INPUT_MEDIA_VERIFICATION` | Skip input media validation | Empty |
| `VLLM_GPU_MEMORY_UTILIZATION` | vLLM GPU memory utilization fraction | Empty |
| `VLM_VIDEO_PRUNING_RATE` | Efficient Video Sampling pruning rate | Compose: Empty; Helm: `0.0` |
| `RTVI_VLLM_MOE_BACKEND` (Helm env: `VLLM_MOE_BACKEND`) | vLLM MoE backend override | Empty |
| `RTVI_VLLM_MM_PROCESSOR_CACHE_GB` (Helm env: `VLLM_MM_PROCESSOR_CACHE_GB`) | Multimodal processor cache size | `1` |
| `VLLM_MM_TENSOR_IPC` | vLLM multimodal tensor IPC setting | Empty |
| `VLLM_MULTIMODAL_TENSOR_IPC` | vLLM multimodal tensor IPC setting | Empty |
| `VLLM_MM_ENCODER_ATTN_BACKEND` | vLLM multimodal encoder attention backend | Empty |
| `VLLM_ROOT` | vLLM package root used by runtime patches | `/usr/local/lib/python3.12/dist-packages/vllm` |
| `VLLM_USE_NVFP4_CT_EMULATIONS` | Enable NVFP4 CT emulation | `0` |
| `KAFKA_ENABLED` | Enable Kafka publishing | Compose: `true`; Helm: `false` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka bootstrap servers | Compose: `kafka:9092`; Helm: `127.0.0.1:9092` |
| `KAFKA_TOPIC` | VisionLLM message topic | `mdx-vlm-captions` |
| `KAFKA_INCIDENT_TOPIC` | Incident topic | `mdx-vlm-incidents` |
| `RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE` (Helm env: `KAFKA_ASYNC_SEND_QUEUE_MAXSIZE`) | Bounded queue size for async Kafka sends | `1024` |
| `ERROR_MESSAGE_TOPIC` | Kafka topic or Redis channel for error messages | `vision-llm-errors` |
| `ENABLE_REDIS_ERROR_MESSAGES` | Publish errors to Redis instead of Kafka | `false` |
| `REDIS_HOST` | Redis host | Compose: `redis`; Helm: `127.0.0.1` |
| `REDIS_PORT` | Redis application port | `6379` |
| `REDIS_DB` | Redis database number | `0` |
| `ENABLE_OTEL_MONITORING` | Enable OpenTelemetry | `false` |
| `OTEL_RESOURCE_ATTRIBUTES` | OpenTelemetry resource attributes | Empty |
| `OTEL_TRACES_EXPORTER` | OpenTelemetry traces exporter | `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP endpoint | `http://otel-collector:4318` |
| `OTEL_METRIC_EXPORT_INTERVAL` | OpenTelemetry metric export interval in milliseconds | `60000` |

### Additional Helm Chart Values

These Kubernetes chart values are defined by the standalone RT-VLM chart under `deploy/helm/services/rtvi/charts/rtvi-vlm`. The chart injects `KAFKA_BOOTSTRAP_SERVERS`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `MODEL_PATH`, `NGC_API_KEY`, and `VIA_VLM_API_KEY` from top-level values or Kubernetes secrets. When `useSharedNim=true`, it also injects `VIA_VLM_ENDPOINT` and `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`.

#### Helm Values
| Value | Description | Default |
|-------|-------------|---------|
| `enabled` | Enable the RT-VLM chart | `false` in `values.yaml`, `true` in `overrides_rtvi_vlm.yaml` |
| `image.repository` | RT-VLM image repository | `nvcr.io/nvidia/vss-core/vss-rt-vlm` |
| `image.tag` | RT-VLM image tag | `3.2.0` |
| `image.pullPolicy` | Kubernetes image pull policy | `IfNotPresent` |
| `replicas` | Number of RT-VLM replicas | `1` |
| `useSharedNim` | Use an in-cluster or remote OpenAI-compatible NIM instead of loading the model in the RT-VLM pod | `false` |
| `modelPath` | Model path used when `useSharedNim=false` | Set by `overrides_rtvi_vlm.yaml` to `ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8` |
| `sharedNimService` | Shared NIM service name when `useSharedNim=true` | Empty |
| `sharedNimPort` | Shared NIM service port | `8000` |
| `vlmBaseUrl` | Remote VLM base URL when NIMs are disabled | Empty |
| `vlmName` | VLM model name override | Empty |
| `viaVlmOpenAiModelDeploymentName` | OpenAI-compatible deployment name override | Empty |
| `nims.enabled` | Whether the umbrella install uses in-cluster NIMs | `true` |
| `ngcApiSecret.name` | Kubernetes secret that contains the NGC API key | Empty, falls back to `global.ngcApiSecret.name` or `ngc-api` |
| `ngcApiSecret.key` | Secret key for the NGC API key | Empty, falls back to `global.ngcApiSecret.key` or `NGC_API_KEY` |
| `hfTokenSecret.name` | Optional Kubernetes secret that contains `HF_TOKEN` | `hf-token-secret` in `overrides_rtvi_vlm.yaml` |
| `hfTokenSecret.key` | Secret key for the Hugging Face token | `HF_TOKEN` |
| `global.imagePullSecrets` | Image pull secrets used by the pod | Empty in `values.yaml`; standalone override uses `ngc-image-pull-secret` |
| `global.ngcApiSecret.name` | Default NGC API key secret | Empty in `values.yaml`; standalone override uses `ngc-api` |
| `global.ngcApiSecret.key` | Default NGC API key secret key | Empty in `values.yaml`; standalone override uses `NGC_API_KEY` |
| `global.useReleaseNamePrefix` | Prefix service names with the Helm release name | `false` in standalone override |
| `kafkaBootstrapServers` | Kafka bootstrap servers injected into the pod | Empty, resolves to `kafka-kafka:9092`; standalone override uses `127.0.0.1:9092` |
| `waitForKafka.enabled` | Run an init container that waits for Kafka and topics | `true`; standalone override sets `false` |
| `waitForKafka.image.repository` | Kafka wait init container image repository | `confluentinc/cp-kafka` |
| `waitForKafka.image.tag` | Kafka wait init container image tag | `8.2.0` |
| `waitForKafka.imagePullPolicy` | Kafka wait init container pull policy | `IfNotPresent` |
| `waitForKafka.timeoutSeconds` | Kafka wait timeout | `1200` |
| `waitForKafka.topics` | Kafka topics to wait for | `mdx-vlm`, `mdx-vlm-incidents` |
| `redisHost` | Redis host injected into the pod | Empty, resolves to `redis`; standalone override uses `127.0.0.1` |
| `redisPort` | Redis port | `6379` |
| `redisDb` | Redis database | `0` |
| `redisPassword` | Redis password | Empty |
| `service.port` | Kubernetes service port | `8000` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `securityContext.runAsUser` | Pod user ID | `1001` |
| `securityContext.runAsGroup` | Pod group ID | `1001` |
| `shmSize` | `/dev/shm` memory volume size | `16Gi` |
| `resources.requests.nvidia.com/gpu` | Requested GPUs | `1` |
| `resources.limits.nvidia.com/gpu` | GPU limit | `1` |
| `env` | List of service environment variables | See the next table |
| `nodeSelector` | Kubernetes node selector | `{}` |
| `tolerations` | Kubernetes tolerations | `[]` |

### Enabling Incidents
To enable incidents, set appropriate `--prompt` or `--system-prompt` with a clear Yes/No expectation.
Incidents will be pushed on the incident Kafka topic.

```
--prompt "You are a warehouse monitoring system focused on safety and efficiency. Analyze the situation to detect any anomalies such as workers not wearing safety gear, leaving items unattended, or wasting time. Respond in the following structured format: Anomaly Detected: Yes/No Reason: [Brief explanation]" --system-prompt "Answer the user's question correctly with yes or no"
```

#### Alert Categorization (API-only)
You can specify a custom alert category for incidents using the `alert_category` field in API requests. This sets the `incident.category` field in Kafka messages, enabling better filtering and routing of alerts.

**Note:** This feature is currently available only via direct API calls (not in CLI).

Example using `/v1/generate_captions`. Set `$BACKEND`, `$MODEL`, and `$STREAM_ID` first as shown elsewhere in this README:

```bash
BACKEND="http://<host_ip>:<backend_port>"
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')
STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream \
  rtsp://<stream_url> \
  --description "Live stream" \
  --backend $BACKEND | grep -oP 'id: \K[^,]+')

curl -X POST "$BACKEND/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "'$STREAM_ID'",
    "model": "'$MODEL'",
    "prompt": "Detect if workers are wearing proper safety equipment.",
    "system_prompt": "Answer with Yes/No format",
    "alert_category": "Worker PPE Violation",
    "stream": true,
    "chunk_duration": 60
  }'
```

Common alert categories:
- `Worker PPE Violation` - Safety equipment compliance
- `Pathway Obstruction` - Blocked walkways or exits
- `Unauthorized Access` - Restricted area violations
- `Equipment Misuse` - Improper tool or machinery usage

If not specified, the default category `vlm-alert` is used.

### URL-Based Processing

Process video/images directly from a URL without uploading via `/v1/files`:

```bash
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')
REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')

# Process from HTTP URL
curl -N -X POST "$BACKEND/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "'"$REQUEST_ID"'",
    "url": "https://example.com/video.mp4",
    "media_type": "video",
    "creation_time": "2025-01-15T10:00:00Z",
    "enable_audio": true,
    "model": "'"$MODEL"'",
    "prompt": "Describe the scene",
    "chunk_duration": 10,
    "stream": true
  }'

# Process from local file (requires FILE_URL_ALLOWED_DIRS)
REQUEST_ID2=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -N -X POST "$BACKEND/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "'"$REQUEST_ID2"'",
    "url": "file:///data/videos/clip.mp4",
    "model": "'"$MODEL"'",
    "prompt": "Describe what you see",
    "stream": true
  }'
```

CLI flags for URL processing:
```bash
python3 rtvi_client_cli.py generate-captions \
  --url https://example.com/video.mp4 \
  --media-type video \
  --creation-time "2025-01-15T10:00:00Z" \
  --enable-audio \
  --model $MODEL \
  --prompt "Describe the scene" \
  --chunk-duration 10 \
  --stream \
  --id $(python3 -c 'import uuid; print(uuid.uuid4())') \
  --backend $BACKEND
```

**URL and media parameters:**
- `url` (str): Video/image URL — supports `http://`, `https://`, `s3://`, `file://`
- `media_type` (str): `video` (default) or `image`
- `creation_time` (str): ISO 8601 timestamp — offsets frame timestamps in the response
- `enable_audio` (bool): Enable audio transcription for the media (default: `false`). CLI flag: `--enable-audio`

**Security**: `file://` URLs require the `FILE_URL_ALLOWED_DIRS` environment variable to be set (see below).

**Response `chunk_id`**: Each chunk in `chunk_responses` includes a `chunk_id` field (zero-based index) for tracking chunk ordering.

**SSE streaming**: When `stream=true`, chunks are delivered incrementally as each completes VLM inference, rather than batching all results at the end.

### CV-Compatible Stream API

The `/v1/stream/add` and `/v1/stream/remove` endpoints provide a CV-compatible interface for managing video streams. These are designed for integration with VST (Video Storage Toolkit) and other CV pipeline services.

#### Add Stream with Auto-Inference

When `metadata` includes VLM inference parameters (`prompt`), inference starts automatically after the stream is added:

```bash
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')

curl -X POST "$BACKEND/v1/stream/add" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "sensor",
    "value": {
      "camera_id": "cam-001",
      "camera_url": "rtsp://camera.example.com:554/stream",
      "change": "camera_add",
      "metadata": {
        "prompt": "Describe what you see",
        "model": "'$MODEL'",
        "chunk_duration": 10,
        "stream": true
      }
    }
  }'
```

Response:
```json
{"camera_id": "cam-001", "asset_id": "uuid-...", "status": "processing", "inference": true}
```

#### Add Stream without Inference (Passthrough)

Omit `metadata` (or omit `prompt`) to add a stream without triggering VLM inference. You can then use `/v1/generate_captions` with the returned `asset_id` to start processing separately:

```bash
# Add stream only
ASSET_ID=$(curl -s -X POST "$BACKEND/v1/stream/add" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "sensor",
    "value": {
      "camera_id": "cam-002",
      "camera_url": "rtsp://camera.example.com:554/stream",
      "change": "camera_add"
    }
  }' | jq -r '.asset_id')

# Start inference separately with SSE streaming
curl -N -X POST "$BACKEND/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "'"$ASSET_ID"'",
    "model": "'"$MODEL"'",
    "prompt": "Describe what you see",
    "chunk_duration": 10,
    "stream": true
  }'
```

#### List Active Streams

```bash
curl "$BACKEND/v1/stream/get-stream-info"
```

Response:
```json
{
  "streams": [
    {"camera_id": "cam-001", "asset_id": "uuid-...", "camera_url": "rtsp://...", "inference_active": true}
  ],
  "stream_count": 1
}
```

#### Remove Stream

Removing a stream stops any active inference and cleans up the asset:

```bash
curl -X POST "$BACKEND/v1/stream/remove" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "sensor",
    "value": {
      "camera_id": "cam-001",
      "change": "camera_remove"
    }
  }'
```

Response:
```json
{"camera_id": "cam-001", "asset_id": "uuid-...", "status": "removed"}
```

#### CLI Commands

```bash
# Add stream with auto-inference
python3 rtvi_client_cli.py stream-add \
  --camera-url "rtsp://camera.example.com:554/stream" \
  --camera-id cam-001 \
  --prompt "Describe what you see" \
  --model $MODEL \
  --chunk-duration 10 \
  --backend $BACKEND

# List streams
python3 rtvi_client_cli.py stream-list --backend $BACKEND

# Remove stream
python3 rtvi_client_cli.py stream-remove \
  --camera-id cam-001 \
  --backend $BACKEND
```

**Note:** When using `stream-add` with inference parameters via CLI, the command tails SSE captions in real time. Press Ctrl+C to stop — the stream is automatically removed on disconnect.

#### Request/Response Schema

**StreamAddRequest:**
- `key` (str): Identifier key, typically `"sensor"`
- `value.camera_id` (str): Unique camera identifier
- `value.camera_url` (str): Stream URL (`rtsp://`, `file://`, `http://`, `https://`)
- `value.camera_name` (str, optional): Human-readable camera name
- `value.change` (str): Operation type — `"camera_add"` for adding
- `value.creation_time` (str, optional): ISO 8601 creation timestamp
- `value.metadata` (object, optional): VLM inference parameters — `prompt`, `model`, `chunk_duration`, `stream`, etc.
- `headers.source` (str, optional): Source identifier (e.g., `"vst"`)
- `headers.created_at` (str, optional): Request creation timestamp

**StreamRemoveRequest:**
- `key` (str): Identifier key
- `value.camera_id` (str): Camera ID to remove
- `value.change` (str): `"camera_remove"`

### Using remote endpoints with Nvidia NIM or GPT-4
```bash
VLM_MODEL_TO_USE=openai-compat
OPENAI_API_KEY=nvapi-XXXXXXX
VIA_VLM_ENDPOINT="https://integrate.api.nvidia.com/v1"
VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME="nvidia/nemotron-nano-12b-v2-vl"
```
For local deployments, `VIA_VLM_ENDPOINT` will change.

### Using gpt-4o
```bash
OPENAI_API_KEY=<openai key if using gpt-4o else optional>
VLM_MODEL_TO_USE=openai-compat
VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME="gpt-4o"
```

### Omni Native Audio Support

Omni models, including Nemotron Nano Omni (`NemotronH_Nano_VL_V2`), can process audio natively alongside video frames when `VLM_MODEL_SUPPORTS_AUDIO=true` is set.

`VLM_TRUST_REMOTE_CODE=true` is always required — the model ships custom tokenizer/processor code not included in the base `transformers` package.

#### .env configuration (video only)

```bash
VLM_MODEL_TO_USE=vllm-compatible
MODEL_PATH=/path/to/nemotron-nano-omni   # local HuggingFace-format model directory
VLM_TRUST_REMOTE_CODE=true               # always required
```

#### .env configuration (video + audio)

```bash
VLM_MODEL_TO_USE=vllm-compatible
MODEL_PATH=/path/to/nemotron-nano-omni
VLM_TRUST_REMOTE_CODE=true               # always required
VLM_MODEL_SUPPORTS_AUDIO=true            # enable native audio processing
INSTALL_PROPRIETARY_CODECS=true          # only needed for proprietary audio codecs
```

#### Helm configuration

For standalone Helm, set `modelPath` and update the matching entries in the chart `env` list:

```yaml
modelPath: "git:https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning"
env:
  - name: VLM_MODEL_TO_USE
    value: "vllm-compatible"
  - name: VLM_TRUST_REMOTE_CODE
    value: "true"
  - name: VLM_MODEL_SUPPORTS_AUDIO
    value: "true"
  - name: INSTALL_PROPRIETARY_CODECS
    value: "true"
```

If the checkpoint requires a Hugging Face token, create the `hf-token-secret` secret and set `hfTokenSecret.name=hf-token-secret`.

#### Start the service

```bash
docker compose up
```

#### Generate captions (video only)

```bash
BACKEND="http://<host_ip>:<backend_port>"
MODEL=$(curl -s $BACKEND/v1/models | jq -r '.data[0].id')

STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream \
  rtsp://<stream_url> \
  --description "Live stream" \
  --backend $BACKEND | grep -oP 'id: \K[^,]+')

python3 rtvi_client_cli.py generate-captions \
  --id $STREAM_ID \
  --model $MODEL \
  --prompt "Describe what you see in this scene." \
  --chunk-duration 10 \
  --stream \
  --backend $BACKEND
```

#### Generate captions (video + audio)

Requires `VLM_MODEL_SUPPORTS_AUDIO=true` in `.env`. Pass `--enable-audio` in the request to activate audio decoding for that request.

```bash
python3 rtvi_client_cli.py generate-captions \
  --id $STREAM_ID \
  --model $MODEL \
  --prompt "Describe what you see and hear in this scene." \
  --chunk-duration 10 \
  --enable-audio \
  --stream \
  --backend $BACKEND
```

Or via curl:

```bash
REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -N -X POST "$BACKEND/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "'"$REQUEST_ID"'",
    "url": "https://example.com/video_with_audio.mp4",
    "media_type": "video",
    "enable_audio": true,
    "model": "'"$MODEL"'",
    "prompt": "Describe what you see and hear.",
    "chunk_duration": 10,
    "stream": true
  }'
```

#### Notes

- `VLM_TRUST_REMOTE_CODE=true` is required for both video-only and video+audio usage.
- `enable_audio` in the request only takes effect when `VLM_MODEL_SUPPORTS_AUDIO=true` is set in `.env`. The env var gates model capability; the per-request flag enables audio decoding for that request.
- Reasoning is disabled by default (`enable_reasoning=false`). Pass `enable_reasoning=true` in the request to enable chain-of-thought output.

## License

This project is licensed under the **Apache License, Version 2.0**. See the top-level [LICENSE](../../../LICENSE) file in the repository, and the SPDX header carried in every source file in [`src/`](src/) and [`tests/`](tests/).
