# Installation and Setup

Complete setup guide for the Data Flywheel Blueprint, including prerequisites, NMP deployment, configuration, and starting services.

> **💡 Encountering Issues?** Check our [FAQ and Troubleshooting Guide](../faq-troubleshooting.md) for solutions to common setup problems.

## Deployment Modes Overview

The Data Flywheel Blueprint is dependent on the **NeMo Microservices Platform (NMP)** and supports two deployment modes:

| Deployment Mode | Description | Best For |
|-----------------|-------------|----------|
| **Option A: NMP Helm Chart + Docker Compose** | Deploy NMP using its Helm chart, then deploy Data Flywheel services using Docker Compose | Development, testing, users who prefer Docker-based services with separate NMP management |
| **Option B: Complete DFW Helm Chart** | Single Helm chart deployment that includes NMP Helm Chart as a dependency | Unified deployment, Kubernetes-native environments, users who prefer all-in-one Helm management |

Choose your deployment mode based on your environment and requirements:

- **NMP Helm Chart + Docker Compose**: Ideal for development and testing. Deploy NMP first via Helm chart, then run Data Flywheel services with Docker Compose, giving you more control over NMP configuration
- **Complete DFW Helm Chart**: Provides a unified deployment on Kubernetes  with NMP bundled as a dependency, simplifying the installation process

> **📌 NeMo Microservices Version:** This blueprint is tested and validated with **NMP 26.3.0**. Always use version-specific documentation when following setup guides.

---

## Prerequisites

### Review Minimum System Requirements

