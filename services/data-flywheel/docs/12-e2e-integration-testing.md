# End-to-End Integration Testing

Learn how to run comprehensive end-to-end integration tests for the Data Flywheel Blueprint to validate the complete workflow from data loading to model evaluation and customization.

This guide covers the E2E integration test that focuses on the core Data Flywheel workflow without infrastructure setup, assuming API server and Elasticsearch services are already accessible.

The test can be executed in two ways:

1. **Standalone Script**: Direct Python execution with command-line arguments
2. **Pytest Integration**: Using pytest with markers and fixtures for better integration into test suites

The test includes integrated data loading functionality with bulk indexing for optimal performance, processing thousands of records efficiently.

> **📖 For infrastructure setup:** See [Getting Started](get-started/index.md) for Docker deployment or [Helm Installation](10-helm-installation.md) for Kubernetes deployment

## Test Architecture

The end-to-end integration test (`tests/e2e_scripts/simple_e2e_test.py`) validates the complete Data Flywheel workflow by performing these operations:

1. **Service Health Check** - Verifies API server and Elasticsearch are accessible
2. **Test Data Loading** - Uses bulk indexing to efficiently load 5,000+ sample records into Elasticsearch
3. **Job Creation** - Creates a flywheel job via API
4. **Job Monitoring** - Monitors progress with detailed status logging
5. **Result Verification** - Validates model evaluations and customizations
6. **Cleanup** - Removes test data

## Prerequisites

### Setup Dependencies

The e2e test script supports both minimal standalone usage and full development environments:

**Option 1: Standalone Mode (Minimal Dependencies)**

```bash
# Install only required packages
pip install requests elasticsearch

# Pull dataset files from Git LFS (if using default dataset)
git lfs pull

# Run with automatic dependency checking
python tests/e2e_scripts/simple_e2e_test.py
```

**Option 2: Full Development Environment (Recommended)**

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
uv sync

# Pull dataset files from Git LFS
git lfs pull
```

**Automatic Dependency Validation:**
The script automatically checks for required dependencies at startup and provides helpful installation guidance if packages are missing.

### Required Services Running

The E2E test requires two accessible services:

- ✅ **API Server** - Data Flywheel API endpoint accessible via HTTP/HTTPS
- ✅ **Elasticsearch** - Elasticsearch cluster accessible via HTTP/HTTPS

**Common Service Configurations:**

**Local Docker Deployment:**

- API Server: `http://localhost:8000` (default)
- Elasticsearch: `http://localhost:9200` (default)

**Remote/Production Deployment:**

- API Server: `https://your-api-server.com`
- Elasticsearch: `https://your-elasticsearch.com`

**Kubernetes with Port-Forwarding:**

- API Server: `http://localhost:8000` (port-forward from cluster)
- Elasticsearch: `http://localhost:9200` (port-forward from cluster)

### Service URL Configuration

The script uses default localhost URLs when no custom URLs are provided:

- **Default API URL**: `http://localhost:8000`
- **Default Elasticsearch URL**: `http://localhost:9200`

Override these defaults using `--api-url` and `--elasticsearch-url` arguments for any custom configuration.

> **📖 For detailed configuration options:** See [Configuration Guide](02-configuration.md)

### Optional Environment Variables

```bash
# Only needed if using remote services
export NGC_API_KEY="your-ngc-api-key"
export NVIDIA_API_KEY="your-nvidia-api-key"
```

## Usage

### Method 1: Standalone Script

**Default Configuration (localhost):**
```bash
# Standalone mode (minimal dependencies)
python tests/e2e_scripts/simple_e2e_test.py

# With uv (recommended - handles all dependencies)
uv run python tests/e2e_scripts/simple_e2e_test.py
```

**Custom URLs:**
```bash
# Run with custom API and Elasticsearch URLs
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url https://my-api-server.com \
  --elasticsearch-url https://my-elasticsearch.com
```

**Custom Data File:**
```bash
# Run with a custom dataset file (within project)
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file data/my_custom_dataset.jsonl

# Run with external data file (outside project directory)
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file /path/to/external/dataset.jsonl \
  --allow-external-files

# Security note: --allow-external-files is required for files outside the project
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file ../external_data/dataset.jsonl \
  --allow-external-files
```

### Method 2: Pytest Integration

**Basic pytest execution:**
```bash
# Run the E2E test using pytest (from project root)
uv run pytest -m e2e_integration tests/e2e_scripts/

# Run with verbose output
uv run pytest -m e2e_integration tests/e2e_scripts/ -s -v

# Run all E2E integration tests from anywhere in the project
uv run pytest -m e2e_integration
```

