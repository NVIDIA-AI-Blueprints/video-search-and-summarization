<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# AOI active-learning deployment (`deploy/docker/active-learning`)

Docker Compose deployment of the DEFT AOI active-learning pipeline: AOI
inspection records are ingested and stored, served to a ChangeNet classifier
for second inspection, and fed to the Data Flywheel. The Flywheel fine-tunes
candidate models with DEFT, gates them on FAR, and promotes them back into
serving.

The stack is split into four bundles under `includes/`. The top-level
`compose.yml` assembles them into one Compose project, `aoi-active-learning`.

| Bundle | Services |
|---|---|
| `deft-aoi-active-learning` | SeaweedFS (3 masters, 3 volumes, filer, S3 gateway), Kafka, Elasticsearch, Kibana, Logstash, `backend-api`, `aoi-client`, `al-archiver`, and the snapshot, eval and KPI workers |
| `deft-aoi-inference` | `aoi-inference-triton` (GPU), `aoi-inference-api` (8 replicas), `aoi-inference` (HAProxy front end) |
| `deft-aoi-data-flywheel` | `data-flywheel-api`, `-worker`, `-parent-worker`, `-beat`, `data-flywheel-model-manager`, Redis, MongoDB, MLflow |
| `deft-aoi-deft-service` | `deft-service` (DEFT training; launches TAO stage containers) |

The bundles don't run on their own. For example, data-flywheel depends on
`backend-api`, `deft-service` and `aoi-inference`. Always go through the
top-level `compose.yml`.

## Requirements

- Docker Engine with the Compose plugin. `include:` with `env_file:` is
  required; this layout was validated with Docker Compose v5.5.1.
- An NVIDIA GPU and the NVIDIA Container Toolkit. `aoi-inference-triton`
  reserves GPU `0`, and `deft-service` launches GPU TAO containers through the
  host Docker socket.
- Pull access to the AOI images, which default to
  `nvcr.io/0691049440816435/mdx/*`. Each image can be overridden with its
  `*_IMAGE` variable in the bundle `.env` files.

## Deploy

Run every command from this directory.

```bash
install -m 600 .env.secrets.example .env.secrets
# Set AOI_SELECTION_TAG, DEFT_RUNTIME_DIR and the four secrets in .env.secrets
mkdir -p "$(grep '^DEFT_RUNTIME_DIR=' .env.secrets | cut -d= -f2-)"

docker compose --env-file .env.secrets config --quiet   # validate
docker compose --env-file .env.secrets up -d
docker compose --env-file .env.secrets ps
```

Pass `--env-file .env.secrets` to every `docker compose` command, including
`ps`, `logs` and `down`. The compose files require those values to
interpolate.

### Deploy and verify in one step

