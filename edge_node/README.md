# VSS Edge Node Implementation

This directory contains the implementation of the Video Search & Summarization (VSS) Edge Node, designed for deployment on resource-constrained devices like NVIDIA Jetson. The architecture is based on a set of containerized microservices communicating via local APIs, a shared SQLite database, and an external MQTT broker.

## Architecture Overview

The edge node is composed of the following services, each implemented as a separate Python module:

| Service | Module | Responsibility |
| :--- | :--- | :--- |
| **vss_ingest** | `services/vss_ingest.py` | RTSP ingestion, ONVIF discovery, video chunking to local disk using `ffmpeg`. |
| **vss_cv** | `services/vss_cv.py` | Wrapper service for detector + tracker inference, exposed via a REST API (`/infer`). |
| **vss_aggregator** | `services/vss_aggregator.py` | Event builder, local SQLite queue management, and metadata generation. Exposed via a REST API (`/events/new`, `/events/pending`). |
| **vss_uploader** | `services/vss_uploader.py` | Presigned upload worker, handles file upload, completion notification, metadata POST, and retry logic. |
| **vss_mqtt** | `services/vss_mqtt.py` | MQTT client for publishing alerts and periodic heartbeats, and subscribing to control topics (e.g., `request_clip`). |
| **vss_sync** | `services/vss_sync.py` | Training and Knowledge Base (KB) polling worker, handles package download, verification, and model reloading. |
| **vss_watchdog** | `services/vss_watchdog.py` | Aggregates health from all local services and provides auto-restart hooks for critical failures. |

## Configuration

The entire edge node is configured via a single YAML file: `config/config.yaml`.

- **Configuration Loader**: `config_loader.py` provides a typed configuration using Pydantic models and validates the configuration against `config/schema.json` using `jsonschema`.
- **CLI Validation**: The configuration can be validated using the command:
  ```bash
  python -m edge_node.config_loader validate /path/to/config.yaml
  ```

## Data Persistence

The following paths are used for data persistence and should be mounted as volumes in the Docker containers:

| Path | Purpose |
| :--- | :--- |
| `/etc/vss/` | Configuration (`config.yaml`) and mTLS certificates. |
| `/var/lib/vss/vss_events.db` | Local SQLite database for event queue, upload status, and device state. |
| `/var/lib/vss/clips/` | Storage for video chunks and extracted event clips. |
| `/opt/vss/models/` | Storage for downloaded and installed CV models and training packages. |

## Deployment (Jetson)

The deployment is managed using Docker Compose and a systemd unit file for persistent operation.

1.  **Dockerfile**: `edge_node/Dockerfile` provides a base image build process, using an NVIDIA CUDA base image (placeholder for L4T) and installing all necessary dependencies.
2.  **Docker Compose**: `docker/jetson/docker-compose.yaml` defines all seven services, ensuring proper volume mounts, port mappings, and the use of the `nvidia` runtime for GPU access.
3.  **Systemd Unit**: `docker/jetson/vss-edge.service` is a sample systemd unit file to manage the Docker Compose stack, ensuring it starts on boot and restarts on failure.

To deploy on a Jetson device:

1.  Copy the `config/config.yaml` and necessary certificates to `/etc/vss/` on the host.
2.  Copy the `docker/jetson/vss-edge.service` to `/etc/systemd/system/`.
3.  Build and run the stack:
    ```bash
    cd /path/to/repo/docker/jetson
    sudo docker-compose up --build -d
    ```
4.  Enable the systemd service (if using systemd):
    ```bash
    sudo systemctl enable vss-edge.service
    sudo systemctl start vss-edge.service
    ```