**Pytest with custom parameters:**
```bash
# Custom workload and client parameters
uv run pytest -m e2e_integration tests/e2e_scripts/ \
  --workload-id "my-test" --client-id "my-client"

# Custom URLs
uv run pytest -m e2e_integration tests/e2e_scripts/ \
  --api-url "https://my-api-server.com" --elasticsearch-url "https://my-elasticsearch.com"

# Custom timeout
uv run pytest -m e2e_integration tests/e2e_scripts/ \
  --e2e-timeout 120
```

### Standalone Script Parameters
```bash
# Custom workload and client IDs
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --workload-id "my-test" --client-id "my-client"

# Custom timeout (default: 90 minutes)
uv run python tests/e2e_scripts/simple_e2e_test.py --timeout 120

# Custom API and Elasticsearch URLs
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "https://my-api-server.com" \
  --elasticsearch-url "https://my-elasticsearch.com"

# Production environment example
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --workload-id "production-test" \
  --api-url "https://dataflywheel-api.company.com" \
  --elasticsearch-url "https://dataflywheel-es.company.com"

# Port-forwarded services from Kubernetes
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "http://localhost:8001" \
  --elasticsearch-url "http://localhost:9201"

# Custom data file with other options
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --data-file "data/custom_dataset.jsonl" \
  --workload-id "custom-test" \
  --timeout 120
```

### Command Line Options

**Standalone Script Options:**
- `--workload-id`: Workload identifier for test data (default: "primary_assistant")
- `--client-id`: Client identifier for test data (default: "client_id")  
- `--timeout`: Timeout in minutes for job completion (default: 90)
- `--api-url`: API server URL (default: `http://localhost:8000`)
- `--elasticsearch-url`: Elasticsearch URL (default: `http://localhost:9200`)
- `--data-file`: Data file path to load. Can be relative to project root or absolute path (default: "data/aiva_primary_assistant_dataset.jsonl")
- `--allow-external-files`: Allow data files outside the project directory (security flag)

**Pytest Options:**
- `--workload-id`: Workload identifier for test data (default: "primary_assistant")
- `--client-id`: Client identifier for test data (default: "pytest-e2e-client")
- `--e2e-timeout`: Timeout in minutes for job completion (default: 90)
- `--api-url`: API server URL (default: `http://localhost:8000`)
- `--elasticsearch-url`: Elasticsearch URL (default: `http://localhost:9200`)

## Security and Path Validation

The e2e script includes security features to prevent accidental access to files outside the project:

### Path Validation Behavior
- **Default (Secure)**: Data files must be within the project directory
- **Standalone Mode**: Use `--allow-external-files` to access external datasets
- **Pytest Mode**: Always enforces project-only security (no external files)

### Example Security Messages
```bash
# Accessing file outside project without flag
❌ Path '/tmp/dataset.jsonl' resolves outside the project directory. 
   Only paths within the project are allowed for security.
   Use allow_external=True to override this check.

# Successful external file access
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file /tmp/dataset.jsonl --allow-external-files
```

### Dependency Management
The script automatically validates required dependencies:
```bash
# Missing dependencies example
❌ Missing required dependencies for standalone usage:
   • requests
   • elasticsearch

To install dependencies:
   pip install requests elasticsearch
   # OR if using uv:
   uv sync
```

## What the Test Does

### 1. Service Health Check (30 seconds)

```text
✅ API Server is available
✅ Elasticsearch is available
```

### 2. Data Loading (15-30 seconds)

The test includes integrated bulk data loading functionality:
- **Dataset**: Configurable dataset file (default: AIVA Primary Assistant Dataset with 5,079 records)
- **Data File**: Specified via `--data-file` argument, supports relative paths (to project root) or absolute paths
- **Method**: Direct bulk indexing using Elasticsearch Python client
- **Performance**: Processes records in chunks of 100 for optimal performance
- **Progress**: Real-time progress logging every 100 documents
- **Connection**: Uses custom Elasticsearch URL or defaults to `localhost:9200`

**Example output:**

```text
[INFO] Loading test data to Elasticsearch from: /path/to/project/data/aiva_primary_assistant_dataset.jsonl
[INFO] Document is already in the log format. Loading 5079 records with overrides...
[INFO] ✅ Successfully indexed 100 documents
[INFO] Processing document 100/5079...
[INFO] ✅ Successfully indexed 100 documents
...
[INFO] ✅ Test data loaded successfully from /path/to/project/data/aiva_primary_assistant_dataset.jsonl
```

