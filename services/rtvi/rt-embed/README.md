# VSS RTVI Embed Microservice

VSS RTVI Embed Microservice generates video embeddings for given video, image or text input.
It supports both file input, file url and live stream videos.
For video input, the video is segmented into chunks as per requested chunk duration and
embeddings are generated for each video chunk.

Embedding models supported:
- [Cosmos-Embed1-448p](https://huggingface.co/nvidia/Cosmos-Embed1-448p)
- [Cosmos-Embed1-336p](https://huggingface.co/nvidia/Cosmos-Embed1-336p)
- [Cosmos-Embed1-224p](https://huggingface.co/nvidia/Cosmos-Embed1-224p)

The VSS RTVI Embed Microservice supports the following input types:

- **Video/Image files:** Video files can be uploaded to the server or accessed through a shared filepath.
- **Live video streams:** RTSP stream URLs starting with `rtsp://`
- **Remote video files:** Files accessible via HTTP/S links.
- **Text input:** Raw text strings for the text embedding API.


## Prerequisites
- **NGC API key** to download the base container and any NGC-hosted model.
- **Docker registry access** — authenticate to NGC before `docker compose pull`:

```bash
export NGC_API_KEY=<your-key>
echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
```

### Software Requirements
- **OS**: Ubuntu 24.04/22.04 or compatible Linux distribution
- **Docker**: Version 28.2+
- **Docker Compose**: Version 2.36+
- **NVIDIA Driver**: 580+
- **NVIDIA Container Toolkit**: Latest version

### Installation

- **Docker & Docker Compose**: Follow the [official Docker installation guide](https://docs.docker.com/engine/install/)
- **NVIDIA Container Toolkit**: Follow the [NVIDIA Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

## Quick Start

The Docker artifacts shipped in [`docker/`](docker/):

| File | Purpose |
|------|---------|
| [`docker/compose.yaml`](docker/compose.yaml) | Compose stack: `rtvi-server` + Kafka + Redis. Every variable exposed with a default |
| [`docker/Dockerfile`](docker/Dockerfile) | (Optional) layers your local `src/` edits onto the shipped image |
| [`configs/prometheus.yml`](configs/prometheus.yml) | Prometheus scrape config used by the `with-monitoring` compose profile |
| [`configs/otel-collector-config.yaml`](configs/otel-collector-config.yaml) | OpenTelemetry Collector pipeline config used by the `with-monitoring` compose profile |
| [`configs/dcgm-metrics-config.csv`](configs/dcgm-metrics-config.csv) | Custom DCGM exporter metrics used by the `with-monitoring` compose profile |

### 1. Create a `.env` file

Create `docker/.env` with the variables you want to override. A starting template:

```bash
BACKEND_PORT=8017
RTVI_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-embed:<tag>
#RTVI_IMAGE=docker.io/library/rtvi-embed:3.2.0-custom
MODEL_PATH=git:https://huggingface.co/nvidia/Cosmos-Embed1-448p
#HF_TOKEN=<HF_TOKEN>
#NGC_API_KEY=nvapi-XXXXXX
NVIDIA_VISIBLE_DEVICES=0

KAFKA_ENABLED=true
#KAFKA_BOOTSTRAP_SERVERS=<Kafka_server_ip:port>
#KAFKA_TOPIC=vision-embed-messages
#ERROR_MESSAGE_TOPIC=vision-embed-errors
```

Replace `<tag>` with the NGC image tag for your platform (for example `3.2.0` on x86, or `3.2.0-sbsa` on SBSA). You can set `RTVI_IMAGE` in `docker/.env` to pin the exact image tag for your deployment.

`compose.yaml` provides defaults for every other variable — see [Complete Environment Variable Reference](#complete-environment-variable-reference) below for the full list.

### 2. Start the service

```bash
cd docker
docker compose up
```

This pulls and runs the shipped image (`RTVI_IMAGE` from `.env`). Kafka and Redis are launched as part of this deployment. Set `KAFKA_BOOTSTRAP_SERVERS` / `REDIS_HOST` in `.env` to point at external instances. Use `KAFKA_PORT` / `REDIS_HOST_PORT` to change the host-side port mappings if `9092` / `6379` are already taken.

**Note:** First-time startup builds the Cosmos-Embed1 TensorRT engine, which can take 10–20 minutes. The compose healthcheck has a 1200 s `start_period` to accommodate this; subsequent restarts reuse the cached engine.

To run detached instead (recommended for the long first boot), use `-d`:

```bash
cd docker
docker compose up -d
docker compose logs -f rtvi-server
```

Check readiness once startup finishes from another shell (`BACKEND_PORT` must match `docker/.env`):

```bash
export BACKEND_PORT=8017
curl -fsS "http://localhost:${BACKEND_PORT}/v1/ready" && echo "Service is ready"
```

**Troubleshooting:** If startup fails with an out-of-memory error, set `NVIDIA_VISIBLE_DEVICES=<gpuid>` to a free GPU.

The APIs are available at `http://<host_ip>:<BACKEND_PORT>/docs`.

### 3. (Optional) Enable monitoring

To start the Prometheus + Jaeger + OpenTelemetry stack alongside the service, set `ENABLE_OTEL_MONITORING=true` in your `.env` and run:

```bash
cd docker
docker compose --profile with-monitoring up
```

The monitoring profile uses the configs under [`configs/`](configs/): [`prometheus.yml`](configs/prometheus.yml), [`otel-collector-config.yaml`](configs/otel-collector-config.yaml), and [`dcgm-metrics-config.csv`](configs/dcgm-metrics-config.csv).

### 4. (Optional) Build a custom image with your code changes

Only needed if you've edited files under [`src/`](src/) and want those changes baked into the running container. Otherwise skip this step.

```bash
# From the rt-embed/ directory — Dockerfile expects src/ in the build context
docker build -f docker/Dockerfile -t rtvi-embed:3.2.0-custom .
```

Then, in `docker/.env`, comment out the shipped image and uncomment the local-build line:

```bash
#RTVI_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-embed:<tag>
RTVI_IMAGE=docker.io/library/rtvi-embed:3.2.0-custom
```

Restart:

```bash
cd docker
docker compose down && docker compose up
```

## Test Client and Commands

### Create virtual environment

Download the Python client file: [rtvi_client_cli.py](src/cli/rtvi_client_cli.py)

```bash
sudo apt install python3-venv
mkdir ~/python_venv
python3 -m venv  ~/python_venv
source ~/python_venv/bin/activate
pip install sseclient-py requests tabulate tqdm pyyaml protobuf
```

### Use python client to generate embeddings for video file

```bash
FILE=its.mp4
BACKEND="http://<host_ip>:<backend_port>"

# Upload video/image file and get file id
FILE_ID=$(python3 rtvi_client_cli.py add-file "$FILE" --backend $BACKEND | grep -oP 'id: \K[^,]+' )

# Trigger embeddings generation request for video
python3 rtvi_client_cli.py generate-video-embeddings\
   --id $FILE_ID \
   --model cosmos-embed1-448p \
   --chunk-duration 60 \
   --backend $BACKEND

```

### Use python client to generate embeddings for live-stream

```bash
LIVE_STREAM="rtsp://nv-wowza-pdc.nvidia.com:1935/vod/Jensen_AI_Summit_India_1080p_blackwell_opus.mp4"

# Add live stream
STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream "$LIVE_STREAM" \
            --backend $BACKEND \
            --description "conference" \
            | grep -oP 'id: \K[^,]+' ) && \
            [ -n "$STREAM_ID" ] && echo "Stream added successfully. Stream ID: $STREAM_ID" || \
            echo "Error: Failed to add live stream. Please check the stream URL and backend connection."

# Trigger embeddings generation request for live-stream
python3 rtvi_client_cli.py generate-video-embeddings\
   --id $STREAM_ID \
   --model cosmos-embed1-448p \
   --chunk-duration 60 \
   --stream \
   --backend $BACKEND

# To delete the above live stream
python3 rtvi_client_cli.py delete-live-stream $STREAM_ID --backend $BACKEND

# To list available live streams
python3 rtvi_client_cli.py list-live-streams --backend $BACKEND

# To print curl request append --print at the end of the command
python3 rtvi_client_cli.py list-live-streams --backend $BACKEND --print

```

### Use python client to generate video embeddings with remote video URL

```bash
# Specify file id to be used with the url
FILE_ID=f1ee672c-1995-41ce-b5d9-9ca751c8d518
URL=https://huggingface.co/datasets/1x-technologies/world_model_raw_data/resolve/main/test_v2.0/videos/video_0.mp4?download=true
python3 rtvi_client_cli.py generate-video-embeddings \
  --id $FILE_ID \
  --model cosmos-embed1-448p  \
  --chunk-duration 60  \
  --backend $BACKEND \
  --url=$URL
```

### Use python client to generate embeddings for text input

```bash
python3 rtvi_client_cli.py generate-text-embeddings\
   --model cosmos-embed1-448p \
   --backend $BACKEND \
   --text-input "a person riding a motorcycle in the night" \
   --text-input "a car overtaking a white truck"
```

### Kafka Consumer

Download:
 [test_kafka_consumer.py](tests/kafka/test_kafka_consumer.py),
 [ext_pb2.py](src/server/protos/ext_pb2.py), and
 [nv_pb2.py](src/server/protos/nv_pb2.py)

```bash
python3 test_kafka_consumer.py \
    --topic vision-embed-messages \
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
ERROR_MESSAGE_TOPIC=vision-embed-errors  # Redis channel name for error messages
```

Error messages will be published to the Redis channel specified in `ERROR_MESSAGE_TOPIC`. The message format remains the same as Kafka (JSON with streamId, timestamp, type, source, event fields).

#### Enabling Redis Password Authentication

To enable password authentication for Redis in the Docker Compose deployment, modify [`docker/compose.yaml`](docker/compose.yaml):

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
    --channel vision-embed-errors \
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
    --channel vision-embed-errors \
    --host <redis_host> \
    --port 6379 \
    --message "Test error message" \
    --type functional
```

## Running Unit Tests on the Container

The unit tests under [`tests/rtvi_embed/`](tests/rtvi_embed/) are not shipped inside the released image; they must be copied into a running `rtvi-server` container and executed against the in-container `src/` tree. The runner [`run_rtvi_embed_tests.py`](tests/rtvi_embed/run_rtvi_embed_tests.py) expects `tests/` and `src/` to live as siblings, which matches `/opt/nvidia/rtvi/` inside the container.

### 1. Start the service

Bring up the stack as described in [Quick Start](#quick-start):

```bash
cd docker
docker compose up -d
```

Wait for the `rtvi-server` container to report healthy (first-time startup builds the TensorRT engine and can take 10–20 minutes).

### 2. Copy the tests into the container

From the `rt-embed/` directory:

```bash
docker cp ../tests/ docker-rtvi-server-1:/opt/nvidia/rtvi/tests
```

Adjust the container name (`docker-rtvi-server-1`) to match your environment — `docker compose ps` lists the actual name.

### 3. Install the test dependencies

```bash
docker compose exec rtvi-server bash -lc \
    "pip install --user pytest pytest-asyncio httpx fastapi"
```

### 4. Run the unit tests

```bash
docker compose exec rtvi-server bash -lc \
    "cd /opt/nvidia/rtvi/tests/rtvi_embed && python3 run_rtvi_embed_tests.py --unit --verbose"
```

The runner sets `PYTHONPATH` to `/opt/nvidia/rtvi/src` automatically so the `server.*` and `cli.*` modules resolve. Other useful flags:

| Flag | Purpose |
|------|---------|
| `--unit` | Run unit tests only |
| `--integration` | Run integration tests (requires `--server <url>`, defaults to `http://localhost:8000`) |
| `--all` | Run unit + integration tests |
| `--coverage` | Emit an HTML + terminal coverage report |
| `--parallel` | Run tests in parallel (requires `pytest-xdist`) |
| `--markers <expr>` | Filter by pytest markers (e.g. `no_gpu`) |

Exit code is non-zero if any selected suite fails.

## Configuration

Configuration is managed through environment variables, typically stored in a `.env` file at the repository root.

### Minimal Configuration

```bash
# Required
BACKEND_PORT=<port>
RTVI_IMAGE=<rtvi_embed_container_image>
```

### Recommended Configuration

```bash
# Ports
RTVI_IMAGE=<rtvi_embed_container_image>     # RTVI Embed Microservice container image
BACKEND_PORT=<port>                         # Host port on which the service will be available

# Storage
ASSET_STORAGE_DIR=/path/to/assets           # Host path for uploaded files
EXAMPLE_STREAMS_DIR=/path/to/sample-videos  # Host path for example streams

# GPU Configuration
NVIDIA_VISIBLE_DEVICES=0                    # Use specific GPUs (default: all)
VLM_BATCH_SIZE=128                          # Override automatic batch size

# Logging
LOG_LEVEL=INFO                              # DEBUG, INFO, WARNING, ERROR

# Kafka server config
KAFKA_ENABLED=<true/false>                  # Enable Kafka messages containing generated embeddings
KAFKA_BOOTSTRAP_SERVERS=<ip_address:port>   # Kafka server
KAFKA_TOPIC=vision-embed-messages           # Kafka message topic

# Redis error message config
ERROR_MESSAGE_TOPIC=vision-embed-errors     # Error message topic (Kafka or Redis channel)
ENABLE_REDIS_ERROR_MESSAGES=true            # Enable Redis messages for errors
REDIS_HOST=<redis_host>                     # Redis server hostname (default: redis)
REDIS_PORT=6379                             # Redis server port (default: redis)


```

**Note**:

For using input video files from AWS S3 buckets set below variables as applicable.

**From AWS S3:**

Ensure the LVS instance is started with these additional environment variables:
- `AWS_ACCESS_KEY_ID` - Your AWS access key ID
- `AWS_SECRET_ACCESS_KEY` - Your AWS secret access key

**From MinIO Object Store:**

If the MinIO is hosted locally or remotely, set the following environment variables:
- `AWS_ENDPOINT_URL_S3` (OR) `S3_ENDPOINT_URL` - MinIO endpoint URL (like `http://minio:9000`)
- `AWS_SECRET_ACCESS_KEY` - MinIO password (like `minio123`)
- `AWS_ACCESS_KEY_ID` - MinIO username (like `minio`)


### Model Configuration
The microservice can be configured to run with variants of the Cosmos-Embed1 model by
setting the `MODEL_PATH` to the appropriate value. Three source schemes are supported:

**HuggingFace (`git:` scheme)** — downloads from HuggingFace Hub on first startup:
```bash
MODEL_PATH=git:https://huggingface.co/nvidia/Cosmos-Embed1-448p
```

**NGC (`ngc:` scheme)** — downloads from the NGC model registry on first startup:
```bash
NGC_API_KEY=<your-ngc-api-key>
MODEL_PATH=ngc:nvidia/tao/cosmos-embed1:v1.0
```
Generate an NGC API key at https://ngc.nvidia.com → **Org** → **API Keys**. The model is
downloaded once and cached in `NGC_MODEL_CACHE` (default: `/opt/nvidia/rtvi/.rtvi/ngc_model_cache/`).

**Local path** — points directly to a pre-downloaded model directory:
```bash
MODEL_PATH=/path/to/cosmos-embed1
```

Also the batch size used for the model can be configured using the `VLM_BATCH_SIZE` environment variable.
Use the /v1/models API to get the name of the model once the server is up.


### Complete Environment Variable Reference

#### Core Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `BACKEND_PORT` | Port for REST API server | `8000` | Yes |
| `LOG_LEVEL` | Logging verbosity | `INFO` | No |

#### Model Download Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `NGC_API_KEY` | NGC API key for downloading models via the `ngc:` scheme | - | No |
| `HF_TOKEN` | HuggingFace API access token for downloading private models via the `git:` scheme | - | No |

#### Model Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `VLM_BATCH_SIZE` | Inference batch size | Auto-calculated | No |
| `NUM_VLM_PROCS` | Number of inference processes | `10` | No |
| `NUM_GPUS` | Number of GPUs to use | Auto-detected | No |
| `NVIDIA_VISIBLE_DEVICES` | GPU device IDs | `all` | No |
| `MODEL_PATH` | Model source: `ngc:<org/team/model:ver>`, `git:<hf-url>`, or local path | `git:https://huggingface.co/nvidia/Cosmos-Embed1-448p` | No |
| `MODEL_IMPLEMENTATION_PATH` | Implementation code path for the model | `/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1` | No |
| `COSMOS_EMBED1_TRT_PRECISION` | trtexec network precision for Cosmos-Embed1 video/text TRT engines (`fp32`, `fp16`, `bf16`, `int8`, `fp8`, `best`). Read by `create_triton_model_repo.py`. Engine filename includes the precision so engines are rebuilt on change. | `fp16` | No |
| `COSMOS_EMBED1_TRT_EXTRA_ARGS` | Extra trtexec args (shell-quoted string) appended verbatim to both video and text engine builds, e.g. `--stronglyTyped --builderOptimizationLevel=5`. Note: `--stronglyTyped` is mutually exclusive with `--fp16`/`--bf16`/`--int8`/`--fp8`/`--best`; pair it with `COSMOS_EMBED1_TRT_PRECISION=fp32`. Engine filename includes a short hash of these args so engines are rebuilt on change. | - | No |


#### Storage and Caching
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ASSET_STORAGE_DIR` | Host path for uploaded files. When set, Docker bind-mounts this path over the default tmpfs at `/tmp/assets`. | - | No |
| `ASSET_TMPFS_SIZE` | Docker tmpfs size for `/tmp/assets` when `ASSET_STORAGE_DIR` is not set | `8g` | No |
| `MAX_ASSET_STORAGE_SIZE_GB` | Numeric max asset storage in GB; enables automatic age-out eviction. `ASSET_TMPFS_SIZE` uses Docker size notation such as `8g`, while this value is numeric; for example, if `ASSET_TMPFS_SIZE` is `8g`, set `MAX_ASSET_STORAGE_SIZE_GB` to `8` or less. When `ASSET_STORAGE_DIR` is set to a bind mount, set this to match available disk space. Startup logs a warning if unset or larger than actual storage capacity. | `8` | No |
| `EXAMPLE_STREAMS_DIR` | Sample streams directory | - | No |
| `RTVI_LOG_DIR` | Log output directory | - | No |
| `NGC_MODEL_CACHE` | Path for NGC/git model cache directory | - | No |

#### Feature Toggles
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ENABLE_NSYS_PROFILER` | Enable NSYS profiling | `false` | No |
| `INSTALL_PROPRIETARY_CODECS` | Download and extract patent-encumbered codec packages at startup (no root required). Accepts `true`/`1`/`yes` (case-insensitive). | `false` | No |
| `FORCE_SW_AV1_DECODER` | Force software AV1 decode | `false` | No |
| `RTVI_ENABLE_GOP_DECODE_OPT` | Attach GOP-aware decode probe on `h264parse`/`h265parse`/`mpeg4videoparse` to drop delta frames in GOPs that contain no selected target frame (file-based decoding only; no effect on live RTSP or when all frames are selected). Set `false`/`0`/`no`/`off` to disable. | `true` | No |

#### RTSP Streaming
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `RTVI_RTSP_LATENCY` | RTSP latency (ms) | `2000` | No |
| `RTVI_RTSP_TIMEOUT` | RTSP timeout (ms) | `2000` | No |
| `RTVI_RTSP_RECONNECTION_INTERVAL` | Time to detect stream interruption and wait for reconnection (seconds) | `5.0` | No |
| `RTVI_RTSP_RECONNECTION_WINDOW` | Duration to attempt reconnection after interruption before terminating the session (seconds) | `60.0` | No |
| `RTVI_RTSP_RECONNECTION_MAX_ATTEMPTS` | Max attempts for reconnection after interruption before terminating the session (no.) | `10` | No |
| `RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC` | Per-delete upper bound (seconds) shared by the pre-delete setup wait (while `use_count > 1`) and the pipeline drain of in-flight chunks. On timeout each stage logs a warning and proceeds. Applies to `DELETE /v1/streams/delete-batch`, `DELETE /v1/streams/delete/{stream_id}`, `POST /v1/stream/remove`, `DELETE /v1/generate_video_embeddings/{stream_id}`. | `30` | No |

#### OpenTelemetry / Monitoring
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ENABLE_OTEL_MONITORING` | Enable Opentelemetry monitoring | `false` | No |
| `OTEL_SERVICE_NAME` | Service name for traces | `rtvi` | No |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint | `http://otel-collector:4318` | No |
| `OTEL_TRACES_EXPORTER` | Traces exporter type | `otlp` | No |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metrics export interval in milliseconds | `60000` | No |
| `ENABLE_REQUEST_PROFILING` | Enable per-request profiling and traces dump | `false` | No |

#### Kafka Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `KAFKA_ENABLED` | Enable Kafka integration | `false` | No |
| `KAFKA_PORT` | Host port to expose Kafka (Docker Compose only) | `9092` | No |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker addresses | `localhost:9092` | No |
| `KAFKA_TOPIC` | Kafka topic name for VisionLLM/embedding messages | `vision-embed-messages` | No |
| `ERROR_MESSAGE_TOPIC` | Kafka topic name for error messages (or Redis channel when Redis is enabled) | `vision-embed-errors` | No |
| `ENABLE_KAFKA_MESSAGES_FOR_TEXT_INPUT` | Enable streaming text embeddings results to Kafka | `false` | No |
| `KAFKA_ASYNC_SEND_QUEUE_MAXSIZE` | Max queued Kafka producer send jobs before dropping during broker metadata stalls | `1024` | No |

#### Redis Error Messages Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ENABLE_REDIS_ERROR_MESSAGES` | Enable Redis for error messages instead of Kafka | `false` | No |
| `REDIS_HOST` | Redis server hostname | `redis` | No |
| `REDIS_PORT` | Redis container/service port for application connections | `6379` | No |
| `REDIS_HOST_PORT` | Host machine port to expose Redis (Docker Compose only) | `6379` | No |
| `REDIS_DB` | Redis database number | `0` | No |
| `REDIS_PASSWORD` | Redis authentication password | - | No |

#### Advanced Performance
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `VSS_NUM_GPUS_PER_VLM_PROC` | GPUs per Embedding process | - | No |
| `DISABLE_DECODER_REUSE` | Disable decoder reuse | Auto | No |

#### Docker Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `RTVI_IMAGE` | Docker image to use | `nvcr.io/nvidia/vss-core/vss-rt-embed:<tag>` | No |
| `HF_TOKEN` | Hugging Face Hub access token for private `git:` model downloads; forwarded from `docker/.env` into the container by Compose | - | No |

#### AWS Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AWS_ACCESS_KEY_ID` | AWS access key for S3 storage | - | No |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for S3 storage | - | No |
| `AWS_DEFAULT_REGION` | AWS region for S3 access | `us-west-1` | No |
| `S3_BUCKET_NAME` | S3 bucket name for input/output files | - | No |
| `S3_ENDPOINT_URL` | Custom S3-compatible endpoint URL (optional, for MinIO/etc.) | - | No |


#### Asset Download Timeout Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ASSET_DOWNLOAD_TOTAL_TIMEOUT` | Total timeout for asset (file) download via url in seconds | `300` | No |
| `ASSET_DOWNLOAD_CONNECT_TIMEOUT` | Timeout for establishing connection for asset (file) download via url in seconds | `10` | No |
| `ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS` | Comma-separated domains to skip SSL verification (e.g., `artifactory.nvidia.com`) | *(empty)* | No |
| `ASSET_DOWNLOAD_MAX_REDIRECTS` | Max redirect hops for URL downloads (0 = disabled, max 10). SSRF-validated. | `0` | No |
| `ASSET_DOWNLOAD_MAX_FILE_SIZE_GB` | Max file size for HTTP/data URI asset ingestion. Direct multipart uploads are constrained by `ASSET_TMPFS_SIZE` or `ASSET_STORAGE_DIR` plus `MAX_ASSET_STORAGE_SIZE_GB`. | `8` | No |
| `ASSET_DOWNLOAD_AUTH_TOKENS` | Server-level auth for URL downloads. Format: `domain1=Bearer token1;domain2=Basic xyz` | *(empty)* | No |
| `ASSET_MAX_AGE_HOURS` | TTL-based asset eviction (hours). 0 = no eviction. | `0` | No |



#### Local File URL Configuration
| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `FILE_URL_ALLOWED_DIRS` | Comma-separated list of absolute directory paths accessible via `file://` URLs in `POST /v1/generate_video_embeddings`. Paths are resolved with `realpath` to prevent directory traversal. `file://` URLs are disabled when this variable is unset or empty. Example: `/mnt/videos,/data/clips` | *(empty — disabled)* | No |

## License

This project is licensed under the **Apache License, Version 2.0**. See the top-level [LICENSE](../../../LICENSE) file in the repository, and the SPDX header carried in every source file in [`src/`](src/) and [`tests/`](tests/).
