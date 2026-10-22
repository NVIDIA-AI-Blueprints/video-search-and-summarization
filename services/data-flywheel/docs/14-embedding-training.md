# Embedding Model Training Guide

This guide provides comprehensive documentation for training embedding models using the Data Flywheel Blueprint. Embedding models are essential for semantic search, retrieval-augmented generation (RAG), and similarity-based applications.

## Table of Contents

- [Embedding Model Training Guide](#embedding-model-training-guide)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Supported Models](#supported-models)
  - [Data Format](#data-format)
    - [Triplet Format](#triplet-format)
    - [Input File Formats](#input-file-formats)
      - [1. List Format (JSONL)](#1-list-format-jsonl)
      - [2. Dictionary Format (JSONL)](#2-dictionary-format-jsonl)
      - [3. CSV Format (Single File)](#3-csv-format-single-file)
      - [4. Separated Bug Pairs Format](#4-separated-bug-pairs-format)
      - [5. Create Custom Evaluation Data](#5-create-custom-evaluation-data)
  - [Configuration](#configuration)
    - [Model Configuration](#model-configuration)
    - [Training Parameters](#training-parameters)
      - [Key Training Parameters](#key-training-parameters)
    - [Data Split Configuration](#data-split-configuration)
    - [Evaluation Metrics](#evaluation-metrics)
  - [Loading Data](#loading-data)
    - [Load Embedding Training Data](#load-embedding-training-data)
    - [Data Loader Options for Training Data](#data-loader-options-for-training-data)
    - [Load BEIR Format Evaluation Data](#load-beir-format-evaluation-data)
    - [Getting Help](#getting-help)
    - [Programmatic Loading](#programmatic-loading)
  - [Training Process](#training-process)
    - [Fine-tuning Approaches](#fine-tuning-approaches)
      - [1. LoRA Fine-tuning](#1-lora-fine-tuning)
      - [2. Full Fine-tuning (SFT)](#2-full-fine-tuning-sft)
      - [3. Iterative Fine-tuning (Training on Customized Models)](#3-iterative-fine-tuning-training-on-customized-models)
    - [Training Workflow](#training-workflow)
  - [Running Embedding Jobs](#running-embedding-jobs)
    - [Basic Embedding Job](#basic-embedding-job)
    - [Monitoring Job Status](#monitoring-job-status)
    - [Cancel Running Job](#cancel-running-job)
    - [Delete Job](#delete-job)
    - [Running Embedding Jobs with Different Configurations](#running-embedding-jobs-with-different-configurations)
  - [Evaluation](#evaluation)
    - [1. Split Dataset Evaluation (Always Runs)](#1-split-dataset-evaluation-always-runs)
    - [2. Custom Dataset Evaluation (Optional)](#2-custom-dataset-evaluation-optional)
    - [Retrieval Metrics](#retrieval-metrics)
    - [Evaluation Configuration](#evaluation-configuration)
      - [Loading BEIR Format Evaluation Data](#loading-beir-format-evaluation-data)
      - [BEIR Data Format Requirements](#beir-data-format-requirements)
      - [Configuring Custom Evaluations](#configuring-custom-evaluations)
  - [Best Practices](#best-practices)
    - [Data Quality](#data-quality)
    - [Training Configuration](#training-configuration)
  - [Examples](#examples)
    - [Example 1: Load Embedding Data](#example-1-load-embedding-data)
    - [Example 2: Run Embedding Training Job](#example-2-run-embedding-training-job)

## Overview

The Data Flywheel Blueprint supports comprehensive embedding model training with the following capabilities:

- **Triplet-based Training**: Uses query-positive-negative triplets for contrastive learning
- **Multiple Fine-tuning Methods**: Supports both SFT (Supervised Fine-Tuning) and LoRA (Low-Rank Adaptation)
- **Automatic Negative Sampling**: Intelligently selects hard negatives from your dataset
- **Retrieval Evaluation**: Evaluates models using standard retrieval metrics (MAP, Recall@k, Precision@k)
- **Scalable Training**: Supports distributed training with tensor and data parallelism

## Supported Models

Currently supported embedding models:

```yaml
embedding_job_config:
  nims:
    - model_name: "nvidia/llama-3.2-nv-embedqa-1b-v2"
      model_type: "embedding"
      context_length: 32768
      gpus: 1
      pvc_size: 25Gi
      tag: "1.9.0"
      customization_enabled: true
      customizer_configs:
        target: "nvidia/llama-3.2-nv-embedqa-1b@v2"
        gpus: 1
        max_seq_length: 8192
```

## Data Format

### Triplet Format

Embedding training uses a triplet format where each record contains:

- **Query**: The search query or question
- **Positive Document**: A relevant/correct document for the query
- **Negative Documents**: Irrelevant documents (1-5 recommended)

This format enables contrastive learning where the model learns to:

1. Maximize similarity between query and positive document
2. Minimize similarity between query and negative documents

### Input File Formats

The system supports multiple input formats:

#### 1. List Format (JSONL)

Each line contains a list with three elements:

```json
["query text", "positive document", ["negative doc 1", "negative doc 2", "negative doc 3"]]
```

Example:

```json
["What is machine learning?", "Machine learning is a subset of AI that enables systems to learn from data.", ["Python is a programming language.", "The weather is sunny today."]]
["How to train a neural network?", "Training a neural network involves forward propagation, loss calculation, and backpropagation.", ["Cats are popular pets.", "Pizza is a popular food."]]
```

**Usage:**

```bash
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --file embedding_list_format.jsonl
```

#### 2. Dictionary Format (JSONL)

Each line contains a dictionary with explicit field names:

```json
{
  "query": "What is deep learning?",
  "pos_doc": "Deep learning is a subset of machine learning using neural networks with multiple layers.",
  "neg_doc": ["Cooking requires various ingredients.", "Basketball is played with a ball."]
}
```

**Usage:**

```bash
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --file embedding_dict_format.jsonl
```

#### 3. CSV Format (Single File)

For CSV files, use the following required columns:

- `bugid`: Unique identifier for the query
- `similar_bug`: Identifier for the similar/positive document  
- `bugdata`: Query text
- `similarbug_data`: Positive document text

Example CSV:

```csv
bugid,similar_bug,bugdata,similarbug_data
"Q001","D001","What is NLP?","Natural Language Processing is a field of AI focusing on human language."
"Q002","D002","How does machine learning work?","Machine learning algorithms learn patterns from data to make predictions."
```

**Usage:**

```bash
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --file embedding_data.csv
```

#### 4. Separated Bug Pairs Format

This format uses two files: a corpus file (CSV) and a mappings file (CSV).

> **Note**: If you want to use the core-bugs dataset, follow the data curation steps in [embedding-example-launchable.ipynb](../notebooks/embedding-example-launchable.ipynb) before using the commands.

**Corpus File (for example, `Core_bugs.csv`)**
Contains the text data. Required columns:

- `bugid`: Unique identifier for the document
- Other columns are combined to form the text content.

**Mappings File (for example, `Core_bugs-combined.csv`)**
Contains the relationships between queries and positive documents. Required columns:

- `bugid`: The query ID
- `Duplicates`: A comma-separated string of positive document IDs (for example, "123,456")

Usage with `--dataset-type bug_pairs_separated`:

```bash
# Note: Files can be in data/ directory or use explicit paths
uv run src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client  \
  --dataset-type bug_pairs_separated \
  --file Core_bugs.csv \
  --chunk-size 5000 \
  --mappings-file Core_bugs-combined.csv
```

> **💡 Path Handling:** You can use `--file Core_bugs.csv` (automatically looks in `data/`) or absolute paths like `--file /path/to/Core_bugs.csv`.

Note: When using CSV format or separated bug pairs, negative samples are automatically generated during the data curation process if not provided.

#### 5. Create Custom Evaluation Data

The `create_custom_eval_embedding_data.py` script allows you to convert your raw data into the BEIR format required for custom evaluations.

**Usage:**

```bash
# Place your input files in the data/ directory
uv run python src/scripts/create_custom_eval_embedding_data.py \
  --eval-records eval_records.csv \
  --corpus-file-path corpus.csv \
  --output-path custom-eval-dataset \
  --limit 1000
```

> **💡 Data File Location:** All file path parameters accept relative paths (resolved from `data/` directory) or absolute paths.

**Parameters:**

- `--eval-records`: Path to the evaluation records file (CSV or JSONL). Contains query IDs and their relevant document IDs.
- `--corpus-file-path`: Path to the full corpus file (CSV). Contains all documents.
- `--output-path`: Directory where the BEIR format files (`corpus.jsonl`, `queries.jsonl`, `qrels/test.tsv`) will be saved.
- `--limit` (optional): Limit the number of records processed.

This script performs the following steps:

1. Loads the corpus and evaluation records.
2. Filters the records to ensure valid references.
3. Creates the standard BEIR files:
   - `corpus.jsonl`: All documents.
   - `queries.jsonl`: All queries.
   - `qrels/test.tsv`: Relevance judgments (query-document pairs).

After creating the dataset, you can load it using the evaluation data loader as shown below.

## Configuration

### Model Configuration

Configure embedding models in `config/config.yaml`:

```yaml
embedding_job_config:
  nims:
    - model_name: "nvidia/llama-3.2-nv-embedqa-1b-v2"
      model_type: "embedding"
      context_length: 32768
      gpus: 1
      pvc_size: 25Gi
      tag: "1.9.0"
      customization_enabled: true
      customizer_configs:
        target: "nvidia/llama-3.2-nv-embedqa-1b@v2"
        gpus: 1
        max_seq_length: 8192
```

### Training Parameters

Configure training parameters for embedding models:

```yaml
embedding_job_config:
  training_config:
    training_type: "sft"
    finetuning_type: "lora_merged"  # Options: "lora_merged", "all_weights"
    epochs: 1
    batch_size: 64
    learning_rate: 0.000025
    min_learning_rate: 0.0000005
    
    # Dataset-specific parameters
    dataset_parameters:
      in_batch_negatives: true  # Use in-batch negative sampling
      num_hard_negatives: 1     # Number of hard negatives per sample
    
    # Training optimization
    micro_batch_size: 1
    weight_decay: 0.01
    warmup_steps: 100
    
    # Parallelism configuration
    tensor_parallel_size: 1
    pipeline_parallel_size: 1
    data_parallel_size: 1
    use_sequence_parallel: false
    training_precision: "bf16-mixed"
    
    # LoRA configuration (when using LoRA fine-tuning)
    lora:
      adapter_dim: 32
      alpha: 64
      adapter_dropout: 0.01
```

#### Key Training Parameters

| Parameter | Description | Recommended Value | Notes |
|-----------|-------------|-------------------|-------|
| `finetuning_type` | Fine-tuning method | "lora_merged" | Use "lora_merged" for efficient training with full model output |
| `batch_size` | Training batch size | 64 | Larger batches improve contrastive learning |
| `learning_rate` | Initial learning rate | 0.000025 | Lower than standard LLM fine-tuning |
| `in_batch_negatives` | Use in-batch sampling | true | Improves training efficiency |
| `num_hard_negatives` | Hard negatives per sample | 1 | Balance between quality and efficiency |
| `warmup_steps` | Learning rate warmup | 100 | Prevents early training instability |
| `tensor_parallel_size` | Tensor parallelism | 1 | Distribute model across GPUs |

### Data Split Configuration

Configure how data is split for training, validation, and evaluation:

```yaml
embedding_job_config:
  data_split_config:
    eval_size: 100         # 100 samples for evaluation
    val_ratio: 0.09        # 9% for validation
    min_total_records: 50  # Minimum dataset size
    random_seed: 42        # For reproducibility
    limit: null            # Default: null (pull all records); set to number to limit
    from_date: null        # Optional: Filter records from this date (YYYY-MM-DD)
    to_date: null          # Optional: Filter records to this date (YYYY-MM-DD)
    min_negatives: 1       # Minimum negative samples
    max_negatives: 5       # Maximum negative samples
```

**Temporal Filtering (Optional):**

Use `from_date` and `to_date` to pull data from specific time periods:

```yaml
data_split_config:
  from_date: "2024-01-01"  # Start date (inclusive)
  to_date: "2024-12-31"    # End date (inclusive)
  limit: null              # Pull all records in date range
```

> **Note:** Both `from_date` and `to_date` must be provided together or both must be `null`. The system filters Elasticsearch records by the `timestamp` field before performing train/val/eval splits.

### Evaluation Metrics

Configure retrieval evaluation metrics:

```yaml
embedding_job_config:
  evaluation_config:
    evaluation_metrics:
      - "recall_5"    # Recall at 5
      - "map"         # Mean Average Precision
      - "P_5"         # Precision at 5
      - "P_10"        # Precision at 10
```

## Loading Data

Use the unified data loader to load your embedding data into the system. This loader provides a consistent interface across all pipeline types and supports multiple data formats.

### Load Embedding Training Data

**Basic Usage:**

```bash
# Place your file in the data/ directory for easy access
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "retrieval-system" \
  --client-id "company-1" \
  --file "embedding_data.jsonl"
```

**With Optional Parameters:**

```bash
# Limit number of records and specify chunk size
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "retrieval-system" \
  --client-id "company-1" \
  --file "embedding_data.jsonl" \
  --limit 10000 \
  --chunk-size 5000
```

**Different Dataset Types:**

```bash
# Triplet format (default) - JSONL/JSON with query, pos_doc, neg_doc
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "retrieval-system" \
  --client-id "company-1" \
  --file "triplet_data.jsonl"

# Bug pairs format - Single CSV with bugid, similar_bug, bugdata, similarbug_data
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "retrieval-system" \
  --client-id "company-1" \
  --dataset-type bug_pairs \
  --file "bug_pairs.csv"

# Bug pairs separated - Two files: corpus and mappings
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "retrieval-system" \
  --client-id "company-1" \
  --dataset-type bug_pairs_separated \
  --file "Core_bugs.csv" \
  --mappings-file "Core_bugs-combined.csv" \
  --chunk-size 5000
```

> **📥 Download Mozilla Core Bugs Dataset:**
> The example above uses the Mozilla Core Bugs dataset. To download it, run:
> ```bash
> # Download the mappings file from GitHub
> cd data/
> curl -O https://raw.githubusercontent.com/av9ash/gitbugs/refs/heads/main/mozillacore/Core_bugs-combined.csv
> # Download the corpus from Google Drive (requires gdown)
> pip install gdown
> gdown 1zdjfEIDkdsFdCkClo4Tw80Ihdk6DAi6j -O Core_bugs.csv.zip
> unzip Core_bugs.csv.zip
> # The files are now in data/ directory and can be used with relative paths
> ```
> For detailed instructions, refer to [embedding-example-launchable.ipynb](../notebooks/embedding-example-launchable.ipynb).

> **💡 Data File Location:** The `--file` parameter accepts relative paths (resolved from `data/` directory) or absolute paths. For example:
> - Relative: `--file embedding_data.jsonl` → looks for `data/embedding_data.jsonl`
> - Absolute: `--file /path/to/embedding_data.jsonl` → uses the exact path provided

### Data Loader Options for Training Data

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `--type embedding` | Specifies embedding training data type | ✅ Yes | - |
| `--workload-id` | Unique identifier for the workload | ✅ Yes | - |
| `--client-id` | Unique identifier for the client | ✅ Yes | - |
| `--file` | Path to your data file (JSONL/JSON/CSV) | ✅ Yes | - |
| `--dataset-type` | Format: `triplet`, `bug_pairs`, or `bug_pairs_separated` | Optional | `triplet` |
| `--mappings-file` | Mappings file for `bug_pairs_separated` format | Conditional* | - |
| `--limit` | Limit number of records to load | Optional | All records |
| `--chunk-size` | Bulk indexing chunk size | Optional | `1000` |
| `--index-name` | Custom Elasticsearch index name | Optional | `flywheel_embeddings_data` |

> **Note:** *`--mappings-file` is required when using `--dataset-type bug_pairs_separated`

### Load BEIR Format Evaluation Data

For custom evaluation datasets in BEIR format, use the dedicated evaluation data loader:

```bash
# Place your custom-eval-dataset directory in the data/ directory
uv run python src/scripts/load_embedding_eval_data.py \
  --workload-id "val-set-1" \
  --client-id "mozillacorebugs" \
  --data-dir "custom-eval-dataset"
```

> **💡 Data Directory Location:** The `--data-dir` parameter accepts relative paths (resolved from `data/` directory) or absolute paths.

**BEIR Format Requirements**:

- `corpus.jsonl`: Document collection with `_id` and `text` fields
- `queries.jsonl`: Query set with `_id` and `text` fields  
- `qrels/test.tsv`: Relevance judgments mapping query IDs to document IDs

> **Note:** Custom evaluations are currently supported only for embedding models. LLM custom evaluation support is planned for a future release

### Getting Help

View all available options and examples:

```bash
# See usage examples for all data types
uv run python src/scripts/load_data.py --examples

# View help
uv run python src/scripts/load_data.py --help
```

### Programmatic Loading

Load data programmatically in Python:

```python
from src.scripts.load_embedding_data import load_embedding_data_to_elasticsearch

# Load training data
load_embedding_data_to_elasticsearch(
    workload_id="retrieval-system",
    client_id="company-1",
    file_path="data/embedding_data.jsonl",
    limit=10000
)
```

## Training Process

### Fine-tuning Approaches

The system supports two fine-tuning approaches for embedding models:

#### 1. LoRA Fine-tuning

- **Efficiency**: Uses low-rank adaptation for parameter-efficient training
- **Memory**: Requires less GPU memory
- **Speed**: Faster training
- **Use Case**: Recommended for most scenarios

Configuration:

```yaml
finetuning_type: "lora_merged"
lora:
  adapter_dim: 32
  alpha: 64
  adapter_dropout: 0.01
```

#### 2. Full Fine-tuning (SFT)

- **Flexibility**: Updates all model weights
- **Performance**: Can achieve better task-specific performance
- **Resources**: Requires more GPU memory and compute
- **Use Case**: When maximum performance is needed

Configuration:

```yaml
finetuning_type: "all_weights"
```

#### 3. Iterative Fine-tuning (Training on Customized Models)

Continue training on top of an already customized embedding model to iteratively improve performance with new data.

**Supported Combinations:**

- `all_weights` on `all_weights`
- `all_weights` on `lora_merged`

> **Note:** LoRA on LoRA combinations are not currently supported.

**Configuration:**

Refer to the example configuration file: [`config/embedding_iterative_finetuning_example.json`](../config/embedding_iterative_finetuning_example.json)

<details>
<summary>View example configuration</summary>

```json
{
  "nims": [
    {
      "model_name": "dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte",
      "model_type": "embedding",
      "context_length": 32768,
      "gpus": 1,
      "pvc_size": "25Gi",
      "tag": "1.9.0",
      "nim_image_model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "customization_enabled": true,
      "customizer_configs": {
        "gpus": 1,
        "max_seq_length": 8192,
        "target": {
          "name": "nvidia/llama-3.2-nv-embedqa-1b-custom@v2",
          "base_model": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "model_uri": "hf://dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte",
          "num_parameters": 1000000000,
          "precision": "bf16-mixed"
        }
      }
    }
  ]
}
```

</details>

**Key Fields:**

| Field | Description | Example |
|-------|-------------|---------|
| `model_name` | Full path to the customized model | `dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte` |
| `nim_image_model_name` | Original base NIM container image | `nvidia/llama-3.2-nv-embedqa-1b-v2` |
| `target.model_uri` | HuggingFace checkpoint URI in data-store | `hf://dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte` |
| `target.base_model` | Original base model name | `nvidia/llama-3.2-nv-embedqa-1b-v2` |

**Finding the Model URI:**

After a customization job completes, retrieve the model information from the job response:

```python
response = requests.get(f"{API_BASE_URL}/api/jobs/{job_id}")
job_data = response.json()
customized_model = job_data['nims'][0]['customizations'][0]['customized_model']
# Example: "dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte"

# The model_uri format is: hf://{customized_model}
model_uri = f"hf://{customized_model}"
```

### Training Workflow

1. **Data Preparation**
   - System automatically formats data into triplets
   - Applies negative sampling if needed
   - Splits data into train/val/eval sets

2. **Model Initialization**
   - Deploys base embedding model
   - Configures training parameters
   - Sets up distributed training if configured

3. **Training Process**
   - Uses contrastive loss with triplet format
   - Applies in-batch negative sampling
   - Performs gradient accumulation for effective batch sizes

4. **Checkpoint Management**
   - Saves model checkpoints periodically
   - Tracks best model based on validation metrics
   - Enables training resumption if interrupted

## Running Embedding Jobs

Once your embedding data is loaded and configuration is set, you can run embedding training jobs.

### Basic Embedding Job

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "workload_id": "retrieval-system",
    "client_id": "company-1",
    "job_type": "embedding_job"
  }'
```

**Save the job ID for subsequent API requests:**

```bash
# Copy the 'id' from the response above and export it
export job_id="your-job-id-here"
```

### Monitoring Job Status

Check the status of your embedding job:

```bash
curl -X GET http://localhost:8000/api/jobs/${job_id} \
  -H "Content-Type: application/json"
```

### Cancel Running Job

If you need to stop an embedding job that is currently running, you can cancel it:

```bash
curl -X POST http://localhost:8000/api/jobs/${job_id}/cancel
```

> **Note:** Only jobs that have not finished can be cancelled. Cancelled jobs will stop all active tasks and mark the job status as `cancelled`. This is useful when:
> - You notice configuration errors early in the job
> - You need to free up resources for a higher-priority job
> - Training metrics indicate the job is not converging properly
> - You want to adjust hyperparameters and restart training

### Delete Job

After an embedding job is complete or cancelled, you can delete it to clean up resources:

```bash
curl -X DELETE http://localhost:8000/api/jobs/${job_id}
```

> **⚠️ Important:** Running jobs must be cancelled first before they can be deleted. The delete operation removes the job and all associated resources, including:
> - **Customized embedding models** from NeMo Microservices Platform
> - **Evaluation jobs** and retrieval metrics from the evaluation service
> - **Training datasets** (triplet data) uploaded to NMP
> - **MLflow experiments** and tracking data (if MLflow is enabled)
> - **MongoDB records** (job metadata, NIMs, evaluations, customizations)

**Best Practice:** Delete completed jobs periodically to free up storage and keep your system organized, especially for embedding jobs which can generate large model artifacts. The deletion process runs in the background and cleans up resources across multiple services.

### Running Embedding Jobs with Different Configurations

You can create custom embedding configurations using the Configuration API and reference them when running jobs.

**Method 1: Use Default Configuration**

Run embedding job with the default `embedding_job_config` from `config.yaml`:

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "workload_id": "retrieval-system",
    "client_id": "company-1",
    "job_type": "embedding_job"
  }'
```

**Method 2: Create Custom Configuration via API**

You can create custom embedding configurations using JSON files or inline JSON.

**Using a JSON File:**

The repository includes an example embedding configuration file at `config/embedding_config_example.json`:

```bash
# Create configuration from JSON file
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d @config/embedding_config_example.json
```

**Using Inline JSON:**

**1. Create a new embedding configuration:**

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "embedding_high_performance",
    "description": "High-performance embedding training configuration",
    "config_type": "embedding_job",
    "config_data": {
      "nims": [
        {
          "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
          "model_type": "embedding",
          "context_length": 32768,
          "gpus": 1,
          "pvc_size": "25Gi",
          "tag": "1.9.0",
          "customization_enabled": true,
          "customizer_configs": {
            "target": "nvidia/llama-3.2-nv-embedqa-1b@v2",
            "gpus": 4,
            "max_seq_length": 8192
          }
        }
      ],
      "data_split_config": {
        "val_ratio": 0.09,
        "eval_size": 100,
        "min_total_records": 50,
        "random_seed": 42,
        "min_negatives": 1,
        "max_negatives": 5
      },
      "training_config": {
        "training_type": "sft",
        "finetuning_type": "all_weights",
        "epochs": 2,
        "batch_size": 128,
        "learning_rate": 0.00005,
        "min_learning_rate": 0.000001,
        "dataset_parameters": {
          "in_batch_negatives": true,
          "num_hard_negatives": 3
        },
        "micro_batch_size": 1,
        "weight_decay": 0.01,
        "warmup_steps": 200,
        "tensor_parallel_size": 1,
        "pipeline_parallel_size": 1,
        "data_parallel_size": 1,
        "use_sequence_parallel": false,
        "training_precision": "bf16-mixed"
      },
      "evaluation_config": {
        "evaluation_metrics": [
          "recall_5",
          "recall_10",
          "map",
          "P_5",
          "P_10"
        ]
      }
    }
  }'
```

**2. Run job with your custom configuration:**

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "workload_id": "retrieval-system",
    "client_id": "company-1",
    "job_type": "embedding_job",
    "config_name": "embedding_high_performance"
  }'
```

**3. List all configurations:**

```bash
curl -X GET http://localhost:8000/api/configs
```

**4. Get a specific configuration:**

```bash
curl -X GET http://localhost:8000/api/configs/embedding_high_performance
```

**5. Delete a configuration:**

```bash
curl -X DELETE http://localhost:8000/api/configs/embedding_high_performance
```

> **Note**: Use the Configuration API to create, update, and manage different embedding configurations dynamically. Reference them by name when starting jobs using the `config_name` parameter.

## Evaluation

The embedding evaluation system supports two types of evaluation:

### 1. Split Dataset Evaluation (Always Runs)

- **Automatically runs** with every embedding training job
- Uses held-out data from your training set (configured via `data_split_config.eval_size`)
- Tests model generalization on the same data distribution
- Evaluates both base and fine-tuned models

### 2. Custom Dataset Evaluation (Optional)

- **Only runs when custom datasets are provided** in configuration
- Uses separately loaded BEIR format datasets
- Tests model on specific domains or benchmarks
- Configure via `evaluation_config.custom_evaluations`

### Retrieval Metrics

Both evaluation types use standard retrieval metrics:

| Metric | Description | Range |
|--------|-------------|-------|
| **MAP** | Mean Average Precision | 0-1 |
| **Recall@k** | Fraction of relevant items in top k | 0-1 |
| **Precision@k** | Fraction of top k that are relevant | 0-1 |
| **NDCG@k** | Normalized Discounted Cumulative Gain | 0-1 |

### Evaluation Configuration

```yaml
embedding_job_config:
  # Split dataset evaluation (always runs)
  data_split_config:
    eval_size: 100  # Held-out samples for evaluation
  
  # Custom dataset evaluation (optional)
  evaluation_config:
    custom_evaluations:
      - name: "domain-test"
        workload_id: "test-dataset-1"
        client_id: "client-1"
```

The system supports custom evaluation datasets in BEIR format for domain-specific evaluation. BEIR (Benchmarking IR) is a standard format for information retrieval evaluation.

#### Loading BEIR Format Evaluation Data

Use the dedicated script to load BEIR format evaluation datasets:

```bash
# Load BEIR format evaluation dataset
uv run python src/scripts/load_embedding_eval_data.py \
  --workload-id "val-set-1" \
  --client-id "mozillacorebugs" \
  --data-dir "custom-eval-dataset"
```

#### BEIR Data Format Requirements

Your evaluation dataset must follow the BEIR format structure:

```text
custom-eval-dataset/
├── corpus.jsonl       # Document corpus
├── queries.jsonl      # Query set
└── qrels/
    └── test.tsv      # Relevance judgments
```

**File Formats:**

1. **corpus.jsonl** - Each line is a JSON object with document information:

```json
{"_id": "doc1", "text": "This is the first document content", "title": "Document 1"}
{"_id": "doc2", "text": "This is the second document content", "title": "Document 2"}
```

2. **queries.jsonl** - Each line is a JSON object with query information:

```json
{"_id": "q1", "text": "What is machine learning?"}
{"_id": "q2", "text": "How does neural network work?"}
```

3. **qrels/test.tsv** - Tab-separated file mapping query IDs to relevant document IDs:

```text
query-id    corpus-id    score
q1          doc1         1
q1          doc5         1
q2          doc2         1
```

#### Configuring Custom Evaluations

After loading your BEIR format data, configure it in your embedding job:

```yaml
embedding_job_config:
  evaluation_config:
    custom_evaluations:
      - name: "custom-eval-dataset"
        workload_id: "val-set-1"  # Must match loading params
        client_id: "mozillacorebugs"
```

The system will automatically run your custom evaluation alongside standard evaluations and report metrics separately.

## Best Practices

### Data Quality

1. **Ensure Relevance**: Positive documents should be highly relevant to queries.
2. **Hard Negatives**: Include challenging negative examples that are topically similar but not relevant.
3. **Diversity**: Include diverse queries and documents covering your domain.
4. **Balance**: Maintain balance between different query types.

### Training Configuration

1. **Batch Size**: Use largest batch size that fits in memory (64+ recommended)
2. **Learning Rate**: Start with 2.5e-5 and adjust based on validation metrics
3. **Warmup**: Use 5-10% of total steps for warmup
4. **Negatives**: Use 3-5 hard negatives per sample for best results

## Examples

### Example 1: Load Embedding Data

**Load Training Data:**

Load your embedding training data into the system using the unified data loader:

```bash
# Load CSV format data (files in data/ directory or explicit paths)
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client  \
  --dataset-type bug_pairs_separated \
  --file Core_bugs.csv \
  --chunk-size 5000 \
  --mappings-file Core_bugs-combined.csv

# Load JSONL format data with limit (relative path - looks in data/ directory)
uv run python src/scripts/load_data.py --type embedding \
  --workload-id "semantic-search" \
  --client-id "team-alpha" \
  --file "embedding_triplets.jsonl" \
  --limit 10000
```

**Load BEIR Format Evaluation Data:**

For custom evaluation datasets in BEIR format, use the dedicated script:

```bash
# Load BEIR format evaluation dataset (relative path - looks in data/ directory)
uv run python src/scripts/load_embedding_eval_data.py \
  --workload-id "val-set-1" \
  --client-id "mozillacorebugs" \
  --data-dir "custom-eval-dataset"
```

> **💡 Data File Location:** All `--file` and `--data-dir` parameters accept relative paths (resolved from `data/` directory) or absolute paths.

### Example 2: Run Embedding Training Job

Start an embedding training job with default configuration:

```bash
# Using curl
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "workload_id": "retrieval-qa",
    "client_id": "company-1",
    "job_type": "embedding_job"
  }'

# Using Python
import requests

response = requests.post(
    "http://localhost:8000/api/jobs",
    json={
        "workload_id": "retrieval-qa",
        "client_id": "company-1",
        "job_type": "embedding_job"
    }
)
print(f"Job ID: {response.json()['job_id']}")
```