### 3. Job Creation (< 30 seconds)

```text
✅ Job created successfully: 64f8a1b2c3d4e5f6g7h8i9j0
```

### 4. Job Monitoring (45-90 minutes)

Real-time progress logging:

```text
📊 Job Status: running | Records: 300 | Elapsed: 0:15:23
  🤖 NIM 1: meta/llama-3.2-1b-instruct
     Status: running | Deployment: ready
     📈 Evaluations (2):
       ✅ BASE: 100.0%
       🔄 ICL: 45.2%
     🎯 Customizations (1):
       🔄 Fine-tuning: 23.1% (epochs: 0, steps: 156)
  ⚖️  LLM Judge: ready
```

### 5. Result Verification (1-2 minutes)

Comprehensive validation:

- ✅ Job completion status
- ✅ Model deployment verification
- ✅ Evaluation completion (base, ICL, customized)
- ✅ Customization success (epochs, steps, progress)
- ✅ Score generation and runtime metrics
- ✅ LLM judge deployment

### 6. Test Summary

```text
📋 SUMMARY:
  Job ID: 64f8a1b2c3d4e5f6g7h8i9j0
  Status: completed
  Records Processed: 300
  NIMs Tested: 1
  Total Evaluations: 3
  Total Customizations: 1
  Total Runtime: 1:23:45
```

## Expected Output

### Successful Test Run

```bash
[10:15:30] [INFO] 🚀 Starting Simplified E2E Integration Test
[10:15:30] [INFO]    Workload ID: e2e-test
[10:15:30] [INFO]    Client ID: test-client
[10:15:30] [INFO]    Timeout: 90 minutes
[10:15:30] [INFO] Checking service availability...
[10:15:30] [INFO] ✅ API Server is available
[10:15:31] [INFO] ✅ Elasticsearch is available
[10:15:31] [INFO] Loading test data...
[10:15:33] [INFO] ✅ Test data loaded successfully
[10:15:33] [INFO] Creating flywheel job...
[10:15:34] [INFO] ✅ Job created successfully: 64f8a1b2c3d4e5f6g7h8i9j0
[10:15:34] [INFO] 🔍 Monitoring job 64f8a1b2c3d4e5f6g7h8i9j0 (timeout: 90 minutes)...
[10:15:34] [INFO] 🔄 Status changed: None → running
[10:15:34] [INFO] 📊 Job Status: running | Records: 300 | Elapsed: 0:00:01
...
[11:39:19] [INFO] 🎉 Job completed successfully!
[11:39:19] [INFO] 🔍 Verifying job results...
[11:39:19] [INFO]   🤖 Verifying NIM 1: meta/llama-3.2-1b-instruct
[11:39:19] [INFO]     ✅ Evaluation base: 100.0% complete, 342s runtime
[11:39:19] [INFO]     ✅ Evaluation icl: 100.0% complete, 298s runtime
[11:39:19] [INFO]     ✅ Evaluation customized: 100.0% complete, 267s runtime
[11:39:19] [INFO]     🎯 Found 1 customization(s)
[11:39:19] [INFO]     ✅ Customization: 100.0% complete, 2 epochs, 1247 steps
[11:39:19] [INFO]   ✅ LLM Judge: completed
[11:39:19] [INFO] 🎉 All verification checks passed!
[11:39:19] [INFO] 🎉 Integration Test PASSED! 🎉
[11:39:20] [INFO] ✅ Cleaned up 300 test records
```

## Verification Checks

The test performs comprehensive validation:

### Job-Level Checks

- ✅ Job status is "completed"
- ✅ Records were processed (> 0)
- ✅ Job has start and finish times
- ✅ No errors reported

### NIM-Level Checks

- ✅ NIM deployment completed
- ✅ NIM status is valid (completed/running)
- ✅ All expected evaluations present

### Evaluation Checks

- ✅ Base evaluation: 100% complete with scores
- ✅ ICL evaluation: 100% complete with scores  
- ✅ Customized evaluation: 100% complete with scores (if customization enabled)
- ✅ Runtime metrics recorded
- ✅ Finish timestamps present

### Customization Checks (if enabled)

- ✅ Customization 100% complete
- ✅ Training epochs completed (> 0)
- ✅ Training steps completed (> 0)
- ✅ Finish timestamp present
- ✅ Corresponding customized evaluation exists

