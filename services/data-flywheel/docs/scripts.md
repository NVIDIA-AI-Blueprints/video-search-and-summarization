# Scripts Directory

Efficiently manage and maintain the Data Flywheel Blueprint application using the following scripts. These utilities help automate cleanup, manage resources, and streamline your workflow.

## Prerequisites

- Docker and Docker Compose must be installed.
- The `uv` package manager must be installed.
- Scripts assume you are running in the project root directory unless otherwise specified.
- Note: Volume cleanup scripts automatically manage service lifecycle - manual shutdown is not required.

---

## Scripts

### `deploy-nmp.sh`

Deployment script for NVIDIA NeMo microservices (NMP) setup. This is a comprehensive deployment script with specialized configuration for enterprise environments.

- Contains advanced deployment logic and enterprise-specific configurations.
- Requires specific NMP credentials and environment setup.
- See internal deployment documentation for usage details.

### `generate_openapi.py`

Python script to generate the OpenAPI specification for the API.

- Imports the FastAPI app and writes the OpenAPI schema to `openapi.json` (or a user-specified path).
- Validates the output path for safety.
- Can be run as `python scripts/generate_openapi.py [output_path.json]`.

### `run.sh`

- Stops any running containers, then starts the main application stack using Docker Compose.
- Builds images as needed.
- Runs MongoDB in detached mode without attaching logs, to reduce log noise.

### `run-dev.sh`

- Stops any running containers, then starts the application stack with both the main and development Docker Compose files.
- Builds images as needed.
- Runs MongoDB, Elasticsearch, and Kibana in detached mode (no logs attached).
- Ensures development UIs are available.

### `stop.sh`

- Stops all running containers for both the main and development Docker Compose files.

### Volume Cleanup Scripts

- `clear_es_volume.sh`, `clear_redis_volume.sh`, `clear_mongodb_volume.sh`, `clear_mlflow_volume.sh`—Each script:
  - Stops the relevant service container (Elasticsearch, Redis, MongoDB, or MLflow).
  - Removes the associated Docker volume to clear all stored data.
  - Restarts the service container to ensure the service is running with a fresh, empty volume.
  - Prints status messages for each step.
- `clear_all_volumes.sh`—A convenience script to clear all persistent data volumes used by the application. It sequentially calls the four volume cleanup scripts above (Elasticsearch, Redis, MongoDB, and MLflow) and restarts all services.

### `check_requirements.sh`

A script to ensure your `requirements.txt` is in sync with your `pyproject.toml`:

- Uses `uv` to generate a temporary list of installed packages.
- Compares this list to `requirements.txt`.
- If out of sync, prints a diff and instructions to update.
- Exits with an error if not up to date, otherwise confirms success.

### `quick-test.sh`

A minimal script to quickly verify that the API is running and responsive:

- Sends a POST request to `http://localhost:8001/jobs` with a test payload.
- Useful for smoke-testing the local API after startup.