| Requirement Type | Details |
|-------------------------|---------|
| Minimum GPU | **Self-hosted LLM Judge**: 6× (NVIDIA B200, H100, or A100 GPUs)<br>**Remote LLM Judge**: 2× (NVIDIA B200, H100, or A100 GPUs)<br>**Note:** GPU requirements are based on model deployment and customization needs. Refer to [System Requirements](../02-configuration.md#system-requirements) for detailed sizing guidance. |
| Cluster | **Docker Compose**: Single-node NVIDIA GPU cluster on Linux<br>**Helm**: Kubernetes cluster (1.25+) with GPU support |
| Disk Space | At least 200 GB free<br>**Note:** Disk space requirements vary based on log volume and model sizes. Refer to [System Requirements](../02-configuration.md#system-requirements) for detailed storage guidance.<br>**⚠️ Limited root disk space?** If your root filesystem (`/`) doesn't have 200 GB available, see [Using Alternative Storage When Root Disk is Full](../faq-troubleshooting.md#using-alternative-storage-when-root-disk-is-full). |
| Software | **Docker Compose**: Python 3.12, Docker Engine, Docker Compose v2<br>**Helm**: Kubernetes cluster, Helm 3.8+, kubectl |

> **📖 For complete system requirements:** Refer to [System Requirements](../02-configuration.md#system-requirements)

### Install Git LFS

You must have Git Large File Storage (LFS) installed to download the dataset files.

1. Install Git LFS package:

   ```bash
   sudo apt-get update && sudo apt-get install -y git-lfs
   ```

2. Initialize Git LFS in your environment:

   ```bash
   git lfs install
   ```

---

## Set Up the Data Flywheel Blueprint

<details>
<summary><strong>Who This Guide Is For</strong></summary>

This manual installation guide assumes you're comfortable working with Kubernetes and Helm. If you've deployed containerized applications before, you're in good shape!

Specifically, you should be familiar with:

- Deploying and managing Kubernetes workloads
- Reading container logs and inspecting pod status
- Working with Helm charts and customizing values files
- Using kubectl commands to manage cluster resources
- [NeMo Microservices installation requirements and prerequisites (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/get-started/setup/requirements.html#nemo-ms-get-started-requirements)

Some experience with Linux command-line tools will also help make the process smoother.

> **Ref**: [NMP Core Concepts (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/about/core-concepts/index.html)

</details>

### 1. Clone the Repository

Clone the repository and pull the dataset files:

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/data-flywheel.git
cd data-flywheel
git checkout main
git lfs pull
```

### 2. Environment Setup

Before configuring and starting services, set up all required API keys and environment variables.

#### Prerequisites: Obtain Required API Keys

##### NGC API Key

You must [generate a personal API key](https://org.ngc.nvidia.com/setup/api-keys) with the `NGC catalog` and `Public API Endpoints` services selected.

<!-- markdownlint-disable MD028 -->
> **📖 For more info:** See [Generating NGC API Keys](https://docs.nvidia.com/ngc/gpu-cloud/ngc-private-registry-user-guide/index.html#generating-api-key)

> **📖 For all required API keys:** Refer to [Required API Keys and Access](../02-configuration.md#required-api-keys-and-access)
<!-- markdownlint-enable MD028 -->

#### Set Environment Variables

1. Set up the NGC API key (generated in [Prerequisites](#prerequisites-obtain-required-api-keys) above):

   ```bash
   export NGC_API_KEY="<your-ngc-api-key>"
   ```

2. Set up the NVIDIA API key for remote configurations:

   Go to [build.nvidia.com](https://build.nvidia.com) and generate an NVIDIA API key, then export it:

   ```bash
   export NVIDIA_API_KEY="<your-nvidia-api-key>"
   ```

   > **Note:** The `NGC_API_KEY` is only needed for NGC login and container downloads. The `NVIDIA_API_KEY` is used for remote API access to NVIDIA services.

3. Set up the Hugging Face token for data uploading:

   Generate a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens), then export it:

   ```bash
   export HF_TOKEN="<your-huggingface-token>"
   ```

   > **Note:** The `HF_TOKEN` is required for data uploading functionality used during model training and evaluation.

4. <details>
   <summary><strong>Optional:</strong> Advanced Environment Variables</summary>

   For remote configurations, if you want to use different API keys for LLM judge and embedding services, you can set custom environment variables:

   ```bash
   # For remote LLM judge services
   export LLM_JUDGE_API_KEY=<your-llm-judge-api-key>

   # For remote embedding services  
   export EMB_API_KEY=<your-embedding-api-key>
   ```

   > **Note:** If you don't set these custom variables, the system will use `NVIDIA_API_KEY` as the default for both remote services.
   > **Tip:** You can use API keys from any provider (OpenAI, Anthropic, etc.) by setting them to `LLM_JUDGE_API_KEY` or `EMB_API_KEY` for remote configurations.
   > **📖 For complete environment setup:** Refer to [Environment Variables](../02-configuration.md#environment-variables)

   </details>

### 3. Configure Data Flywheel

Before starting services, it's important to understand the key configuration concepts and customize settings for your environment.

#### Configuration Overview

The Data Flywheel Blueprint uses a configuration file (`config/config.yaml`) organized into two parts:

**Part 1: System Configuration (Static)** — Settings loaded at startup, require restart to change:

- **NeMo Microservices**: Platform endpoints and namespace configuration
- **Logging**: Log verbosity levels
- **MLflow**: Experiment tracking and model management
- **LLM Judge**: Can be configured as either:
  - **Local deployment**: Runs as a NIM in your cluster (requires additional GPU resources)
  - **Remote deployment**: Uses external API endpoints (recommended for resource efficiency)

**Part 2: Job-Level Configuration (Dynamic)** — Can be managed via [Configuration Management API](../02-configuration.md#configuration-management-system):

- **Model Deployments (NIMs)**: Candidate models deployed locally for evaluation and customization
- **Data Split Settings**: Controls how data is split for training, validation, and evaluation
- **ICL Settings**: In-context learning example selection and parameters
- **Training Parameters**: Fine-tuning settings for model customization

#### Configuration File Setup

Review and modify the [configuration file](../../config/config.yaml) according to your requirements.

<!-- markdownlint-disable MD028 -->
> **📖 To configure NIM deployments, GPU allocation, data splits, and training parameters:** See [Part 2: Job-Level Configuration (Dynamic)](../02-configuration.md#part-2-job-level-configuration-dynamic) in the Configuration Guide.

> **📖 For complete configuration reference:** See the [Configuration Guide](../02-configuration.md)
<!-- markdownlint-enable MD028 -->

### 4. Start Services

Choose your deployment method based on your environment and requirements:

#### Choose Your Deployment Method

| Method | Best For | Requirements |
|--------|----------|--------------|
| **NMP Helm Chart + Docker Compose** | Development, testing | Docker Engine, Docker Compose v2, NMP deployed via Helm |
| **Complete DFW Helm Chart** | Unified deployment, Kubernetes-native environments  | Kubernetes cluster (1.25+), Helm 3.8+, kubectl |

---

#### Prerequisites

Before deploying, ensure you have the following installed:

- [minikube](https://minikube.sigs.k8s.io/docs/start/) - For local Kubernetes deployment
- Docker 27 or later - Container runtime
- NVIDIA GPU Driver 560.35.03 or higher - [Driver Installation Guide](https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/index.html)
- NVIDIA Container Toolkit v1.16.2 or higher - [Installation Guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Helm 3.8+ - Kubernetes package manager
- kubectl - Kubernetes command-line tool
- All API keys set as environment variables (see [Environment Setup](#2-environment-setup) above)

---

#### Option A: NMP Helm Chart + Docker Compose Deployment

This deployment mode requires NMP to be deployed first using its Helm chart, followed by Data Flywheel services using Docker Compose. Ideal for development and testing.

##### Step 1: Deploy NMP

Before starting Docker Compose services, you must deploy NMP using one of the following methods:

<details>
<summary><strong>Method 1: Using the Helper Script (Quick Setup)</strong></summary>

Use the provided helper script to automate NMP setup on a local Minikube cluster:

```bash
chmod +x ./scripts/deploy-nmp.sh
./scripts/deploy-nmp.sh
```

> **Note**: This is a repository-specific helper script for local Minikube deployments and is distinct from the official NMP deployment scripts. It performs the following:
>
> - **Pre-flight Checks**: Verifies `kubectl`, `minikube`, and `docker` are installed and checks Minikube status.
> - **Authentication**: Prompts for `NGC_API_KEY` if not set, creates a Kubernetes secret named `ngc-api`.
> - **Resource Validation**: Checks for required GPU/disk resources and software versions.

**Troubleshooting:**
When run with the `--progress` flag, the script writes full logs to `/tmp/nemo-deploy.log`. If you encounter issues, refer to the logs and check the [FAQ and Troubleshooting Guide](../faq-troubleshooting.md). Ensure your `ngc-api` secret is correctly created and you have valid NGC credentials.

</details>

<details>
<summary><strong>Method 2: Manual Installation (Recommended for Production)</strong></summary>

For more control or production deployments, follow the official [NeMo Microservices Platform Prerequisites (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/get-started/setup/index.html) guide.

> **⚠️ Important: NMP Version & Configuration Requirements**
>
> **1. Use NMP Version 26.3.0 (Early Release)**
>
> Data Flywheel Blueprint has been tested and is compatible with NMP version **26.3.0 (Early Release)**, which is packaged as the `nemo-platform` Helm chart `2.1.0-nightly-20260422`. This Early Release chart is hosted on the **internal `nvidian/nemo-llm` NGC org**, so your `NGC_API_KEY` must have access to that org.
>
> ```bash
> # Pull the 26.3.0 EA chart from the internal NGC org
> ngc registry chart pull --org nvidian 'nvidian/nemo-llm/nemo-platform:2.1.0-nightly-20260422'
> ```
>
> **2. Follow the Manual Installation Guide**
>
> Use the [Install Manually (v26.03.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/get-started/setup/minikube/minikube-manual.html) guide.
>
> **3. Use the Recommended Values File**
>
> Install NMP with the [`deploy/nmp-demo-values.yaml`](../../deploy/nmp-demo-values.yaml) file to enable model customization and prevent job timeouts:
>
> ```bash
> helm --namespace default install \
>    nemo https://helm.ngc.nvidia.com/nvidian/nemo-llm/charts/nemo-platform-2.1.0-nightly-20260422.tgz \
>    --username='$oauthtoken' --password="$NGC_API_KEY" \
>    -f deploy/nmp-demo-values.yaml \
>    --set guardrails.guardrails.nvcfAPIKeySecretName="nvidia-api" \
>    --wait --timeout 20m
> ```
>
> **What this configuration provides:**
>
> | Setting | Value | Purpose |
> |---------|-------|---------|
> | **Extended Job Timeout** | `MAX_PENDING_JOB_MINUTES=180` | Prevents premature cancellation of multi-stage customization pipelines. Adjust based on your dataset size and expected training time. |
> | **Pre-enabled Models** | See list below | Models ready for customization out of the box |
>
> **Pre-enabled models:**
> - `meta/llama-3.2-1b-instruct@2.0`
> - `meta/llama-3.2-3b-instruct@2.0`
> - `meta/llama-3.1-8b-instruct@2.0`
> - `nvidia/llama-3.2-nv-embedqa-1b@v2`
>
> Any other NMP-supported model can be enabled by adding it to `deploy/nmp-demo-values.yaml` with `enabled: true`.
>
> **Why the timeout matters:** The default 30-minute timeout is insufficient for multi-stage customization pipelines. Without this configuration, jobs may fail with "deadline exceeded" errors even when training completes successfully.
>
> **📖 Troubleshooting:** See [Job Cancelled: Deadline Exceeded](../faq-troubleshooting.md#job-cancelled-deadline-exceeded) in the FAQ.
>
> **Note:** The Data Flywheel Blueprint automatically manages model deployment—spinning up or down models in the configured namespace. No manual intervention is required.

</details>

##### Step 2: Start Docker Compose Services

**Quick Start Options:**

1. **Recommended:** Use the [launch script](../../scripts/run.sh):

   ```bash
   ./scripts/run.sh
   ```

   > **⏱️ Initial Deployment Time:**
   >
   > If you are using a local (on-prem) LLM judge configuration with the Meta Llama 3.3 70B Instruct model, the initial startup will take **10-20 minutes or more** as the model needs to be downloaded and initialized. The actual time depends on your network speed and bandwidth. This only applies to the first deployment. Subsequent restarts will be faster.

2. Use the [development script](../../scripts/run-dev.sh):

   This script runs additional services for observability:

   - `flower`: A web UI for monitoring Celery tasks and workers
   - `kibana`: A visualization dashboard for exploring data stored in Elasticsearch

   ```bash
   ./scripts/run-dev.sh
   ```

3. Use Docker Compose directly:

   > **Set the backend profile.** Elasticsearch (the NMP data pool) is gated behind
   > the `nmp` Compose profile, so an NMP deployment must enable it via
   > `COMPOSE_PROFILES=nmp` (or run `./scripts/run.sh nmp`, which sets it for you).
   > Combine profiles with a comma, e.g. `COMPOSE_PROFILES=nmp,mlflow`.

   ```bash
   export COMPOSE_PROFILES=nmp && \
   docker compose -f ./deploy/docker-compose.yaml down && \
   docker compose -f ./deploy/docker-compose.yaml build api ingestion-plugins-api && \
   docker compose -f ./deploy/docker-compose.yaml up -d --no-deps --no-attach mongodb
   ```

   **To start with MLflow enabled:**

  ```bash
  export COMPOSE_PROFILES=nmp,mlflow && docker compose -f ./deploy/docker-compose.yaml build api ingestion-plugins-api && \
  docker compose -f ./deploy/docker-compose.yaml up -d
  ```

**Using Environment Files:**

If you want to use an `.env` file to store all environment variables, you have two options:

- **Option 1:** Store the `.env` file in the `deploy/` folder (Docker Compose default location):

```bash
# Place your .env file at deploy/.env (include COMPOSE_PROFILES=nmp in it, or export it first)
export COMPOSE_PROFILES=nmp && \
docker compose -f ./deploy/docker-compose.yaml build api ingestion-plugins-api && \
docker compose -f ./deploy/docker-compose.yaml up -d
```

- **Option 2:** Use a custom `.env` file location with the `--env-file` argument:

```bash
# Example: Using .env file at the root of the repo (set COMPOSE_PROFILES=nmp in the .env file)
docker compose -f ./deploy/docker-compose.yaml --env-file .env build api ingestion-plugins-api && \
docker compose -f ./deploy/docker-compose.yaml --env-file .env up -d
```

   <details>
   <summary>Example `.env` file contents</summary>

   **Example `.env` file contents:**

   ```bash
   # API Keys for NVIDIA services
   NVIDIA_API_KEY=nvapi-your-nvidia-api-key-here
   NGC_API_KEY=nvapi-your-ngc-api-key-here
   
   # Hugging Face token for data uploading
   HF_TOKEN=hf_your-huggingface-token-here
   
   # Optional: Override API keys for specific services
   LLM_JUDGE_API_KEY=your-custom-llm-judge-api-key
   EMB_API_KEY=your-custom-embedding-api-key
   
   # Docker Compose profiles — `nmp` enables Elasticsearch (the NMP data pool);
   # add `mlflow` to also start MLflow. Comma-separated.
   COMPOSE_PROFILES=nmp,mlflow
   
   # Optional: Database connection overrides
   MONGODB_URL=mongodb://localhost:27017
   REDIS_URL=redis://localhost:6379/0
   ELASTICSEARCH_URL=http://localhost:9200
   ```

   > **Note:** The `--env-file` argument allows you to specify any `.env` file location in your repository.

   </details>

   > **MLflow Integration**
   >
   > MLflow is controlled via the `COMPOSE_PROFILES` environment variable:
   > - Add `mlflow` to the profile list to enable both the MLflow Docker service and configuration. Keep `nmp` in the list so Elasticsearch still starts, e.g. `COMPOSE_PROFILES=nmp,mlflow`.
   > - The MLflow service will be available at `http://localhost:5000`
   > - MLflow configuration is automatically enabled when the profile is active

**Verification:**

After starting the services, verify that they are running correctly:

```bash
# Check running containers
docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"

# Test API connectivity
curl http://localhost:8000/api/jobs -s | jq .
```

<details>
<summary>Expected output from <code>docker ps</code></summary>

```text
CONTAINER ID   NAMES                           STATUS
2afff821649e   deploy-celery_parent_worker-1   Up 13 seconds
87b8bbcdf898   deploy-api-1                    Up 13 seconds
736ffbb4e2cd   deploy-celery_worker-1          Up 13 seconds
bcaa9c424d28   deploy-mongodb-1                Up 14 seconds (healthy)
9a0399c7b64b   deploy-elasticsearch-1          Up 14 seconds
2ceb682ce958   deploy-redis-1                  Up 14 seconds
```

</details>

**✅ Checking Logs for Deployment Issues:**

If you encounter any issues during or after deployment, check the logs for the API and Celery worker services:

```bash
# Check API logs
docker logs deploy-api-1 --tail 100

# Check Celery worker logs
docker logs deploy-celery_worker-1 --tail 100

# Check Celery parent worker logs
docker logs deploy-celery_parent_worker-1 --tail 100

# Follow logs in real-time (useful for debugging)
docker logs -f deploy-api-1
docker logs -f deploy-celery_worker-1
```

These logs will help identify any configuration issues, connectivity problems, or service startup failures.

**Stopping Docker Compose Services:**

```bash
./scripts/stop.sh
```

> **📖 For Docker Compose cleanup:** Refer to [Cleanup](#cleanup) section below

---

#### Option B: Complete DFW Helm Chart Deployment

This deployment mode uses a **single Helm chart that includes NMP as a dependency**, providing a unified installation on Kubernetes  with all dependencies managed automatically.

**Quick Start:**

1. **Automated Deployment (Recommended):**

   Use the provided deployment script for a fully automated setup:

   ```bash
   # Ensure all required environment variables are set
   export NGC_API_KEY="your_ngc_api_key_here"
   export NVIDIA_API_KEY="your_nvidia_api_key_here"
   export HF_TOKEN="your_hugging_face_token_here"
   
   # Optional: Set custom API keys for specific services
   export LLM_JUDGE_API_KEY="your_llm_judge_api_key_here"  # Defaults to NVIDIA_API_KEY
   export EMB_API_KEY="your_emb_api_key_here"              # Defaults to NVIDIA_API_KEY
   
   # Run the automated deployment script
   ./scripts/deploy-helm.sh
   ```

   > **⏱️ Initial Deployment Time:**
   >
   > If you are using a local (on-prem) LLM judge configuration with the Meta Llama 3.3 70B Instruct model, the initial deployment will take **10-20 minutes or more** as the model needs to be downloaded and initialized. The actual time depends on your network speed and bandwidth. This only applies to the first deployment. Plan for this additional time during your initial cluster setup.

   The script automatically:
   - Validates required tools and environment variables
   - Creates and configures minikube cluster with GPU support (if needed)
   - Sets up Kubernetes namespace and DNS resolution
   - Installs Helm chart with all configurations (including NMP as a dependency)
   - Waits for pods to be ready
   - Sets up automatic port forwarding

   > **📖 Troubleshooting:** If port forwarding fails or disconnects, refer to [Port Forwarding Not Working](../faq-troubleshooting.md#port-forwarding-not-working) for manual setup and alternative solutions.

2. **Manual Deployment:**

   For manual control or custom configurations:

   ```bash
   # Navigate to chart directory
   cd deploy/helm/data-flywheel
   
   # Update chart dependencies
   helm dependency update
   
   # Install with your API keys
   helm upgrade --install data-flywheel . \
     --set secrets.ngcApiKey=$NGC_API_KEY \
     --set secrets.nvidiaApiKey=$NVIDIA_API_KEY \
     --set secrets.hfToken=$HF_TOKEN \
     --set secrets.llmJudgeApiKey=$LLM_JUDGE_API_KEY \
     --set secrets.embApiKey=$EMB_API_KEY \
     --namespace nv-nvidia-blueprint-data-flywheel \
     --timeout 20m
   ```

3. **Increase Proxy Body Size for Large Datasets**

   For uploading larger datasets, this step is necessary to prevent upload errors due to size limits. The following command increases the ingress proxy body size limit to `2g` (adjust this value if you need to support larger files):

   ```bash
   kubectl patch ingress nemo-microservices-helm-chart -n default --type='json' -p='[{"op": "add", "path": "/metadata/annotations/nginx.ingress.kubernetes.io~1proxy-body-size", "value": "2g"}]'
   ```

**Accessing Services:**

After deployment, set up port forwarding to access services:

```bash
# Forward API service
kubectl port-forward service/df-api-service 8000:8000

# Forward MLflow service (if enabled)
kubectl port-forward service/df-mlflow-service 5000:5000
```

Or use the automated port forwarding script:

```bash
./scripts/helm/forward-ports.sh \
  df-api-service 8000 \
  df-mlflow-service 5000
```

**Verification:**

```bash
# Check pod status
kubectl get pods -n nv-nvidia-blueprint-data-flywheel

# Test API connectivity
curl http://localhost:8000/api/jobs -s | jq .
```

> **💡 Understanding Pod Status**: When checking pods, ensure service pods show **READY `1/1`** AND **STATUS `Running`** to be truly ready. Note that `customizer-downloader-*` pods showing `Completed` with `0/1` ready is expected behavior. See [Understanding Pod Readiness](../faq-troubleshooting.md#pods-not-starting) for details.

**✅ Checking Logs for Deployment Issues:**

If you encounter any issues during or after deployment, check the logs for the API and Celery worker services:

**Helm Deployment:**
```bash
# Check API logs
kubectl logs -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100

# Check Celery worker logs
kubectl logs -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100

# Check Celery parent worker logs
kubectl logs -l app=df-celery-parent-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
```

These logs will help identify any configuration issues, connectivity problems, or service startup failures.

> **📖 For complete Helm deployment instructions, configuration options, troubleshooting, and operations:** Refer to [Helm Installation Guide](../10-helm-installation.md)

---

> **⏳ Important:** After starting the Data Flywheel services, wait 15-20 minutes for all deployments to be ready before starting your first job. This delay is normal during the initialization phase as the system sets up model deployments and services. You can verify services are ready by checking pod status (for Helm deployments using `kubectl get pods`) or service health endpoints. For Helm deployments, see the Verification section above. For Docker Compose deployments, check service logs to confirm all services are running.

<details>
<summary>🧹 Cleanup Procedures</summary>

## Cleanup

Cleanup procedures differ based on your deployment method:

### Docker Compose Cleanup

#### 1. Stop Services

When you're done using the services, stop them using the stop script:

```bash
./scripts/stop.sh
```

#### 2. Resource Cleanup

**Automatic Cleanup (During System Shutdown)**

The system automatically cleans up running resources through the `CleanupManager` when Celery workers are shut down gracefully. This happens automatically when:
- Docker containers are stopped (`docker compose down`)
- Celery workers receive shutdown signals (SIGTERM, SIGINT)
- The system is restarted

The `CleanupManager` automatically:
1. **Finds all running flywheel runs** from the database with `PENDING` or `RUNNING` status
2. **Identifies running NIMs** with `RUNNING` deployment status for each flywheel run
3. **Cancels active customization jobs** for each running NIM
4. **Shuts down NIM deployments** using the DMS client
5. **Shuts down LLM judge deployments** (if running locally)
6. **Marks all resources as cancelled** in the database with appropriate error messages

The cleanup is triggered by the `worker_shutting_down` signal in the Celery worker, ensuring that all resources are properly cleaned up even during unexpected shutdowns.

For technical details about the automatic cleanup process, see the [Architecture Overview](../01-architecture.md#automatic-resource-cleanup).

#### 3. Clear Volumes

Clear all service volumes (Elasticsearch, Redis, and MongoDB) using the [clear volumes script](../../scripts/clear_all_volumes.sh):

```bash
./scripts/clear_all_volumes.sh
```

### Helm (Kubernetes) Cleanup

#### 1. Uninstall Helm Chart

Remove the Data Flywheel Blueprint deployment:

```bash
# Uninstall the Helm chart
helm uninstall data-flywheel -n nv-nvidia-blueprint-data-flywheel

# Clean up NIM services and finalizers
kubectl get nimservices -n nv-nvidia-blueprint-data-flywheel -o name | \
  xargs -I {} kubectl patch {} -p '{"metadata":{"finalizers":[]}}' --type=merge

kubectl delete nimservices --all -n nv-nvidia-blueprint-data-flywheel --force --grace-period=0

# Clean up remaining resources
kubectl delete all --all -n nv-nvidia-blueprint-data-flywheel --force --grace-period=0
kubectl delete pvc --all -n nv-nvidia-blueprint-data-flywheel --force --grace-period=0

# Remove namespace
kubectl patch namespace "nv-nvidia-blueprint-data-flywheel" \
  -p '{"spec":{"finalizers":null}}' --type=merge
kubectl delete ns nv-nvidia-blueprint-data-flywheel
```

#### 2. Clear Data (Optional)

Use the provided cleanup scripts to clear data from specific services:

```bash
# Clear all data from all services
./scripts/helm/clear-all-data.sh

# Or clear specific services
./scripts/helm/clear-mongodb-data.sh
./scripts/helm/clear-es-data.sh
./scripts/helm/clear-mlflow-data.sh
./scripts/helm/clear-redis-data.sh
```

> **Note:** All cleanup scripts accept an optional `NAMESPACE` environment variable. If not provided, they default to `nv-nvidia-blueprint-data-flywheel`.

> **📖 For complete Helm cleanup procedures:** Refer to [Helm Installation Guide - Cleanup](../10-helm-installation.md#helm-chart-cleanup)

### NMP Cleanup

You can remove NMP when you're done using the platform by following the official [Uninstall NeMo Microservices Helm Chart (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/set-up/deploy-as-platform/uninstall-platform-helm-chart.html) guide.

</details>

<details>
<summary>🔍 Troubleshooting</summary>

## Troubleshooting

If you encounter any issues:

1. Check that all environment variables are properly set.
   - See the [Environment Variables section](../02-configuration.md#environment-variables) for the complete list of required and optional variables.
2. Make sure all prerequisites are installed and configured correctly.
3. Verify that you have the necessary permissions and access to all required resources.
4. **For detailed troubleshooting**: Refer to the comprehensive [FAQ and Troubleshooting Guide](../faq-troubleshooting.md) for solutions to common setup, deployment, and runtime issues.

</details>

## Next Steps

- Ready to work with LLM models? Refer to [LLM Pipeline](llm-pipeline/index.md)
- Ready to work with embedding models? Refer to [Embedding Pipeline](embedding-pipeline/index.md)