### LLM Judge Checks

- ✅ LLM judge deployment completed/ready

> **📖 For evaluation details:** See [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)

## Troubleshooting

### Services Not Available

**API Server Connection Issues:**

```text
❌ API Server is not accessible: Connection refused
```

**Solutions:**

- **Local Docker**: Ensure Data Flywheel services are running:
  ```bash
  docker-compose -f deploy/docker-compose.yaml up -d
  ```
- **Kubernetes**: Check services and set up port-forwarding:
  ```bash
  kubectl get pods -l app=df-api-deployment
  kubectl port-forward service/df-api-service 8000:8000
  ```
- **Custom URL**: Verify the API server URL is correct and accessible:
  ```bash
  curl https://your-api-server.com/api/jobs
  ```

**Elasticsearch Connection Issues:**

```text
❌ Cannot connect to Elasticsearch at https://your-elasticsearch.com
```

**Solutions:**

- **Local Docker**: Ensure Elasticsearch is running:
  ```bash
  curl http://localhost:9200/_cluster/health
  ```
- **Custom URL**: Verify Elasticsearch URL and authentication:
  ```bash
  curl https://your-elasticsearch.com/_cluster/health
  ```

### Job Creation Failed

```text
❌ Failed to create job: 400 - No data found for workload_id
```
**Solution**: Check that test data was loaded properly or use different workload/client IDs.

### Data Loading Failed

**Dataset File Not Found:**

```text
❌ Invalid data file path 'my_dataset.jsonl': File not found: /path/to/project/my_dataset.jsonl
```
**Solutions**:

- Ensure the dataset file exists at the specified path
- For default dataset: `ls -la data/aiva_primary_assistant_dataset.jsonl`
- For custom dataset: Check the path you provided with `--data-file`

**Path Security Restrictions:**

```text
❌ Path '../external_data.jsonl' resolves outside the project directory.
   Only paths within the project are allowed for security.
```
**Solutions**:

- Move the data file into the project directory: `cp /external/data.jsonl data/`
- Use `--allow-external-files` flag for standalone mode:
  ```bash
  python tests/e2e_scripts/simple_e2e_test.py \
    --data-file /external/data.jsonl --allow-external-files
  ```
- **Note**: Pytest mode always enforces security (no external files allowed)

**Missing Dependencies:**

```text
❌ Missing required dependencies for standalone usage:
   • requests
   • elasticsearch
```
**Solutions**:

```bash
# Option 1: Install minimal dependencies
pip install requests elasticsearch

# Option 2: Install full development environment
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

**Bulk Indexing Errors:**

```text
❌ Bulk indexing error: Connection timeout
```
**Solutions:**

- Check Elasticsearch cluster health and load
- Reduce chunk size if memory constraints exist
- Verify network connectivity to Elasticsearch
- Check Elasticsearch logs for specific errors

**Index Creation Failed:**

```text
❌ Failed to create index: Permission denied
```
**Solution**: Verify Elasticsearch user has index creation permissions:
```bash
curl -X GET "https://your-elasticsearch.com/_cat/indices/flywheel*"
```

**Performance Issues:**

```text
[INFO] Processing document 1000/5079... (taking too long)
```
**Solutions:**

- Verify Elasticsearch cluster resources (CPU, memory)
- Check network latency to Elasticsearch
- Monitor Elasticsearch indexing queue

### Custom URL Configuration

**Using Different Ports:**
```bash
# If API is port-forwarded to a different port
uv run python tests/e2e_scripts/simple_e2e_test.py --api-url "http://localhost:8001"

# If both services use custom ports
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "http://localhost:8001" \
  --elasticsearch-url "http://localhost:9201"
```

**Production/Remote Services:**
```bash
# Production deployment with custom domains
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "https://dataflywheel-api.company.com" \
  --elasticsearch-url "https://dataflywheel-es.company.com"

# Mixed local and remote services
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "http://localhost:8000" \
  --elasticsearch-url "https://remote-elasticsearch.com"
```

**HTTPS and Authentication:**
```bash
# HTTPS endpoints with authentication handled by environment
export ELASTIC_PASSWORD="your-password"
uv run python tests/e2e_scripts/simple_e2e_test.py \
  --api-url "https://secure-api.company.com" \
  --elasticsearch-url "https://secure-es.company.com"
```

### Job Timeout

```text
⏰ Job monitoring timed out after 90 minutes
```
**Solution**: Increase timeout or check for stuck processes:
```bash
# Check job status manually (use your actual job_id)
curl http://localhost:8000/api/jobs/${job_id}