`deploy-and-verify.sh` runs the same deploy, then checks the result. It needs
[uv](https://docs.astral.sh/uv/), or `pytest` and `pyyaml` for `python3`.

```bash
./deploy-and-verify.sh                 # config checks, pull, up -d, wait, deployment checks
./deploy-and-verify.sh --verify-only   # checks only, against the running stack
./deploy-and-verify.sh --no-pull       # use the images already on this host
```

It exits 0 only if every check passes. The checks are in `tests/`:

- `test_config.py` runs before deploying and needs no running stack. It checks
  `.env.secrets` and `DEFT_RUNTIME_DIR`, image pinning and registry access, and
  that the shared Data Flywheel config renders for every `data-flywheel-*`
  service with FAR targets that agree.
- `test_live_deployment.py` is read-only. It checks replicas, health,
  restarts, one-shot jobs, the HTTP endpoints, the Kafka topics, the bucket,
  the index template, the Logstash pipelines and the model repository.

The HTTP checks use each port's bind address, `127.0.0.1` by default. To run
them from another machine, set `AL_VERIFY_HOST` and publish the ports on a
reachable address first (see [Published ports](#published-ports)).

To stop the stack, run `docker compose --env-file .env.secrets down`. Named
volumes are kept. Add `-v` to delete them, which erases the bucket, the Kafka
and Elasticsearch data, the MLflow registry and the model repository.

## Configuration

Values resolve in this order. The first source that sets a variable wins.

1. The shell environment.
2. `.env.secrets`, passed with `--env-file`. It holds the required inputs and
   secrets, and anything you put in it applies to all four bundles.
3. The bundle's own `includes/<bundle>/.env.deft-aoi-*` file. These hold the
   tunable defaults; each one only affects its own bundle.
4. The default written in the compose file.

Service-to-service URLs, Kafka topic names and the S3 bucket name
(`aoi-samples`) are fixed in the compose files rather than exposed as
variables. Producers, consumers and the bootstrap jobs must agree on them, so
changing one in isolation would break delivery.

## Published ports

Every published port binds to `127.0.0.1` by default. To accept remote clients
on a port, set its bind variable to `0.0.0.0` or to a host IP. For example,
set `AOI_BIND_ADDRESS=0.0.0.0` for AOI machines that post to `backend-api`.
Docker-published ports bypass host firewalls such as ufw.

| Service | Default host port | Port variable | Bind variable |
|---|---|---|---|
| `backend-api` | 8008 | `AOI_BACKEND_PORT` | `AOI_BIND_ADDRESS` |
| `aoi-client` | 8012 | `AOI_CLIENT_PORT` | `AOI_BIND_ADDRESS` |
| `al-archiver` | 8013 | `AOI_ARCHIVER_PORT` | `AOI_BIND_ADDRESS` |
| `aoi-inference` | 5010 | `AOI_INFERENCE_PORT` | `AOI_INFERENCE_BIND_ADDRESS` |
| `seaweedfs-s3` | 8333 | `SEAWEEDFS_S3_PORT` | `SEAWEEDFS_S3_BIND_ADDRESS` |
| `kafka` (external listener) | 9094 | `KAFKA_EXTERNAL_PORT` | fixed to `127.0.0.1` |
| `elasticsearch` | 9201 | `ELASTICSEARCH_PORT` | `ELK_BIND_ADDRESS` |
| `kibana` | 5602 | `KIBANA_PORT` | `ELK_BIND_ADDRESS` |
| `logstash` (monitoring API) | 9600 | `LOGSTASH_PORT` | `ELK_BIND_ADDRESS` |
| `data-flywheel-api` | 8020 | `DFW_API_PORT` | `DFW_BIND_ADDRESS` |
| `data-flywheel-model-manager` | 8090 | `DFW_MODEL_MANAGER_PORT` | `DFW_BIND_ADDRESS` |
| `mlflow` | 5500 | `MLFLOW_HOST_PORT` | `MLFLOW_BIND_ADDRESS` |
| `deft-service` | 9150 | `DEFT_SERVICE_PORT` | `DEFT_SERVICE_BIND_ADDRESS` |

These services are reachable only on the project network: the SeaweedFS
masters, volume servers and filer, the Kafka broker listener, Redis, MongoDB,
Triton and the `aoi-inference-api` replicas. The SeaweedFS master API in
particular has no authentication, so it must stay unpublished.

Keep these in mind before exposing a port:

- Elasticsearch runs with security disabled.
- `deft-service` mounts the Docker socket.
- Presigned S3 URLs only work for a remote browser if `seaweedfs-s3` is
  reachable from it. In that case, also set `AWS_PUBLIC_ENDPOINT` in
  `includes/deft-aoi-active-learning/.env.deft-aoi-active-learning`.

## Running next to VSS

The stack can share a host with a VSS profile:

- No service sets `container_name`, so containers are named
  `aoi-active-learning-<service>-<n>`.
- Networks and volumes are scoped to the `aoi-active-learning` project.
- The default host ports avoid the ones VSS publishes, such as 8000, 5000,
  5601, 8010, 8011 and 9200.

Use `docker compose --env-file .env.secrets exec <service> …` instead of a
container name.

## Startup order

Three one-shot jobs prepare shared state:

| Job | What it prepares |
|---|---|
| `seaweedfs-bucket-bootstrap` | Creates the `aoi-samples` bucket |
| `kafka-aoi-topic-bootstrap` | Creates the eight Kafka topics and verifies their partition and retention policy |
| `elasticsearch-aoi-template-bootstrap` | Installs the `aoi-inspection` index template |

Services that need that state declare `depends_on` with
`condition: service_completed_successfully` on the job. A partial bring-up such
as `docker compose --env-file .env.secrets up -d logstash` therefore runs the
jobs it needs first. If a job fails, `up` reports it rather than leaving a
dependent service waiting.

`aoi-inference` reports healthy only when an HTTP request through HAProxy to
`/v1/health` succeeds. Services that depend on it with `service_healthy`
therefore start only once inference can actually answer.

## Model repository

`aoi-inference-triton-seed-aoi-model-repository` copies the baseline ChangeNet
model from the inference image into the `aoi-inference-model-repository`
volume. It does this only when the volume is empty or incomplete. The volume
also holds every version the Flywheel promotes later. For that reason,
upgrading `AOI_INFERENCE_IMAGE` does **not** refresh the baseline in an
existing volume.

To start over from a new image's baseline, remove the volume. This erases all
promoted versions.

```bash
docker compose --env-file .env.secrets down
docker volume rm aoi-active-learning_aoi-inference-model-repository
```

The seed job sets a default ACL on the volume so that Triton (UID 1000) and the
Model Manager (UID 10001) can both write to it. The job needs `setfacl` (the
`acl` package) in the inference image. It does not install packages at deploy
time; if `setfacl` is missing, it stops with an error.

## Regenerating the bundles

The bundle `compose.yml` and `.env.deft-aoi-*` files are generated by
`blueprint_builder` (see `x-blueprint-builder` in each file). When you
regenerate them, carry the hand changes in this directory into the generator
input:

- SPDX headers
- bind addresses
- fixed topic and bucket names
- bootstrap `depends_on` instead of `*-wait-for-*` containers
- no `container_name`
- one shared Data Flywheel config
