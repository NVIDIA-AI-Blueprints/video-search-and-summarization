# FAQ and Troubleshooting Guide

This guide provides solutions to common issues encountered when working with the Data Flywheel Blueprint. Use this resource to quickly resolve problems and get back to optimizing your AI models.

## Table of Contents

- [FAQ and Troubleshooting Guide](#faq-and-troubleshooting-guide)
  - [Table of Contents](#table-of-contents)
  - [Quick Links](#quick-links)
  - [Common Setup Issues](#common-setup-issues)
    - [NGC Login and Authentication](#ngc-login-and-authentication)
    - [Docker and Container Issues](#docker-and-container-issues)
    - [NeMo Microservices Platform (NMP) Deployment](#nemo-microservices-platform-nmp-deployment)
    - [Environment Variable Configuration](#environment-variable-configuration)
  - [Service Connection Issues](#service-connection-issues)
    - [API Server Not Accessible](#api-server-not-accessible)
    - [Elasticsearch Connection Problems](#elasticsearch-connection-problems)
    - [MongoDB Connection Issues](#mongodb-connection-issues)
    - [Redis Connection Issues](#redis-connection-issues)
  - [Data Loading Issues](#data-loading-issues)
    - [Failed to Load Data into Elasticsearch](#failed-to-load-data-into-elasticsearch)
    - [Dataset Format Validation Errors](#dataset-format-validation-errors)
    - [Embedding Data Loading Issues](#embedding-data-loading-issues)
    - [DPO Data Loading Issues](#dpo-data-loading-issues)
  - [Job Creation and Execution Issues](#job-creation-and-execution-issues)
    - [Job Creation Failed: No Data Found](#job-creation-failed-no-data-found)
    - [Job Stuck in Pending Status](#job-stuck-in-pending-status)
    - [Job Failed: Insufficient GPU Resources](#job-failed-insufficient-gpu-resources)
    - [Job Cancelled: Deadline Exceeded](#job-cancelled-deadline-exceeded)
    - [Job Failed: Model Deployment Timeout](#job-failed-model-deployment-timeout)
  - [Model Deployment Issues](#model-deployment-issues)
    - [NIM Deployment Failed](#nim-deployment-failed)
    - [Model Not Responding](#model-not-responding)
    - [Customization Job Failed](#customization-job-failed)
  - [Evaluation Issues](#evaluation-issues)
    - [LLM Judge Not Available](#llm-judge-not-available)
    - [Evaluation Timeout](#evaluation-timeout)
    - [Remote API Rate Limiting](#remote-api-rate-limiting)
  - [Notebook-Specific Issues](#notebook-specific-issues)
    - [Brev Launchable Deployment Issues](#brev-launchable-deployment-issues)
    - [Kernel Crashes or Out of Memory](#kernel-crashes-or-out-of-memory)
    - [Git LFS Pull Failed](#git-lfs-pull-failed)
  - [Configuration Issues](#configuration-issues)
    - [Invalid Configuration File](#invalid-configuration-file)
    - [Model Not Enabled for Customization](#model-not-enabled-for-customization)
    - [MLflow Not Starting](#mlflow-not-starting)
  - [Performance and Resource Issues](#performance-and-resource-issues)
    - [Jobs Taking Too Long](#jobs-taking-too-long)
    - [High Memory Usage](#high-memory-usage)
    - [Disk Space Running Low](#disk-space-running-low)
    - [Using Alternative Storage When Root Disk is Full](#using-alternative-storage-when-root-disk-is-full)
  - [Kubernetes/Helm Deployment Issues](#kuberneteshelm-deployment-issues)
    - [Pods Not Starting](#pods-not-starting)
    - [Port Forwarding Not Working](#port-forwarding-not-working)
    - [PVC Mount Issues](#pvc-mount-issues)
  - [Best Practices and Tips](#best-practices-and-tips)
    - [Monitoring Job Progress](#monitoring-job-progress)
    - [Cleanup and Maintenance](#cleanup-and-maintenance)
    - [Debugging Tips](#debugging-tips)
  - [Getting Help](#getting-help)

---

## Quick Links

- [Installation Guide](get-started/install-and-setup.md)
- [Configuration Guide](02-configuration.md)
- [Architecture Overview](01-architecture.md)
- [API Reference](06-api-reference.md)
- [Limitations & Best Practices](04-limitations-best-practices.md)

---

## Common Setup Issues

### NGC Login and Authentication

**Problem**: `docker login nvcr.io` fails with authentication error

**Solutions**:

1. **Verify NGC API Key**:
   ```bash
   # Check if NGC_API_KEY is set
   echo $NGC_API_KEY
   
   # If empty, set it
   export NGC_API_KEY="your-ngc-api-key"
   ```

2. **Login with correct format**:
   ```bash
   # Use '$oauthtoken' as username (literal string, not a variable)
   echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
   ```

3. **Generate a new API key**:
   - Go to [NGC Personal API Keys](https://org.ngc.nvidia.com/setup/api-keys)
   - Click "Generate Personal Key"
   - Ensure `NGC catalog` and `Public API Endpoints` services are selected
   - Copy the new key and update your environment

**Common Mistakes**:
- Using `$oauthtoken` without quotes (should be `'$oauthtoken'`)
- Using NGC_API_KEY without the `nvapi-` prefix
- API key expired or not activated

---

### Docker and Container Issues

**Problem**: Docker services fail to start or containers crash

**Solutions**:

1. **Check Docker is running**:
   ```bash
   docker ps
   # If error, start Docker daemon
   sudo systemctl start docker
   ```

2. **Check available resources**:
   ```bash
   # Check disk space
   df -h
   
   # Check Docker disk usage
   docker system df
   ```

3. **Clean up Docker resources**:
   ```bash
   # Remove stopped containers
   docker container prune -f
   
   # Remove unused images
   docker image prune -a -f
   
   # Remove unused volumes
   docker volume prune -f
   ```

4. **Restart services**:
   ```bash
   # Stop all services
   ./scripts/stop.sh
   
   # Start fresh
   ./scripts/run.sh
   ```

**Problem**: Port already in use errors

**Solutions**:

1. **Check what's using the port**:
   ```bash
   # Check port 8000 (API)
   sudo lsof -i :8000
   
   # Check port 9200 (Elasticsearch)
   sudo lsof -i :9200
   ```

2. **Stop conflicting services** or **change ports** in `docker-compose.yaml`

---

### NeMo Microservices Platform (NMP) Deployment

**Problem**: NMP deployment fails or times out

**Solutions**:

1. **Use automated script**:
   ```bash
   chmod +x scripts/deploy-nmp.sh
   ./scripts/deploy-nmp.sh
   ```

2. **Manual deployment troubleshooting**:
   ```bash
   # Check minikube status
   minikube status
   
   # Check NMP pods (Docker Compose: default namespace, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default
   
   # Check specific service logs (replace <service-name> with nemo-customizer, nemo-evaluator, etc.)
   kubectl logs -n default -l app.kubernetes.io/name=<service-name>
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`
   
   > **💡 Tip - Understanding Pod Status**: When checking pods, ensure service pods show **READY `1/1`** AND **STATUS `Running`** to be truly ready. A pod can be `Running` but still show `0/1` ready while health checks are initializing. Customizer model downloader pods (`customizer-downloader-*`) are an exception—they should show STATUS `Completed` and READY will stay at `0/1`. See [Pods Not Starting](#pods-not-starting) for detailed explanation.

3. **Common NMP issues**:
   - **Insufficient resources**: Ensure your cluster has enough GPUs and memory
   - **Namespace conflicts**: Use unique namespaces for different deployments
   - **Version mismatch**: Verify you're using NMP 26.3.0 or compatible version
   - **`kube-rbac-proxy` ImagePullBackOff**: See [kube-rbac-proxy Image Pull Failure](#kube-rbac-proxy-image-pull-failure) below

4. **Verify NMP configuration**:
   ```bash
   # Check config.yaml has correct NMP endpoints
   cat config/config.yaml | grep -A 5 "nmp_config"
   ```

**Expected values**:
```yaml
nmp_config:
  nemo_base_url: "http://nemo.test"
  nim_base_url: "http://nim.test"
  datastore_base_url: "http://data-store.test"
```

> **📖 For detailed NMP troubleshooting**: Refer to the official [NeMo Microservices Troubleshooting Guide (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/troubleshooting/index.html) for component-specific issues including Customizer, Evaluator, Data Designer, Guardrails, and platform setup.

> **📝 Note on Deployment Methods and Namespaces**:
> 
> The Data Flywheel Blueprint supports two deployment methods with different namespace configurations:
> 
> **1. Docker Compose Deployment** (Development/Single-node):
> - **NMP (NeMo Microservices)**: Deployed separately via minikube to `default` namespace
> - **Data Flywheel services**: Run via docker-compose on localhost
> - **Setup script**: `scripts/deploy-nmp.sh` for NMP, `scripts/run.sh` for Data Flywheel
> 
> **2. Helm Chart Deployment** (Production/Kubernetes):
> - **Everything deployed together** to `nv-nvidia-blueprint-data-flywheel` namespace
> - **Includes**: NMP + Data Flywheel + all infrastructure services
> - **Setup script**: `scripts/deploy-helm.sh`
> - **Helm chart**: `deploy/helm/data-flywheel/` (includes NMP as a dependency)
> 
> Throughout this guide, namespace references assume the Docker Compose method. For Helm deployments, replace `default` with `nv-nvidia-blueprint-data-flywheel` for all NMP-related commands.

---

### kube-rbac-proxy Image Pull Failure

**Problem**: `data-flywheel-nemo-operator-controller-manager` is stuck in `ImagePullBackOff` for the `kube-rbac-proxy` sidecar — `gcr.io/kubebuilder/` was deprecated and removed by Google.

**Fix**:

```bash
kubectl set image deployment/data-flywheel-nemo-operator-controller-manager \
  kube-rbac-proxy=quay.io/brancz/kube-rbac-proxy:v0.18.1 \
  -n nv-nvidia-blueprint-data-flywheel
```

> **Note**: This fix is already included in `deploy/helm/data-flywheel/values.yaml` and is applied automatically when deploying via `scripts/deploy-helm.sh`.

---

### Environment Variable Configuration

**Problem**: Services can't find required environment variables

**Solutions**:

1. **Create .env file** in the `deploy/` directory:
   ```bash
   cat > deploy/.env << 'EOF'
   NGC_API_KEY=nvapi-your-ngc-api-key
   NVIDIA_API_KEY=nvapi-your-nvidia-api-key
   HF_TOKEN=hf_your-hugging-face-token
   COMPOSE_PROFILES=mlflow
   EOF
   ```

2. **Source environment variables**:
   ```bash
   # Export variables
   export NGC_API_KEY="your-key"
   export NVIDIA_API_KEY="your-key"
   export HF_TOKEN="your-token"
   ```

3. **Verify variables are set**:
   ```bash
   # Check all required variables
   env | grep -E "NGC_API_KEY|NVIDIA_API_KEY|HF_TOKEN"
   ```

4. **Docker Compose with env file**:
   ```bash
   docker compose -f ./deploy/docker-compose.yaml --env-file deploy/.env build api ingestion-plugins-api && \
   docker compose -f ./deploy/docker-compose.yaml --env-file deploy/.env up -d
   ```

---

## Service Connection Issues

### API Server Not Accessible

**Problem**: `curl http://localhost:8000/api/jobs` fails with "Connection refused"

**Solutions**:

1. **Check if API server is running**:
   ```bash
   # Docker Compose
   docker ps | grep api
   
   # Kubernetes (Helm deployment)
   kubectl get pods -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel
   ```

2. **✅ Check API logs for errors**:
   
   **Docker Compose:**
   ```bash
   # Check recent logs
   docker logs deploy-api-1 --tail 100
   
   # Follow logs in real-time
   docker logs -f deploy-api-1
   ```
   
   **Helm (Kubernetes):**
   ```bash
   # Check recent logs (Data Flywheel is in nv-nvidia-blueprint-data-flywheel namespace)
   kubectl logs -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   
   # Follow logs in real-time
   kubectl logs -f -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel
   ```
   
   Look for error messages indicating:
   - Database connection failures (MongoDB, Elasticsearch, Redis)
   - Missing environment variables
   - Port binding issues
   - Configuration errors

3. **Wait for services to be ready**:
   - After starting services, **wait 4-5 minutes** for all deployments to initialize
   - Check API is ready:
     ```bash
     curl http://localhost:8000/api/jobs
     ```

4. **Kubernetes port forwarding** (Helm deployment):
   ```bash
   # Set up port forwarding (Data Flywheel is in nv-nvidia-blueprint-data-flywheel namespace)
   kubectl port-forward service/df-api-service 8000:8000 -n nv-nvidia-blueprint-data-flywheel
   ```

---

### Elasticsearch Connection Problems

**Problem**: Cannot connect to Elasticsearch

**Solutions**:

1. **Verify Elasticsearch is running**:
   ```bash
   # Check service status
   docker ps | grep elasticsearch
   
   # Check cluster health
   curl http://localhost:9200/_cluster/health?pretty
   ```

2. **Check Elasticsearch version compatibility**:
   ```bash
   # Check ES container version
   docker exec deploy-elasticsearch-1 bin/elasticsearch --version
   
   # Check Python client version
   pip show elasticsearch
   ```
   
   **Important**: Ensure the Python `elasticsearch` package version matches your Elasticsearch container version. For example, if using Elasticsearch 8.17.2:
   ```bash
   pip install elasticsearch==8.17.2
   ```

3. **Check Elasticsearch logs**:
   ```bash
   docker logs deploy-elasticsearch-1
   ```

4. **Common Elasticsearch issues**:
   - **Out of memory**: Increase heap size in docker-compose.yaml
   - **Disk space**: Elasticsearch needs at least 512MB reserved
   - **Port conflict**: Ensure port 9200 is not in use
   - **Version mismatch**: Python client and ES server versions must be compatible

5. **Restart Elasticsearch**:
   ```bash
   docker restart deploy-elasticsearch-1
   ```

---

### MongoDB Connection Issues

**Problem**: MongoDB connection failed or timeout

**Solutions**:

1. **Check MongoDB is running**:
   ```bash
   docker ps | grep mongodb
   ```

2. **Test connection**:
   ```bash
   # Using docker exec
   docker exec -it deploy-mongodb-1 mongosh
   
   # Or using connection string
   mongosh "mongodb://localhost:27017"
   ```

3. **Check MongoDB logs**:
   ```bash
   docker logs deploy-mongodb-1
   ```

4. **Verify connection string** in environment variables:
   ```bash
   echo $MONGODB_URL
   # Should be: mongodb://localhost:27017
   ```

---

### Redis Connection Issues

**Problem**: Redis connection errors or task queue not working

**Solutions**:

1. **Check Redis is running**:
   ```bash
   docker ps | grep redis
   ```

2. **Test Redis connection**:
   ```bash
   # Using redis-cli
   docker exec -it deploy-redis-1 redis-cli ping
   # Should return: PONG
   ```

3. **Check Celery worker logs**:
   ```bash
   docker logs deploy-celery_worker-1
   ```

4. **Restart Redis and workers**:
   ```bash
   docker restart deploy-redis-1
   docker restart deploy-celery_worker-1
   docker restart deploy-celery_parent_worker-1
   ```

---

## Data Loading Issues

### Failed to Load Data into Elasticsearch

**Problem**: Data loading script fails or no data is indexed

**Solutions**:

1. **Verify file path and format**:
   ```bash
   # Check file exists
   ls -lh data/your-data-file.jsonl
   
   # Verify JSONL format (each line should be valid JSON)
   head -n 1 data/your-data-file.jsonl | jq .
   ```

2. **Check required fields** are present:
   ```bash
   # For LLM data: timestamp, workload_id, client_id, request, response
   head -n 1 data/your-data-file.jsonl | jq 'keys'
   ```

3. **Check Elasticsearch index**:
   ```bash
   # List indices
   curl http://localhost:9200/_cat/indices?v
   
   # Check document count
   curl http://localhost:9200/flywheel/_count?pretty
   ```

4. **Load data programmatically**:
   ```python
   from src.scripts.load_test_data import load_data_to_elasticsearch
   
   load_data_to_elasticsearch(
       file_path="data/your-data.jsonl",
       workload_id="your-workload",
       client_id="your-client"
   )
   ```

---

### Dataset Format Validation Errors

**Problem**: "Invalid data format" or schema validation errors

**Solutions**:

1. **LLM Data Format** - Each document must have:
   ```json
   {
     "timestamp": 1715854074,
     "workload_id": "tool_call_routing",
     "client_id": "acme-prod",
     "request": {
       "model": "llama-3-70b-instruct",
       "messages": [{"role": "user", "content": "Your prompt"}]
     },
     "response": {
       "choices": [{"message": {"content": "Response text"}}]
     }
   }
   ```
   
   **Note**: If your data is in a different format (e.g., just `messages` field), the loading script can auto-transform it. The script requires `workload_id` and `client_id` as function parameters.

2. **DPO Data Format** - Each document must have:
   ```json
   {
     "prompt": "Your question",
     "chosen_response": "Better answer",
     "rejected_response": "Worse answer"
   }
   ```
   
   **Optional fields** (can be included in data or provided as function parameters):
   ```json
   {
     "prompt": "Your question",
     "chosen_response": "Better answer",
     "rejected_response": "Worse answer",
     "workload_id": "dpo-task",
     "client_id": "client-1"
   }
   ```
   
   **Note**: 
   - `workload_id` is **required** - must be either in the data or passed as a function parameter
   - `client_id` is **optional** - can be in the data or passed as a function parameter
   - If both are provided (in data and as parameter), the parameter value takes precedence

3. **Validate your data**:
   ```python
   import json
   
   # Check each line is valid JSON
   with open("data/your-data.jsonl", "r") as f:
       for i, line in enumerate(f, 1):
           try:
               data = json.loads(line)
               # Check required fields
               assert "workload_id" in data
               assert "client_id" in data
           except Exception as e:
               print(f"Error on line {i}: {e}")
   ```

---

### Embedding Data Loading Issues

**Problem**: Embedding data fails to load or corpus/mappings not found

**Solutions**:

1. **Check file paths**:
   ```python
   from pathlib import Path
   
   # Verify files exist
   corpus_path = Path("data/Core_bugs.csv")
   mappings_path = Path("data/Core_bugs-combined.csv")
   
   print(f"Corpus exists: {corpus_path.exists()}")
   print(f"Mappings exists: {mappings_path.exists()}")
   ```

2. **Verify CSV format**:
   ```python
   import pandas as pd
   
   # Check corpus columns
   corpus = pd.read_csv("data/Core_bugs.csv")
   print(f"Corpus columns: {corpus.columns.tolist()}")
   # Should have: bugid, Description, etc.
   
   # Check mappings columns
   mappings = pd.read_csv("data/Core_bugs-combined.csv")
   print(f"Mappings columns: {mappings.columns.tolist()}")
   # Should have: bugid, Duplicates
   ```

3. **Required parameters**:
   ```python
   from src.scripts.load_embedding_data import load_embedding_data_to_elasticsearch
   
   load_embedding_data_to_elasticsearch(
       file_path="data/Core_bugs.csv",
       workload_id="mozilla_bugs",  # Required
       client_id="mozilla",  # Required
       dataset_type="bug_pairs_separated",
       mappings_file_path="data/Core_bugs-combined.csv"  # Required for bug_pairs_separated
   )
   ```

**Error**: `ValueError: mappings_file_path is required for bug_pairs_separated dataset type`

**Solution**: Always provide `mappings_file_path` parameter when using `bug_pairs_separated` dataset type.

---

### DPO Data Loading Issues

**Problem**: DPO data loading fails with format errors

**Solutions**:

1. **Verify DPO format requirements**:
   - **Required fields**: `prompt`, `chosen_response`, `rejected_response`
   - **workload_id**: Required - must be either in the data or passed as function parameter
   - **client_id**: Optional - can be in the data or passed as function parameter
   - If both data and parameter are provided, the parameter value takes precedence

2. **Handle different prompt formats**:
   ```python
   # String format (recommended for single-turn)
   {"prompt": "What is Python?", ...}
   
   # Message format (for multi-turn conversations)
   {
       "prompt": [
           {"role": "user", "content": "Hello"},
           {"role": "assistant", "content": "Hi!"},
           {"role": "user", "content": "What is Python?"}
       ],
       ...
   }
   ```

3. **Load DPO data**:
   
   **Option 1: Pass workload_id as parameter (data doesn't need workload_id/client_id)**:
   ```python
   from src.scripts.load_dpo_data import load_dpo_data_to_elasticsearch
   
   load_dpo_data_to_elasticsearch(
       file_path="data/dpo-data.jsonl",
       workload_id="dpo-task",
       client_id="client-1"  # Optional
   )
   ```
   
   **Option 2: Include workload_id in data (no parameters needed)**:
   ```python
   from src.scripts.load_dpo_data import load_dpo_data_to_elasticsearch
   
   # If your data already has workload_id and client_id fields
   load_dpo_data_to_elasticsearch(
       file_path="data/dpo-data-with-ids.jsonl"
   )
   ```
   
   **Option 3: Override data values with parameters**:
   ```python
   from src.scripts.load_dpo_data import load_dpo_data_to_elasticsearch
   
   # Parameters override values in the data
   load_dpo_data_to_elasticsearch(
       file_path="data/dpo-data.jsonl",
       workload_id="override-workload",  # This overrides any workload_id in data
       client_id="override-client"       # This overrides any client_id in data
   )
   ```

---

## Job Creation and Execution Issues

### Job Creation Failed: No Data Found

**Problem**: `POST /api/jobs` returns "No data found for workload_id"

**Solutions**:

1. **Verify data is loaded**:
   ```bash
   # Check document count for your workload
   curl -X POST "http://localhost:9200/flywheel/_search?pretty" \
     -H "Content-Type: application/json" \
     -d '{
       "query": {
         "bool": {
           "must": [
             {"term": {"workload_id": "your-workload"}},
             {"term": {"client_id": "your-client"}}
           ]
         }
       }
     }'
   ```

2. **Check workload_id and client_id match**:
   - Use exact same values as when loading data
   - Values are case-sensitive

3. **Minimum data requirements**:
   - LLM jobs: At least 50 records recommended
   - DPO jobs: At least 50 preference pairs
   - Embedding jobs: Depends on dataset size

---

### Job Stuck in Pending Status

**Problem**: Job created but stays in "pending" status indefinitely

**Solutions**:

1. **✅ Check Celery workers are running and review logs**:
   
   **Docker Compose:**
   ```bash
   # Check worker containers are running
   docker ps | grep celery
   
   # Check Celery worker logs for errors
   docker logs deploy-celery_worker-1 --tail 100
   docker logs deploy-celery_parent_worker-1 --tail 100
   
   # Follow logs in real-time to see task processing
   docker logs -f deploy-celery_worker-1
   ```
   
   **Helm (Kubernetes):**
   ```bash
   # Check worker pods are running
   kubectl get pods -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel
   kubectl get pods -l app=df-celery-parent-worker-deployment -n nv-nvidia-blueprint-data-flywheel
   
   # Check Celery worker logs for errors
   kubectl logs -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   kubectl logs -l app=df-celery-parent-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   
   # Follow logs in real-time
   kubectl logs -f -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel
   ```
   
   Look for:
   - Redis connection errors
   - Task serialization errors
   - Worker startup failures
   - Task processing errors

2. **Check for previous stuck jobs**:
   ```bash
   # List all jobs
   curl http://localhost:8000/api/jobs | jq '.[] | {id, status}'
   
   # Cancel stuck jobs (replace with actual job ID)
   curl -X POST "http://localhost:8000/api/jobs/${job_id}/cancel"
   ```

3. **Task serialization safeguard**:
   - Only **one Flywheel job can run at a time** (by design)
   - If another job is running, new jobs will wait in queue
   - Check if previous job is still running

4. **Restart workers**:
   ```bash
   docker restart deploy-celery_worker-1
   docker restart deploy-celery_parent_worker-1
   ```

---

### Job Failed: Insufficient GPU Resources

**Problem**: Job fails with GPU allocation errors

**Solutions**:

1. **Check GPU availability**:
   ```bash
   # Check NVIDIA GPUs
   nvidia-smi
   
   # In Kubernetes
   kubectl describe nodes | grep -A 5 "nvidia.com/gpu"
   ```

2. **Review system requirements**:
   - **Remote LLM Judge**: Minimum 2× (H100, A100, or B200)
   - **Local LLM Judge**: Minimum 6× GPUs
   - See [System Requirements](02-configuration.md#system-requirements)

3. **Use remote LLM judge** to reduce GPU requirements:
   ```yaml
   # config/config.yaml
   llm_judge_config:
     deployment_type: "remote"
     url: "https://integrate.api.nvidia.com/v1/chat/completions"
     model_name: "meta/llama-3.3-70b-instruct"
   ```

4. **Free up GPU resources**:
   ```bash
   # Kill stuck processes
   nvidia-smi --query-compute-apps=pid --format=csv,noheader | xargs -n1 kill -9
   ```

---

### Job Cancelled: Deadline Exceeded

**Problem**: Customization job fails with error message even though training appears to complete successfully

**Error Message**:
```json
{
  "message": "Job cancelled: deadline exceeded.\nCould not acquire resources.\n\nTo adjust the timeout, set:\ncustomizer.env.MAX_PENDING_JOB_MINUTES=<int>\n",
  "detail": "\"Pod Scheduling failed\""
}
```

**Root Cause**: 

**CustomizationJob Structure**: Each CustomizationJob consists of three sequential steps:

1. **EntityHandler_0**: Downloads the base model and training data
2. **TrainingJob**: Performs the actual training
3. **EntityHandler_1**: Uploads the trained output model

**Normal Step Lifecycle**: Each step transitions through states: `created → pending → running → completed`

**The Timeout Mechanism**: 

While the job runs, the customizer's job controller continuously polls each step's status in a loop. The controller calculates elapsed time from the **initial job creation** (when EntityHandler_0 started), not per-step. If the controller's polling loop catches **any step in the `pending` state** AND the total elapsed time exceeds `MAX_PENDING_JOB_MINUTES` (default: 30 minutes), it immediately cancels the entire job.

**Why This Causes Issues**:

The problem is **timing-dependent**. Steps can transition between states very quickly (milliseconds):
- `EntityHandler_0_Created`: 2025-12-19T12:00:00
- `TrainingJob_Running`: 2025-12-19T12:02:00
- `TrainingJob_Completed`: 2025-12-19T12:32:00 (30+ minutes elapsed)
- `EntityHandler_1_Created`: 2025-12-19T12:32:44
- `EntityHandler_1_Pending`: 2025-12-19T12:32:44 ← **If controller catches it here, job is cancelled**
- `EntityHandler_1_Running`: 2025-12-19T12:32:44 ← **Would succeed if controller missed the pending state**

**The "Unlucky" Scenario**: If the controller's polling loop happens to check status while EntityHandler_1 is briefly in `pending` state (even for milliseconds), and 30+ minutes have elapsed since EntityHandler_0 started, the job gets cancelled—even though:
- Training completed successfully
- EntityHandler_1 would transition to `running` immediately
- The entire job would succeed moments later

**The "Lucky" Scenario**: If EntityHandler_1 transitions `pending → running` so quickly that the controller's loop misses the `pending` state, the job succeeds.

This means EntityHandler_1 is penalized for all the execution time of EntityHandler_0 and TrainingJob, making job success dependent on polling timing rather than actual resource availability.

**Solutions**:

1. **Use the recommended nmp-demo-values.yaml configuration** (Strongly Recommended):

   When installing or upgrading NMP, use the provided configuration file that sets `MAX_PENDING_JOB_MINUTES=180` (3 hours):

   ```bash
   # During NMP installation
   helm install nemo <chart> -f deploy/nmp-demo-values.yaml
   
   # Or upgrade existing NMP installation
   helm upgrade nemo <chart> -f deploy/nmp-demo-values.yaml
   ```

   This prevents timeout issues during:
   - Container image pulls
   - Multi-stage pipeline execution
   - GPU resource allocation delays
   - Model initialization

2. **Manually configure MAX_PENDING_JOB_MINUTES**:

   If you're not using `nmp-demo-values.yaml`, add this to your NMP Helm values:

   ```yaml
   customizer:
     env:
       MAX_PENDING_JOB_MINUTES: "180"  # 3 hours - adjust based on dataset size
   ```

   **Sizing Guidelines:**
   - Consider your total pipeline time: data download + training + upload
   - Add buffer time for container pulls and pod initialization
   - Monitor your job execution times and adjust accordingly

3. **Verify the timeout is applied**:

   ```bash
   # Check customizer pod environment variables
   kubectl get pod <customizer-pod-name> -n <namespace> -o jsonpath='{.spec.containers[0].env[?(@.name=="MAX_PENDING_JOB_MINUTES")].value}'
   ```

4. **Monitor job progress**:

   ```bash
   # Check pod status during job execution (look for EntityHandler_1)
   kubectl get pods -n <namespace> | grep -E "(entity-handler|training)"
   
   # Check if EntityHandler_1 is in ContainerCreating state
   kubectl describe pod <entity-handler-1-pod-name> -n <namespace>
   
   # Check customizer logs for timeout warnings
   kubectl logs -n <namespace> -l app.kubernetes.io/name=nemo-customizer --tail=50 | grep -i "pending\|timeout"
   ```

**Common Observation**: You'll often see that training completed successfully but EntityHandler_1 pod never got a chance to run before being cancelled.

**Related Configuration Files**:
- `deploy/nmp-demo-values.yaml` - Recommended NMP configuration with proper timeouts
- `deploy/helm/data-flywheel/values.yaml` - Data Flywheel configuration (lines 685-690)

**Note**: This is a known issue in Customizer V1. The workaround of extending `MAX_PENDING_JOB_MINUTES` prevents the premature cancellation until Customizer V2 introduces proper per-stage timeout handling.

---

### Job Failed: Model Deployment Timeout

**Problem**: Job fails because model deployment times out

**Solutions**:

1. **Check NMP status**:
   ```bash
   # Check NMP pods (NMP namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default
   
   # Check NIM deployment status (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get nimservices -n default
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel` for both commands

2. **Increase timeout** in configuration (if available)

3. **Check NMP connectivity**:
   ```bash
   # Test NMP endpoints
   curl http://nemo.test/v1/models
   curl http://nim.test/v1/models
   ```

4. **Verify model is enabled** in `deploy/nmp-demo-values.yaml`

5. **Wait for initialization**:
   - First deployment of a model takes longer (5-10 minutes)
   - Subsequent deployments are faster

---

## Model Deployment Issues

### NIM Deployment Failed

**Problem**: NIM fails to deploy or becomes unavailable

**Solutions**:

1. **Check DMS (Deployment Management Service)**:
   ```bash
   # Check DMS logs (Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl logs -n default -l app.kubernetes.io/name=nemo-deployment-management
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`

2. **Check model deployment status**:
   
   **Using Python SDK**:
   ```python
   from nemo_microservices import NeMoMicroservices
   import os
   
   client = NeMoMicroservices(
       base_url="http://nemo.test",
       inference_base_url="http://nim.test"
   )
   
   # Get deployment details
   response = client.deployment.model_deployments.retrieve(
       "your-deployment-name",
       namespace="your-namespace"
   )
   print(f"Status: {response.status_details.status}")
   print(f"Deployed: {response.deployed}")
   ```
   
   **Using cURL**:
   ```bash
   # Get deployment details
   curl -X GET \
     "http://nemo.test/v1/deployment/model-deployments/<namespace>/<deployment-name>" \
     -H 'accept: application/json' | jq
   ```

3. **Common causes**:
   - Insufficient GPU resources
   - Container image pull errors
   - Network connectivity issues
   - NIM service configuration issues

4. **Check NIM pod details**:
   ```bash
   # Find NIM pods (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default | grep nim
   
   # Describe the NIM pod for detailed status and events (namespace)
   kubectl describe pod <nim-pod-name> -n default
   ```
   
   > **Note**: For Helm deployment, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`
   
   Look for issues in the `Events` section such as:
   - Image pull failures
   - Resource allocation problems
   - Volume mount issues
   - Configuration errors

5. **Retry deployment**:
   - Cancel failed job
   - Free up resources
   - Create new job

---

### Model Not Responding

**Problem**: Model deployed but not responding to requests

**Solutions**:

1. **Check model health**:
   ```bash
   # Test model endpoint
   curl http://nim.test/v1/models
   
   # Test inference
   curl -X POST "http://nim.test/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{"model": "meta/llama-3.2-1b-instruct", "messages": [{"role": "user", "content": "test"}]}'
   ```

2. **Check model logs**:
   ```bash
   # Find model pod (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default | grep nim
   
   # Check logs (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl logs <model-pod-name> -n default
   ```
   
   > **Note**: For Helm deployment, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`

3. **Verify model URL** in job configuration

4. **Restart model deployment**:
   ```bash
   # Delete deployment (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl delete nimservice <model-name> -n default
   
   # Will be redeployed automatically by DFW
   ```
   
   > **Note**: For Helm deployment, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`

---

### Customization Job Failed

**Problem**: Fine-tuning job fails during customization

**Solutions**:

1. **✅ Check customization logs**:
   
   **Docker Compose:**
   ```bash
   # Worker logs show customization progress
   docker logs -f deploy-celery_worker-1 | grep customization
   
   # Check all worker logs
   docker logs deploy-celery_worker-1 --tail 200
   docker logs deploy-celery_parent_worker-1 --tail 200
   ```
   
   **Helm (Kubernetes):**
   ```bash
   # Check worker logs for customization details
   kubectl logs -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=200 | grep customization
   
   # Check all worker logs
   kubectl logs -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=200
   kubectl logs -l app=df-celery-parent-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=200
   ```

2. **Common causes**:
   - Insufficient training data (need at least 50 records)
   - Invalid LoRA configuration
   - GPU memory issues
   - Training timeout

3. **Adjust training parameters**:
   ```yaml
   # config/config.yaml - reduce resource usage
   training_config:
     epochs: 1  # Reduce from 2
     batch_size: 8  # Reduce from 16
     learning_rate: 0.0001
   ```

4. **Check NeMo Customizer status**:
   ```bash
   # Check customizer pods (Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default -l app.kubernetes.io/name=nemo-customizer
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`

---

## Evaluation Issues

### LLM Judge Not Available

**Problem**: Evaluation fails because LLM judge is not ready

**Solutions**:

1. **Wait for LLM judge to be ready**:
   - For remote judge: Check API connectivity
   - For local judge: Wait for deployment (can take 5-10 minutes)

2. **Verify remote judge configuration**:
   ```yaml
   # config/config.yaml
   llm_judge_config:
     deployment_type: "remote"
     url: "https://integrate.api.nvidia.com/v1/chat/completions"
     model_name: "meta/llama-3.3-70b-instruct"
   ```

3. **Test remote judge**:
   ```bash
   # Test NVIDIA API connectivity
   curl -X POST "https://integrate.api.nvidia.com/v1/chat/completions" \
     -H "Authorization: Bearer $NVIDIA_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "meta/llama-3.3-70b-instruct",
       "messages": [{"role": "user", "content": "test"}],
       "max_tokens": 10
     }'
   ```

4. **Check API key is set**:
   ```bash
   echo $NVIDIA_API_KEY
   echo $LLM_JUDGE_API_KEY
   ```

---

### Evaluation Timeout

**Problem**: Evaluation jobs time out before completion

**Solutions**:

1. **Reduce evaluation dataset size**:
   ```python
   # When creating job
   data_split_config = {
       "eval_size": 20,  # Reduce from default
       "val_ratio": 0.1
   }
   ```

2. **Check evaluator service**:
   ```bash
   # Check evaluator pods (Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get pods -n default -l app.kubernetes.io/name=nemo-evaluator
   kubectl logs -n default -l app.kubernetes.io/name=nemo-evaluator
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel`

3. **Monitor evaluation progress**:
   ```bash
   # Check worker logs
   docker logs -f deploy-celery_worker-1 | grep evaluation
   ```

4. **Increase timeout** (if configurable in your version)

---

### Remote API Rate Limiting

**Problem**: Remote API calls fail due to rate limiting

**Solutions**:

1. **Check rate limit headers**:
   - Most APIs return rate limit info in response headers
   - Implement exponential backoff

2. **Reduce concurrent requests**:
   ```yaml
   # Reduce eval_size to make fewer API calls
   data_split_config:
     eval_size: 10
   ```

3. **Use local judge** if available:
   ```yaml
   llm_judge_config:
     deployment_type: "local"
     model_name: "meta/llama-3.3-70b-instruct"
   ```

4. **Wait and retry**:
   - Rate limits usually reset after a time period
   - Check API provider documentation for limits

---

## Notebook-Specific Issues

### Brev Launchable Deployment Issues

**Problem**: Deployment fails in Brev environment

**Solutions**:

1. **Verify GPU availability**:
   ```bash
   nvidia-smi
   # Should show 2 GPUs for remote judge setup
   ```

2. **Check NGC login in notebook**:
   ```bash
   # Re-run NGC login cell
   echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
   ```

3. **Ensure data-flywheel repo is cloned**:
   ```bash
   ls -la ~/data-flywheel
   # If not found, re-run git clone cell
   ```

4. **Check directory structure**:
   ```bash
   cd ~/data-flywheel
   ls -la config/config.yaml
   ls -la scripts/deploy-nmp.sh
   ```

5. **Wait for services**:
   - After running deployment cell, wait 10+ minutes
   - Check logs: `tail -f ~/data-flywheel/flywheel_deploy.log`

---

### Kernel Crashes or Out of Memory

**Problem**: Jupyter kernel dies or notebook runs out of memory

**Solutions**:

1. **Restart kernel**:
   - Click `Kernel` → `Restart Kernel`
   - Re-run necessary setup cells

2. **Reduce data size**:
   ```python
   # Load only subset of data
   df = pd.read_csv("data/large-file.csv", nrows=1000)
   ```

3. **Clear variables**:
   ```python
   # Free memory
   del large_dataframe
   import gc
   gc.collect()
   ```

4. **Monitor memory**:
   ```python
   import psutil
   print(f"Memory usage: {psutil.virtual_memory().percent}%")
   ```

---

### Git LFS Pull Failed

**Problem**: `git lfs pull` fails or dataset files are missing

**Solutions**:

1. **Install and initialize Git LFS**:
   ```bash
   sudo apt-get update && sudo apt-get install -y git-lfs
   git lfs install
   ```

2. **Pull LFS files**:
   ```bash
   cd ~/data-flywheel
   git lfs pull
   ```

3. **Check LFS files**:
   ```bash
   # List LFS tracked files
   git lfs ls-files
   ```

4. **Manual download**:
   - If LFS continues to fail, download datasets manually from links in notebook
   - Place in `data/` directory

---

## Configuration Issues

### Invalid Configuration File

**Problem**: Service fails to start due to config.yaml errors

**Solutions**:

1. **Validate YAML syntax**:
   ```bash
   # Check for syntax errors
   python -c "import yaml; yaml.safe_load(open('config/config.yaml'))"
   ```

2. **Check required sections exist**:
   ```yaml
   # Must have these sections:
   data_split_config:
     ...
   llm_judge_config:
     ...
   llm_job_config:  # or embedding_job_config, dpo_job_config
     nims:
       - model_name: "..."
   ```

3. **Use example config as reference**:
   ```bash
   # View default config structure
   cat config/config.yaml
   ```

4. **Reset to default**:
   ```bash
   git checkout config/config.yaml
   ```

---

### Model Not Enabled for Customization

**Problem**: Job fails because model doesn't support customization

**Solutions**:

1. **Check NMP values file**:
   ```yaml
   # deploy/nmp-demo-values.yaml
   customizer:
     customizationTargets:
       overrideExistingTargets: true
       targets:
         meta/llama-3.2-1b-instruct@2.0:
           enabled: true  # Must be enabled
         meta/llama-3.2-3b-instruct@2.0:
           enabled: true
         meta/llama-3.1-8b-instruct@2.0:
           enabled: true
         nvidia/llama-3.2-nv-embedqa-1b@v2:
           enabled: true
   ```

2. **Update Helm deployment**:
   ```bash
   helm upgrade nemo <chart> -f deploy/nmp-demo-values.yaml
   ```

3. **Verify in config.yaml**:
   ```yaml
   llm_job_config:
     nims:
       - model_name: "meta/llama-3.2-1b-instruct"
         customization_enabled: true
   ```

4. **Supported models**:
   - meta/llama-3.2-1b-instruct
   - meta/llama-3.2-3b-instruct
   - meta/llama-3.1-8b-instruct
   - nvidia/llama-3.2-nv-embedqa-1b-v2

---

### MLflow Not Starting

**Problem**: MLflow service not available

**Solutions**:

1. **Enable MLflow profile**:
   ```bash
   export COMPOSE_PROFILES=mlflow
   docker compose -f ./deploy/docker-compose.yaml build api ingestion-plugins-api && \
   docker compose -f ./deploy/docker-compose.yaml up -d
   ```

2. **Check MLflow container**:
   ```bash
   docker ps | grep mlflow
   ```

3. **Access MLflow UI**:
   ```bash
   # Should be available at
   curl http://localhost:5000
   ```

4. **Check MLflow logs**:
   ```bash
   docker logs deploy-mlflow-1
   ```

5. **Kubernetes MLflow**:
   ```bash
   kubectl port-forward service/df-mlflow-service 5000:5000
   ```

---

## Performance and Resource Issues

### Jobs Taking Too Long

**Problem**: Jobs take several hours to complete

**Expected behavior**:
- Embedding jobs: 2-4 hours depending on dataset size
- LLM jobs: 1-3 hours depending on data and number of models
- DPO jobs: Similar to LLM jobs

**Optimization tips**:

1. **Reduce evaluation data**:
   ```python
   data_split_config = {
       "eval_size": 20,  # Smaller = faster
       "val_ratio": 0.1
   }
   ```

2. **Reduce number of models**:
   ```yaml
   # Test with fewer NIMs first
   llm_job_config:
     nims:
       - model_name: "meta/llama-3.2-1b-instruct"  # Only one model
   ```

3. **Use remote judge**:
   - Remote judge is faster than local deployment
   - Reduces GPU resource contention

4. **Monitor progress**:
   ```python
   from notebooks.utils.job_monitor_helper import monitor_job
   
   monitor_job(
       api_base_url="http://localhost:8000",
       job_id="your-job-id",
       poll_interval=30
   )
   ```

---

### High Memory Usage

**Problem**: System running out of memory

**Solutions**:

1. **Check memory usage**:
   ```bash
   free -h
   docker stats
   ```

2. **Reduce Elasticsearch memory**:
   ```yaml
   # docker-compose.yaml
   elasticsearch:
     environment:
       - "ES_JAVA_OPTS=-Xms512m -Xmx512m"  # Reduce from default
   ```

3. **Reduce Celery concurrency**:
   ```yaml
   # docker-compose.yaml
   celery_worker:
     command: [..., "--concurrency=25"]  # Reduce from 50
   ```

4. **Cleanup old data**:
   ```bash
   # Clear old Elasticsearch indices
   curl -X DELETE "http://localhost:9200/old-index-*"
   
   # Clear MongoDB old jobs
   # (Be careful - only delete completed jobs you don't need)
   ```

---

### Disk Space Running Low

**Problem**: Running out of disk space

**Solutions**:

1. **Check disk usage**:
   ```bash
   df -h
   docker system df
   ```

2. **Clean up Docker**:
   ```bash
   # Remove everything not currently in use
   docker system prune -a --volumes -f
   ```

3. **Clean up old logs**:
   ```bash
   # Find large log files
   find . -name "*.log" -size +100M
   
   # Remove old logs
   rm -f *.log
   ```

4. **Clear model artifacts**:
   ```bash
   # Remove old LoRA adapters if not needed
   # (Check MLflow artifact storage location)
   ```

5. **Increase disk space**:
   - For production: Provision more storage
   - Minimum recommendation: 200 GB

---

### Using Alternative Storage When Root Disk is Full

**Problem**: Model downloads or deployments fail due to insufficient disk space on the root filesystem.

**When to use this guide**: This guide is for systems where the root filesystem (`/`) has limited capacity but additional storage is mounted elsewhere (e.g., `/ephemeral`, `/mnt/storage`, etc.).

**Check your disk space**:

```bash
df -h
```

**Example output showing insufficient root disk**:

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda1        97G   97G     0 100% /
/dev/vdb        6.3T  166M  6.0T   1% /ephemeral
```

In this example:
- Root filesystem (`/`) is at **100% capacity** (insufficient for this project)
- Alternative mount (`/ephemeral`) has **6.0T available** (sufficient for this project)

> **💡 Minimum Requirement**: The Data Flywheel Blueprint requires at least **200 GB of free disk space** for Docker images, models, logs, and data storage. If your root filesystem doesn't have enough space, follow the steps below to use an alternative storage location.

**Solution**: Configure Docker and Minikube to use the alternative storage mount.

1. **Set your storage path**:

   ```bash
   # Replace with your actual available storage path (e.g., /mnt/storage, /data, etc.)
   export STORAGE_PATH="/ephemeral"
   ```

2. **Stop services**:

   ```bash
   minikube stop
   sudo systemctl stop docker
   ```

3. **(Optional) Move existing data**:

   If you need to preserve existing images/clusters, copy the data to the new location:
   
   ```bash
   sudo rsync -aP /var/lib/docker/ $STORAGE_PATH/docker/
   rsync -aP ~/.minikube/ $STORAGE_PATH/minikube/
   ```

4. **Configure Docker Storage**:
   
   Edit `/etc/docker/daemon.json` (create if it doesn't exist) to add the `data-root` configuration:
   
   ```json
   {
     "data-root": "/ephemeral/docker"
   }
   ```
   
   > **Note**: Replace `/ephemeral` with your actual `$STORAGE_PATH` value. Ensure the file contains valid JSON if other keys exist.

5. **Configure Minikube Storage**:

   Create and use a dedicated directory for Minikube state:
   
   ```bash
   sudo mkdir -p $STORAGE_PATH/minikube
   sudo chown $USER:$USER $STORAGE_PATH/minikube
   export MINIKUBE_HOME=$STORAGE_PATH/minikube
   ```

6. **Create Docker data-root and set ownership**:

   ```bash
   sudo mkdir -p $STORAGE_PATH/docker
   sudo chown root:root $STORAGE_PATH/docker
   ```

7. **Start Docker daemon**:

   ```bash
   sudo systemctl start docker
   ```

8. **Verify changes**:

   ```bash
   docker info | grep "Docker Root Dir"
   env | grep MINIKUBE_HOME
   ```
   
   Expected output should show your new storage path:
   ```text
   Docker Root Dir: /ephemeral/docker
   MINIKUBE_HOME=/ephemeral/minikube
   ```

---

## Kubernetes/Helm Deployment Issues

### Pods Not Starting

**Problem**: Kubernetes pods stuck in Pending or CrashLoopBackOff

**Solutions**:

1. **Check pod status** (Helm deployment):
   ```bash
   kubectl get pods -n nv-nvidia-blueprint-data-flywheel
   kubectl describe pod <pod-name> -n nv-nvidia-blueprint-data-flywheel
   ```

2. **Understanding Pod Readiness**:

   When checking `kubectl get pods`, you'll see output like this:
   ```
   NAME                    READY   STATUS      RESTARTS   AGE
   api-pod-abc123         1/1     Running     0          5m
   worker-pod-def456      0/1     Running     0          2m
   cust-job-xyz789        0/1     Completed   0          1m
   ```

   **Key Indicators**:
   
   - **READY Column**: Shows `current/total` containers ready (e.g., `1/1`, `0/1`)
   - **STATUS Column**: Shows pod lifecycle state (e.g., `Running`, `Completed`, `Pending`)

   **✅ What "Truly Ready" Means**:
   
   For most pods to be **truly ready**, you need **BOTH**:
   - **READY**: `1/1` (all containers ready)
   - **STATUS**: `Running`
   
   ⚠️ **Common Misunderstandings**:
   - A pod can show **STATUS = `Running`** but have **READY = `0/1`** → **NOT ready yet**
   - This happens when the container is running but health/readiness checks haven't passed yet
   - Wait until **READY becomes `1/1`** before considering the pod fully operational
   
   ℹ️ **Health Check Timing**:
   - Even after READY shows `1/1`, health checks may initially fail—this is normal
   - Health/readiness probes poll periodically and may take 30-60 seconds to stabilize
   - If health checks continue to fail after several minutes, investigate the pod logs

   **🔧 Exception: Customizer Model Downloader Pods**:
   
   Pods starting with `customizer-downloader-*` behave differently from regular service pods:
   
   **What are these pods?**
   - **Kubernetes Job pods** (not Deployments) that download base model files from NGC to persistent storage
   - Each enabled customization target in `nmp-demo-values.yaml` creates one downloader job
   - Pre-download models so they're ready for fine-tuning jobs
   - Auto-deleted 2 hours after completion
   
   **Expected behavior**:
   - **STATUS**: Should reach `Completed` when download finishes
   - **READY**: Will stay at `0/1` (this is normal for Job pods!)
   - For customizer-downloader pods, **only check the STATUS column** (should be `Completed`)
   - If STATUS is `Error` or `Failed`, check:
     - NGC authentication (NGC_API_KEY secret)
     - Network connectivity to NGC
     - PVC mount issues
     - Pod logs: `kubectl logs <pod-name>`
   
   **Mapping to nmp-demo-values.yaml**:
   
   Each enabled target creates one downloader pod:
   
   | Pod Name Pattern | Customization Target | Model Source |
   |------------------|---------------------|--------------|
   | `customizer-downloader-meta-llama-3-2-1b-instruct-*` | `meta/llama-3.2-1b-instruct@2.0` | `ngc://nvidia/nemo/llama-3_2-1b:2.0` |
   | `customizer-downloader-meta-llama-3-2-3b-instruct-*` | `meta/llama-3.2-3b-instruct@2.0` | `ngc://nvidia/nemo/llama-3_2-3b:2.0` |
   | `customizer-downloader-meta-llama-3-1-8b-instruct-*` | `meta/llama-3.1-8b-instruct@2.0` | `ngc://nvidia/nemo/llama-3_1-8b:2.0` |
   | `customizer-downloader-nvidia-llama-3-2-nv-embedqa-*` | `nvidia/llama-3.2-nv-embedqa-1b@v2` | `ngc://nvidia/nemo/llama-3_2-nv-embedqa-1b:v2` |
   
   **Note**: If you enable/disable targets in `nmp-demo-values.yaml` and redeploy, you'll see corresponding downloader pods created/removed
   
   **Quick Reference Table**:
   
   | Pod Type | Expected READY | Expected STATUS | Pod Kind | Notes |
   |----------|---------------|-----------------|----------|-------|
   | Service pods<br>(api, worker, nemo-*, etc.) | `1/1` | `Running` | Deployment | Both READY and STATUS must be satisfied |
   | Customizer downloader pods<br>(`customizer-downloader-*`) | `0/1` | `Completed` | Job | Only STATUS matters; one-time model download jobs |
   | Starting/initializing pods | `0/1` | `Running` | Deployment | Temporary; wait for READY to become `1/1` |
   | Failed pods | `0/1` | `Error` or `CrashLoopBackOff` | Any | Investigate logs immediately |

3. **Common issues**:
   - **ImagePullBackOff**: NGC authentication issue
   - **Pending**: Insufficient resources
   - **CrashLoopBackOff**: Configuration or connectivity issue

4. **Check events**:
   ```bash
   kubectl get events -n nv-nvidia-blueprint-data-flywheel --sort-by='.lastTimestamp'
   ```

5. **Check logs**:
   ```bash
   kubectl logs <pod-name> -n nv-nvidia-blueprint-data-flywheel
   kubectl logs <pod-name> -n nv-nvidia-blueprint-data-flywheel --previous
   ```

---

### Port Forwarding Not Working

**Problem**: `kubectl port-forward` fails or disconnects

**Solutions**:

1. **Use automated script**:
   ```bash
   ./scripts/helm/forward-ports.sh df-api-service 8000 df-mlflow-service 5000
   ```

2. **Manual port forward with retry**:
   ```bash
   while true; do
     kubectl port-forward service/df-api-service 8000:8000 -n nv-nvidia-blueprint-data-flywheel
     sleep 2
   done
   ```

3. **Check service exists**:
   ```bash
   kubectl get svc -n nv-nvidia-blueprint-data-flywheel
   ```

4. **Use NodePort or LoadBalancer** for more stable access (production)

---

### PVC Mount Issues

**Problem**: Persistent Volume Claims not mounting

**Solutions**:

1. **Check PVC status**:
   ```bash
   kubectl get pvc -n nv-nvidia-blueprint-data-flywheel
   ```

2. **Check storage class**:
   ```bash
   kubectl get storageclass
   ```

3. **Delete and recreate PVC**:
   ```bash
   # WARNING: This deletes data!
   ./scripts/helm/clear-all-data.sh
   helm upgrade --install data-flywheel ...
   ```

4. **Check PV provisioner** is installed and working

---

## Best Practices and Tips

### Monitoring Job Progress

**Use built-in monitoring**:

```python
from notebooks.utils.job_monitor_helper import monitor_job

# Monitor with auto-refresh
monitor_job(
    api_base_url="http://localhost:8000",
    job_id=job_id,
    poll_interval=30  # Check every 30 seconds
)
```

**Check job status manually**:

```bash
# Get job details (use your actual job_id)
curl http://localhost:8000/api/jobs/${job_id} | jq .

# List all jobs
curl http://localhost:8000/api/jobs | jq '.[] | {id, status, workload_id}'
```

**Monitor worker logs**:

```bash
# Real-time logs
docker logs -f deploy-celery_worker-1

# Search for specific job (use your actual job_id)
docker logs deploy-celery_worker-1 2>&1 | grep ${job_id}
```

---

### Cleanup and Maintenance

**Regular cleanup routine**:

```bash
# Stop services
./scripts/stop.sh

# Clear volumes (optional - removes all data)
./scripts/clear_all_volumes.sh

# Clean Docker resources
docker system prune -a --volumes -f

# Start fresh
./scripts/run.sh
```

**Kubernetes cleanup**:

```bash
# Clear specific service data
./scripts/helm/clear-mongodb-data.sh
./scripts/helm/clear-es-data.sh

# Or clear everything
./scripts/helm/clear-all-data.sh
```

**Cancel old jobs**:

```python
import requests

api_url = "http://localhost:8000"

# Get all jobs
jobs = requests.get(f"{api_url}/api/jobs").json()

# Cancel pending/stuck jobs
for job in jobs:
    if job['status'] in ['pending', 'running']:
        print(f"Cancelling job {job['id']}")
        requests.post(f"{api_url}/api/jobs/{job['id']}/cancel")
```

---

### Debugging Tips

1. **✅ Check Service Logs First (Most Important)**:
   
   When troubleshooting any issue, always start by checking the logs of the API and Celery worker services:
   
   **Docker Compose Deployment:**
   ```bash
   # Check API logs
   docker logs deploy-api-1 --tail 100
   
   # Check Celery worker logs
   docker logs deploy-celery_worker-1 --tail 100
   docker logs deploy-celery_parent_worker-1 --tail 100
   
   # Follow logs in real-time (useful for active debugging)
   docker logs -f deploy-api-1
   docker logs -f deploy-celery_worker-1
   
   # Check all infrastructure service logs
   docker logs deploy-elasticsearch-1 --tail 100
   docker logs deploy-mongodb-1 --tail 100
   docker logs deploy-redis-1 --tail 100
   ```
   
   **Helm (Kubernetes) Deployment:**
   ```bash
   # Check API logs
   kubectl logs -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   
   # Check Celery worker logs
   kubectl logs -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   kubectl logs -l app=df-celery-parent-worker-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   
   # Follow logs in real-time
   kubectl logs -f -l app=df-api-deployment -n nv-nvidia-blueprint-data-flywheel
   kubectl logs -f -l app=df-celery-worker-deployment -n nv-nvidia-blueprint-data-flywheel
   
   # Check all infrastructure service logs
   kubectl logs -l app=df-elasticsearch-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   kubectl logs -l app=df-mongodb-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   kubectl logs -l app=df-redis-deployment -n nv-nvidia-blueprint-data-flywheel --tail=100
   ```

2. **Enable verbose logging**:
   ```bash
   # Set log level in docker-compose.yaml
   environment:
     - LOG_LEVEL=DEBUG
   ```

3. **Inspect database state**:
   ```bash
   # MongoDB
   docker exec -it deploy-mongodb-1 mongosh
   > use flywheel
   > db.jobs.find().pretty()
   
   # Elasticsearch
   curl "http://localhost:9200/flywheel/_search?pretty&size=1"
   ```

4. **Use MLflow for experiment tracking**:
   - Access MLflow UI at `http://localhost:5000`
   - View training runs, metrics, and artifacts
   - Compare different experiments

5. **Check NMP status** (if using local deployment):
   ```bash
   # Check all NMP resources (NMP namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get all -n default
   
   # Check NIM services (namespace - Docker Compose: default, Helm: nv-nvidia-blueprint-data-flywheel)
   kubectl get nimservices -n default
   ```
   
   > **Note**: For Helm deployments, replace `-n default` with `-n nv-nvidia-blueprint-data-flywheel` for both commands

---

## Getting Help

If you've tried the solutions above and still have issues:

1. **Check the Documentation**:
   - [Installation Guide](get-started/install-and-setup.md)
   - [Configuration Guide](02-configuration.md)
   - [API Reference](06-api-reference.md)
   - [Architecture Overview](01-architecture.md)
   - [NeMo Microservices Troubleshooting (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/troubleshooting/index.html) - Official troubleshooting for Customizer, Evaluator, and other NMP components

2. **Search GitHub Issues**:
   - Check [Data Flywheel Issues](https://github.com/NVIDIA-AI-Blueprints/data-flywheel/issues)
   - Search for similar problems and solutions

3. **Review Logs**:
   - Gather relevant logs from services
   - Include error messages in your issue report

4. **Create a Detailed Issue Report**:
   - Describe what you're trying to do
   - Include steps to reproduce the problem
   - Provide relevant logs and error messages
   - Mention your environment (OS, GPU, deployment method)
   - Include your configuration (sanitized, without API keys)

5. **Community Resources**:
   - [NeMo Microservices Documentation (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/)
   - [NeMo Microservices Troubleshooting Guide (v26.3.0)](https://docs.nvidia.com/nemo/microservices/26.03.0/troubleshooting/index.html)
   - [NVIDIA Developer Forums](https://forums.developer.nvidia.com/)

---

**Remember**: Most issues can be resolved by:
1. ✅ Verifying all services are running
2. ✅ Checking environment variables are set correctly
3. ✅ Ensuring data is loaded properly
4. ✅ Waiting adequate time for initialization (4-5 minutes)
5. ✅ Reading error messages carefully

Good luck with your Data Flywheel deployment! 🚀