# Increase timeout
uv run python tests/e2e_scripts/simple_e2e_test.py --timeout 120
```

### Verification Failures

```text
❌ Verification failed with the following issues:
  • Missing base evaluation for meta/llama-3.2-1b-instruct
```
**Solution**: Check the job details for specific component failures:
```bash
# Use your actual job_id
curl http://localhost:8000/api/jobs/${job_id} | jq
```

## Pytest Markers and Test Organization

The test class is explicitly decorated with pytest markers for clear organization:

```python
@pytest.mark.e2e_integration
@pytest.mark.slow
class TestE2EIntegration:
    def test_full_e2e_workflow(self, e2e_config):
        # Test implementation
```

**Marker Definitions:**

- `@pytest.mark.e2e_integration`: Marks the test as an end-to-end integration test
- `@pytest.mark.slow`: Marks the test as slow-running (useful for filtering)

**Running specific test types:**

```bash
# Run only E2E integration tests
pytest -m e2e_integration

# Skip slow tests (useful for quick test runs)
pytest -m "not slow"

# Run E2E tests but skip slow ones (contradictory - would skip our E2E test since it's marked as slow)
pytest -m "e2e_integration and not slow"  # This would skip our E2E test

# Run all tests except E2E integration
pytest -m "not e2e_integration"

# Run E2E tests with verbose output
pytest -m e2e_integration -v

# Run E2E tests from specific directory
pytest -m e2e_integration tests/e2e_scripts/
```

**Marker Usage Guide:**

- `e2e_integration`: Identifies end-to-end integration tests that require full system setup
- `slow`: Marks tests that take significant time (E2E tests typically run 45-90 minutes)
- Use markers to filter tests in CI/CD pipelines or during development
- Combine markers with boolean logic (`and`, `or`, `not`) for precise test selection

## Integration with CI/CD

Both standalone and pytest modes return proper exit codes:

- **Exit Code 0**: Test passed
- **Exit Code 1**: Test failed

**Example CI/CD configurations:**

**Using Standalone Script:**
```yaml
- name: Run E2E Integration Test
  run: |
    # Start services (example with Docker Compose)
    docker-compose -f deploy/docker-compose.yaml up -d
    
    # Wait for services to be ready
    sleep 60
    
    # Run test with default localhost URLs
    uv run python tests/e2e_scripts/simple_e2e_test.py --timeout 120
```

**Using Custom URLs in CI:**
```yaml
- name: Run E2E Integration Test (Remote)
  env:
    API_URL: https://staging-api.company.com
    ELASTICSEARCH_URL: https://staging-es.company.com
  run: |
    # Run test with custom URLs from environment
    uv run python tests/e2e_scripts/simple_e2e_test.py \
      --api-url "$API_URL" \
      --elasticsearch-url "$ELASTICSEARCH_URL" \
      --timeout 120
```

**Using Pytest:**
```yaml
- name: Run E2E Integration Test with Pytest
  run: |
    # Start services (example with Docker Compose)
    docker-compose -f deploy/docker-compose.yaml up -d
    
    # Wait for services to be ready
    sleep 60
    
    # Run E2E tests with pytest
    uv run pytest -m e2e_integration tests/e2e_scripts/ --e2e-timeout 120 -v
```

## Related Documentation

- **[Getting Started](get-started/index.md)**: Local development setup using Docker Compose
- **[Configuration Guide](02-configuration.md)**: Detailed configuration options and examples  
- **[API Reference](06-api-reference.md)**: Complete API documentation for job management
- **[Workflow Orchestration](07-workflow-orchestration.md)**: Understanding the task orchestration system
- **[Helm Installation](10-helm-installation.md)**: Kubernetes deployment for production environments

## Extending the Test

### Custom Test Data

**Using Command Line (Recommended):**

```bash
# Use custom dataset file (within project)
python tests/e2e_scripts/simple_e2e_test.py --data-file data/my_custom_dataset.jsonl

# Use dataset from external directory (standalone mode)
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file /external/path/to/dataset.jsonl \
  --allow-external-files

# Use uv for full dependency management
uv run python tests/e2e_scripts/simple_e2e_test.py --data-file data/my_custom_dataset.jsonl
```

**Programmatically (for custom test classes):**

```python
# Create test instance with custom data file (project-only)
test = SimpleE2ETest(
    workload_id="custom-test",
    data_file="data/my_custom_dataset.jsonl",
    standalone_mode=False  # Enforces security checks
)

