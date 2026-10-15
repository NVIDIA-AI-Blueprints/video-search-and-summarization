# ReID Embed microservice

The ReID Embed microservice stores and retrieves object re-identification embeddings. It consumes embeddings in a DeepStream schema from Kafka, stores each embedding with its tracking id in Milvus, and answers a query embedding with the closest object ids and their confidence.

Supported DeepStream schemas: `deepstream`, `minimal`, and `protobuf`.

## Prerequisites

- **OS**: Ubuntu 22.04/24.04 or a compatible Linux distribution
- **Docker**: Version 28+
- **Docker Compose**: Version 2 (`docker compose`)
- **NVIDIA Container Toolkit**: [install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

The image is built `FROM nvcr.io/nvidia/pytorch:25.11-py3`. Log in before the first build:

```bash
docker login nvcr.io
```

Use `$oauthtoken` as the username and an NGC API key as the password.

Kafka is not part of this stack. It must be reachable on the host at `localhost:9092`. If you do not already have a broker:

```bash
wget https://dlcdn.apache.org/kafka/4.0.0/kafka_2.13-4.0.0.tgz
tar -xzf kafka_2.13-4.0.0.tgz
cd kafka_2.13-4.0.0

export KAFKA_CLUSTER_ID="$(bin/kafka-storage.sh random-uuid)"
bin/kafka-storage.sh format --standalone -t "$KAFKA_CLUSTER_ID" -c config/server.properties
bin/kafka-server-start.sh config/server.properties &

bin/kafka-topics.sh --bootstrap-server localhost:9092 \
    --create --topic mv3dt --partitions 1 --replication-factor 1 \
    --config retention.ms=60000 --if-not-exists
```

## Quick Start

The Docker artifacts shipped in [`docker/`](docker/):

| File | Purpose |
|------|---------|
| [`docker/compose.yaml`](docker/compose.yaml) | Compose stack: ReID Embed microservice + etcd + MinIO + Milvus, all on the host network |
| [`docker/Dockerfile`](docker/Dockerfile) | Image. NGC PyTorch base plus the packages locked in [`docker/py_deps/`](docker/py_deps/) |
| [`docker/py_deps/pyproject.toml`](docker/py_deps/pyproject.toml) | Direct dependencies installed on top of the NGC base |
| [`docker/py_deps/uv.lock`](docker/py_deps/uv.lock) | Lockfile. The image installs from this with `uv` |
| [`docker/py_deps/requirements.txt`](docker/py_deps/requirements.txt) | Hash-free export of the lock, for review |
| [`docker/packagelist.txt`](docker/packagelist.txt) | Paths from [`src/`](src/) copied into the image |
| [`src/start_service.sh`](src/start_service.sh) | Container entrypoint |

### 1. Create a `.env` file

Create `docker/.env`. Compose reads it for variable substitution and passes it into the container. `up` fails if the file is missing. Defaults are fine; uncomment a line only to override it:

```bash
#ENABLE_COMPRESSION=true
```

### 2. Start the service

```bash
cd docker
docker compose up -d
docker compose logs -f reid-embed
```

This builds the image and starts etcd, MinIO, Milvus, and the ReID Embed microservice on the host network.

Check readiness once startup finishes:

```bash
curl -fsS http://localhost:8000/health/ready && echo "Service is ready"
```

`/health/ready` returns 503 until Milvus is reachable and the child processes have finished startup.

The APIs are available at `http://<host_ip>:8000/docs`.

## Runtime Configuration

Set these in `docker/.env`. Unset variables keep the defaults below.

### Runtime and connectivity

| Variable | Default | Description |
|----------|---------|-------------|
| `REID_SERVICE_PORT` | `8000` | Port the HTTP server binds. With `network_mode: host` this is the port on the machine. |
| `KAFKA_CONSUMER_SERVER` | `localhost:9092` | `host:port` of the Kafka broker that carries DeepStream embeddings. |
| `KAFKA_CONSUMER_TOPIC` | `mv3dt` | Topic to consume. Set this to the topic name in the DeepStream pipeline config. |
| `MILVUS_HOST` | `localhost` | Host of the Milvus instance that stores the main embeddings collection. `compose.yaml` runs Milvus on the host network, so `localhost` is correct for that stack. |
| `MILVUS_PORT` | `19530` | Milvus gRPC port. |
| `LOG_LEVEL` | `INFO` | Log verbosity for the service and uvicorn. One of `ERROR`, `WARNING`, `INFO`, `DEBUG`. |

### Compression

Background compression builds a smaller collection from the main embeddings. Leave it off unless search should run against that collection.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_COMPRESSION` | `false` | Turn the compression loop on. |
| `COMPRESSION_INTERVAL_SEC` | `1` | Seconds between compression runs, once at least `COMPRESSION_MIN_BATCH` rows are stored. Set `0` to ignore the timer and compress only when the collection reaches `COMPRESSION_BATCH_LIMIT`. |
| `COMPRESSION_BATCH_LIMIT` | `5000` | Row count that triggers one compression run. Used only when `COMPRESSION_INTERVAL_SEC` is `0`. |
| `COMPRESSION_MIN_BATCH` | `100` | Minimum rows required before a timed run. Ignored when `COMPRESSION_INTERVAL_SEC` is `0`. |

### Secondary embedding generation

The worker computes a second embedding per tracked object and publishes an enriched frame. Setting `ENABLE_SECONDARY_EMBEDDING=true` also turns compression on.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SECONDARY_EMBEDDING` | `false` | Start the worker. It runs after compression. |
| `SECONDARY_EMBEDDING_MODEL` | `siglip2` | Which TensorRT engine to load. `siglip2` or `radio-clip`. |
| `SECONDARY_EMBEDDING_ONNX_MODEL_PATH` | unset | ONNX file used to build that engine. Leave unset to use the path bundled with the selected model. |
| `KAFKA_PRODUCER_SERVER` | `localhost:9092` | `host:port` of the Kafka broker that receives the enriched frames. Used when `MESSAGE_BROKER=kafka`. The producer connects on the first publish. |
| `KAFKA_PRODUCER_TOPIC` | `compressed-embeddings` | Topic those frames are written to. |
| `VIOS_SERVER` | unset | `host:port` of a VIOS server. When set, the worker fetches the source frame from VIOS instead of a local file. |
| `SECONDARY_EMBEDDING_MIN_VISIBILITY` | `0.7` | Objects whose visibility is below this value, from 0 to 1, are not embedded. |

### Redis consumer

Set `MESSAGE_BROKER=redis` to ingest from a Redis stream instead of Kafka. `compose.yaml` does not start Redis.

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_BROKER` | `kafka` | `kafka` or `redis`. This chooses both the ingest source and where secondary embeddings are published. |
| `REDIS_CONSUMER_SERVER` | `localhost:6379` | `host:port` of the Redis instance to read from. |
| `REDIS_CONSUMER_TOPIC` | `mv3dt` | Stream name. Set this to the stream in the DeepStream pipeline config. |
| `REDIS_CONSUMER_PAYLOAD_KEY` | `metadata` | Hash field that holds the serialized DeepStream payload. Set this to the same `payloadkey` the DeepStream Redis adapter uses. |

### Redis producer

Used when `MESSAGE_BROKER=redis` and secondary embedding is on. The worker writes enriched frames to this stream.

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_PRODUCER_SERVER` | `localhost:6379` | `host:port` of the Redis instance to write to. |
| `REDIS_PRODUCER_TOPIC` | `compressed-embeddings` | Stream name for the enriched frames. |
| `REDIS_PRODUCER_PAYLOAD_KEY` | `metadata` | Hash field used in `XADD`. Set this to the field name the downstream subscriber reads. |

## License

This project is licensed under the **Apache License, Version 2.0**. See the [top-level LICENSE](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/main/LICENSE) file in the repository, and the SPDX header carried in every source file in [`src/`](src/) and [`tests/`](tests/).

## External Materials Notice

NOTICE AND DISCLAIMER: This software automatically retrieves, accesses or interacts with external materials.  Those retrieved materials are not distributed with this software and are governed solely by separate terms, conditions and licenses.  You are solely responsible for finding, reviewing and complying with all applicable terms, conditions, and licenses, and for verifying the security, integrity and suitability of any retrieved materials for your specific use case. This software is provided "AS IS", without warranty of any kind. The author makes no representations or warranties regarding any retrieved materials, and assumes no liability for any losses, damages, liabilities or legal consequences from your use or inability to use this software or any retrieved materials. Use this software and the retrieved materials at your own risk.