# Create test instance with external data file (standalone)
test = SimpleE2ETest(
    workload_id="custom-test",
    data_file="/external/path/to/dataset.jsonl",
    standalone_mode=True  # Allows external files
)
```

### Bulk Loading Performance Tuning
Adjust bulk indexing parameters for different environments:
```python
# In _bulk_index_documents method
# For high-performance clusters
chunk_size = 500

# For memory-constrained environments  
chunk_size = 50

# Custom progress logging intervals
if i > 0 and i % 500 == 0:  # Log every 500 documents
    self.log(f"Processing document {i}/{len(test_data)}...")
```

### Additional Verification Checks
Add custom verification in `verify_results()`:
```python
# Check specific evaluation metrics
for eval_data in evaluations:
    scores = eval_data.get("scores", {})
    if "function_name_accuracy" in scores:
        accuracy = scores["function_name_accuracy"]
        if accuracy < 0.8:
            issues.append(f"Function name accuracy too low: {accuracy}")

# Add data volume validation
total_records = job_data.get("num_records", 0)
if total_records < 5000:
    issues.append(f"Expected at least 5000 records, got {total_records}")
```

### Performance Monitoring
Add custom timing and performance logging:
```python
import time

def _load_data_to_elasticsearch(self, ...):
    start_time = time.time()
    
    # ... existing bulk loading code ...
    
    end_time = time.time()
    duration = end_time - start_time
    records_per_second = len(test_data) / duration
    self.log(f"Loading performance: {records_per_second:.1f} records/second")
```

### Different Timeouts per Phase
Modify monitoring logic for phase-specific timeouts:
```python
# Different timeouts for different phases
deployment_timeout = 20  # minutes
evaluation_timeout = 30   # minutes  
customization_timeout = 40  # minutes
```

## Embedding Pipeline E2E Test

The embedding E2E test (`tests/e2e_scripts/embedding_e2e_test.py`) validates the embedding model workflow, which differs significantly from the LLM pipeline in terms of data format, workflow, and evaluation metrics.

### Overview

The embedding pipeline test follows the same dual-mode pattern (standalone/pytest) as the LLM test but is specifically adapted for embedding workflows:

**Test Workflow:**
1. Service health check
2. Load embedding triplet data to Elasticsearch
3. Create embedding job
4. Monitor job progress
5. Verify evaluations (base and customized)
6. Cleanup

### Key Differences from LLM Test

The embedding pipeline has several important differences:

| Aspect | LLM Test | Embedding Test |
|--------|----------|----------------|
| **Data Format** | OpenAI request/response pairs | Triplet format (query, pos_doc, neg_doc) |
| **Collection** | `flywheel` | `flywheel_embeddings_data` |
| **Workflow** | base-eval → ICL-eval → customization → customized-eval | base-eval → customization → customized-eval |
| **Evaluations** | 3 types (base, ICL, customized) | 2 types (base, customized) |
| **Metrics** | similarity, function_name, tool_calling_correctness | recall_5, recall_10, ndcg_cut_5, ndcg_cut_10 |
| **LLM Judge** | Required for evaluation | Not used (retriever benchmarks) |
| **Benchmark** | Custom evaluation datasets | SciDocs retriever benchmark |

### Usage

**Default (embedding dataset):**
```bash
# Standalone mode (minimal dependencies)
python tests/e2e_scripts/embedding_e2e_test.py

# With uv (recommended)
uv run python tests/e2e_scripts/embedding_e2e_test.py
```

**Custom dataset:**
```bash
# With custom dataset file (within project)
python tests/e2e_scripts/embedding_e2e_test.py \
  --data-file data/my_embeddings.jsonl

# With external dataset file (outside project)
python tests/e2e_scripts/embedding_e2e_test.py \
  --data-file /path/to/external/embeddings.jsonl \
  --allow-external-files
```

**Custom URLs:**
```bash
# Run with custom API and Elasticsearch URLs
python tests/e2e_scripts/embedding_e2e_test.py \
  --api-url https://my-api-server.com \
  --elasticsearch-url https://my-elasticsearch.com
```

**With pytest:**
```bash
# Basic pytest execution
pytest -m e2e_integration tests/e2e_scripts/embedding_e2e_test.py

# With verbose output
pytest -m e2e_integration tests/e2e_scripts/embedding_e2e_test.py -s -v

# With custom parameters
pytest -m e2e_integration tests/e2e_scripts/embedding_e2e_test.py \
  --workload-id "my-embedding-test" --e2e-timeout 120
```

### Command Line Options

**Standalone Script Options:**
- `--workload-id`: Workload identifier for test data (default: "embedding-test")
- `--client-id`: Client identifier for test data (default: "test-client")
- `--timeout`: Timeout in minutes for job completion (default: 90)
- `--api-url`: API server URL (default: "http://localhost:8000")
- `--elasticsearch-url`: Elasticsearch URL (default: "http://localhost:9200")
- `--data-file`: Data file path (default: "data/embedding_data.jsonl"). Supports both .json and .jsonl
- `--allow-external-files`: Allow data files outside the project directory (security flag)

**Pytest Options:**
Same as LLM test (see above), but uses `embedding_e2e_config` fixture with default embedding dataset.

### Data Format Requirements

The embedding test accepts triplet data in two formats:

**List format:**
```json
["What is machine learning?", "ML is a subset of AI that enables computers to learn from data.", "Cooking involves preparing food using various techniques."]
```

**Dict format:**
```json
{
  "query": "What is machine learning?",
  "pos_doc": "ML is a subset of AI that enables computers to learn from data.",
  "neg_doc": "Cooking involves preparing food using various techniques."
}
```

**JSONL file example:**
```jsonl
["query 1", "positive doc 1", "negative doc 1"]
["query 2", "positive doc 2", "negative doc 2"]
```

**JSON file example:**
```json
[
  ["query 1", "positive doc 1", "negative doc 1"],
  ["query 2", "positive doc 2", "negative doc 2"]
]
```

Or with dict format:
```json
[
  {"query": "query 1", "pos_doc": "positive doc 1", "neg_doc": "negative doc 1"},
  {"query": "query 2", "pos_doc": "positive doc 2", "neg_doc": "negative doc 2"}
]
```

### What the Test Does

#### 1. Service Health Check (30 seconds)
```
✅ API Server is available
✅ Elasticsearch is available
```

#### 2. Embedding Data Loading (30-60 seconds)

The test loads embedding triplet data:
- **Dataset**: Configurable (default: embedding_data.jsonl with 10,000+ triplets)
- **Format**: Query-positive document-negative document triplets
- **Collection**: `flywheel_embeddings_data` in Elasticsearch
- **Method**: Direct bulk indexing with progress logging
- **Performance**: Processes in chunks of 100 for optimal performance

**Example output:**
```
[INFO] Loading embedding test data to Elasticsearch from: /path/to/project/data/embedding_data.jsonl
[INFO] Loaded 10000 records from file
[INFO] Processing document 100/10000...
[INFO] ✅ Successfully indexed 100 documents
[INFO] ✅ Successfully indexed 10000/10000 embedding triplets
```

#### 3. Job Creation (< 30 seconds)
```
✅ Embedding job created successfully: 64f8a1b2c3d4e5f6g7h8i9j0
```

Job request includes `job_type: "embedding_job"` and embedding-specific data split config:
```python
{
    "val_ratio": 0.05,
    "min_total_records": 50,
    "random_seed": 42,
    "limit": 1000
}
```

#### 4. Job Monitoring (60-90 minutes)

Real-time progress logging (no ICL or LLM judge):
```
📊 Job Status: running | Records: 1000 | Elapsed: 0:15:23
  🤖 NIM 1: nvidia/llama-3.2-nv-embedqa-1b-v2
     Status: running | Deployment: ready
     📈 Evaluations (2):
       ✅ BASE-EVAL: 100.0%
       🔄 CUSTOMIZED-EVAL: 45.2%
     🎯 Customizations (1):
       🔄 Fine-tuning: 65.3% (epochs: 0, steps: 423)
```

#### 5. Result Verification (1-2 minutes)

Comprehensive validation for embedding-specific metrics:
- ✅ Job completion status
- ✅ Model deployment verification
- ✅ Evaluation completion (base and customized only)
- ✅ Embedding metrics present (recall_5, recall_10, ndcg_cut_5, ndcg_cut_10)
- ✅ Customization success (epochs, steps, progress)
- ✅ Score generation and runtime metrics

**Note:** No ICL evaluation or LLM judge verification for embeddings.

#### 6. Test Summary
```
📋 SUMMARY:
  Job ID: 64f8a1b2c3d4e5f6g7h8i9j0
  Status: completed
  Records Processed: 1000
  NIMs Tested: 1
  Total Evaluations: 2
  Total Customizations: 1
  Total Runtime: 1:15:32
```

### Expected Test Duration

- **Data Loading:** 30-60 seconds (depending on dataset size)
- **NIM Deployment:** 10-15 minutes
- **Base Evaluation:** 15-20 minutes (SciDocs benchmark)
- **Customization:** 30-40 minutes
- **Customized Evaluation:** 15-20 minutes
- **Total:** ~60-90 minutes

### Verification Checks

The test performs comprehensive validation specific to embeddings:

#### Job-Level Checks
- ✅ Job status is "completed"
- ✅ Records were processed (> 0)
- ✅ Job has start and finish times
- ✅ No errors reported

#### NIM-Level Checks
- ✅ NIM deployment completed
- ✅ NIM status is valid (completed/running)
- ✅ Expected evaluations present (2 only: base and customized)

#### Evaluation Checks
- ✅ Base evaluation: 100% complete with embedding metrics
- ✅ Customized evaluation: 100% complete with embedding metrics
- ✅ All embedding metrics present: recall_5, recall_10, ndcg_cut_5, ndcg_cut_10
- ✅ Runtime metrics recorded
- ✅ Finish timestamps present

**Note:** No ICL evaluation verification for embeddings.

#### Customization Checks
- ✅ Customization 100% complete
- ✅ Training epochs completed (> 0)
- ✅ Training steps completed (> 0)
- ✅ Finish timestamp present
- ✅ Corresponding customized evaluation exists

**Note:** No LLM judge checks for embeddings.

### Troubleshooting

#### Data Loading Issues

**Dataset File Not Found:**
```
❌ Invalid data file path 'embedding_data.jsonl': File not found: /path/to/project/data/embedding_data.jsonl
```
**Solutions:**
- Ensure the embedding dataset exists: `ls -la data/embedding_data.jsonl`
- Pull Git LFS files: `git lfs pull`
- Use a different dataset: `--data-file data/my_embeddings.jsonl`

**Invalid Triplet Format:**
```
⚠️  Skipping invalid record: missing required keys
```
**Solution:** Ensure your data follows the triplet format:
```json
["query", "pos_doc", "neg_doc"]
```
or
```json
{"query": "...", "pos_doc": "...", "neg_doc": "..."}
```

**Wrong Collection:**
```
❌ No data found for workload_id
```
**Solution:** Verify data is in `flywheel_embeddings_data` collection (not `flywheel`)

#### Evaluation Issues

**Missing Metrics:**
```
❌ Evaluation base-eval for nvidia/llama-3.2-nv-embedqa-1b-v2 missing metric: recall_5
```
**Solution:** Check that the evaluation completed successfully and uses the SciDocs benchmark.

**Wrong Number of Evaluations:**
```
❌ NIM has insufficient evaluations: 3 (expected ≥2)
```
**Note:** Embeddings should have exactly 2 evaluations (base and customized), not 3 like LLMs.

### Integration with CI/CD

**Using Standalone Script:**
```yaml
- name: Run Embedding E2E Test
  run: |
    # Start services
    docker-compose -f deploy/docker-compose.yaml up -d
    
    # Wait for services
    sleep 60
    
    # Run embedding test with default embedding dataset
    uv run python tests/e2e_scripts/embedding_e2e_test.py --timeout 120
```

**Using Pytest:**
```yaml
- name: Run Embedding E2E Test with Pytest
  run: |
    # Start services
    docker-compose -f deploy/docker-compose.yaml up -d
    
    # Wait for services
    sleep 60
    
    # Run embedding E2E tests
    uv run pytest -m e2e_integration tests/e2e_scripts/embedding_e2e_test.py --e2e-timeout 120 -v
```

### Extending the Embedding Test

#### Custom Embedding Datasets

Create your own triplet dataset:
```python
# Generate triplets for your domain
triplets = [
    {
        "query": "domain-specific query",
        "pos_doc": "relevant document",
        "neg_doc": "irrelevant document"
    }
]

# Save to JSONL
with open('data/my_embeddings.jsonl', 'w') as f:
    for triplet in triplets:
        f.write(json.dumps(triplet) + '\n')

# Run test
python tests/e2e_scripts/embedding_e2e_test.py --data-file data/my_embeddings.jsonl
```

#### Custom Embedding Metrics

Extend verification to check specific metric values:
```python
# In verify_results method
scores = eval_data.get("scores", {})
recall_5 = scores.get("recall_5", 0)
if recall_5 < 0.5:
    issues.append(f"Recall@5 too low: {recall_5}")
```
