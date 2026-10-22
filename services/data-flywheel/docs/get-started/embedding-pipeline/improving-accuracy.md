# Improving Accuracy of Baseline Model

Learn how to fine-tune pre-trained embedding models to improve accuracy on your domain-specific data.

## Overview

Pre-trained embedding models provide a good starting point, but they may not perform optimally on your specific domain. Fine-tuning these baseline models with your domain data can significantly improve:

- **Retrieval Accuracy**: Better matching of queries to relevant documents
- **Domain Understanding**: Improved understanding of domain-specific terminology
- **Task Performance**: Better performance on your specific use cases

## Data Format

For embedding model training, use triplet format with query-positive-negative samples. Each record represents:

- `query`: The search query or question
- `pos_doc`: A relevant/correct document for the query
- `neg_doc`: List of irrelevant documents (1-5 negatives recommended)

> **📖 For complete embedding data format and training guide:** Refer to [Embedding Training Guide](../../14-embedding-training.md#data-format)

> **Note**
> `workload_id` and `client_id` must be provided when loading data with the embedding script.

> **Note:** Unlike LLM tool-calling use case, there is no pre-existing embedding dataset in this repository. Users must follow the [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb) notebook to download and prepare an embedding dataset before proceeding with data loading.

## Loading Data

Use the unified data loader to load your embedding dataset into the system. This loader provides a consistent interface across all pipeline types and supports multiple data formats.

### Load Embedding Training Dataset

#### Embedding Dataset (Retrieval & Similarity)

```bash
# Place your file in the data/ directory for easy access
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --file embedding_data.jsonl  # Use your prepared dataset from the notebook
```

### Data Loader Options

#### Training Data

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `--type embedding` | Specifies embedding training data type | ✅ Yes | - |
| `--workload-id` | Unique identifier for the workload | ✅ Yes | - |
| `--client-id` | Unique identifier for the client | ✅ Yes | - |
| `--file` | Path to your data file (JSONL/JSON/CSV) | ✅ Yes | - |
| `--dataset-type` | Format: `triplet`, `bug_pairs`, or `bug_pairs_separated` | Optional | `triplet` |
| `--mappings-file` | Mappings file for `bug_pairs_separated` format | Conditional* | - |
| `--limit` | Limit number of records to load | Optional | Unlimited |
| `--chunk-size` | Bulk indexing chunk size | Optional | `1000` |

> **Note:** *`--mappings-file` is required when using `--dataset-type bug_pairs_separated`

#### Dataset Type Examples

```bash
# Triplet format (default) - JSONL/JSON with query, pos_doc, neg_doc
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --file triplet_data.jsonl

# Bug pairs format - Single CSV with bugid, similar_bug, bugdata, similarbug_data
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --dataset-type bug_pairs \
  --file bug_pairs.csv

# Bug pairs separated - Two files: corpus and mappings
uv run python src/scripts/load_data.py --type embedding \
  --workload-id embedding-demo \
  --client-id demo-client \
  --dataset-type bug_pairs_separated \
  --file Core_bugs.csv \
  --mappings-file Core_bugs-combined.csv \
  --chunk-size 5000
```

> **📥 Download Mozilla Core Bugs Dataset:**
>
> The example above uses the Mozilla Core Bugs dataset. To download it:
>
> ```bash
> # Navigate to data directory
> cd data/
> 
> # Download mappings file from GitHub
> curl -O https://raw.githubusercontent.com/av9ash/gitbugs/refs/heads/main/mozillacore/Core_bugs-combined.csv
> 
> # Download corpus from Google Drive (requires gdown)
> pip install gdown
> gdown 1zdjfEIDkdsFdCkClo4Tw80Ihdk6DAi6j -O Core_bugs.csv.zip
> unzip Core_bugs.csv.zip
> 
> # Files are now ready in data/ directory
> ```
>
> For detailed data preparation steps, refer to [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb).

### Load Embedding Evaluation Dataset

#### Embedding Evaluation Dataset (BEIR Format)

For custom evaluation datasets in BEIR format, use the dedicated evaluation data loader:

```bash
uv run python src/scripts/load_embedding_eval_data.py \
  --workload-id val-set-1 \
  --client-id embedding-demo \
  --data-dir custom-eval-dataset
```

#### Evaluation Data Parameters

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `--workload-id` | Unique identifier for evaluation set | ✅ Yes | - |
| `--client-id` | Unique identifier for the client | ✅ Yes | - |
| `--data-dir` | Directory containing BEIR format data | ✅ Yes | - |

> **💡 Data Directory Location:** The `--data-dir` parameter accepts relative paths (resolved from `data/` directory) or absolute paths.
> **Note:** Custom evaluations are currently supported only for embedding models. LLM custom evaluation support is planned for a future release.

#### BEIR Format Requirements

- `corpus.jsonl`: Document collection with `_id` and `text` fields
- `queries.jsonl`: Query set with `_id` and `text` fields  
- `qrels/test.tsv`: Relevance judgments mapping query IDs to document IDs

### Getting Help

#### For Training Data

View all available options and examples:

```bash
# See usage examples for all data types
uv run python src/scripts/load_data.py --examples

# View help
uv run python src/scripts/load_data.py --help
```

#### For Evaluation Data

```bash
# View help for BEIR format evaluation data
uv run python src/scripts/load_embedding_eval_data.py --help
```

> **📖 For complete embedding training guide:** Refer to [Embedding Training Guide](../../14-embedding-training.md)

## Running Jobs

### Start Embedding Job

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "embedding-demo", "client_id": "demo-client", "job_type": "embedding_job"}'
```

### Using a Dynamic Configuration

Instead of using the default configuration from `config.yaml`, you can create and use custom configurations dynamically through the API. This allows you to manage different configurations for different embedding use cases without modifying the config file.

<details>
<summary><strong>Step 1: Create a Custom Embedding Configuration</strong></summary>

**Using the example file provided:**

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d @config/embedding_config_example.json
```

> **💡 Tip:** Review and customize [`config/embedding_config_example.json`](../../../config/embedding_config_example.json) before creating the configuration. This file includes all available embedding training options including custom evaluation datasets. Ensure the `"name"` field in the JSON matches the `config_name` you'll use when creating a job.

<details>
<summary><strong>Or create inline with full configuration</strong></summary>

```bash
curl -X POST http://localhost:8000/api/configs \
-H "Content-Type: application/json" \
-d '{
  "name": "embedding_training_config",
  "description": "Example embedding model training configuration for the Data Flywheel Blueprint",
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
          "gpus": 1,
          "max_seq_length": 8192
        }
      }
    ],
    "data_split_config": {
      "val_ratio": 0.09,
      "eval_size": 100,
      "min_total_records": 50,
      "random_seed": 42
    },
    "training_config": {
      "training_type": "sft",
      "finetuning_type": "lora_merged",
      "epochs": 1,
      "batch_size": 64,
      "learning_rate": 0.000025,
      "min_learning_rate": 0.0000005,
      "dataset_parameters": {
        "in_batch_negatives": true,
        "num_hard_negatives": 1
      },
      "micro_batch_size": 1,
      "weight_decay": 0.01,
      "warmup_steps": 100,
      "tensor_parallel_size": 1,
      "pipeline_parallel_size": 1,
      "data_parallel_size": 1,
      "use_sequence_parallel": false,
      "training_precision": "bf16-mixed",
      "lora": {
        "adapter_dim": 32,
        "alpha": 64,
        "adapter_dropout": 0.01
      }
    },
    "evaluation_config": {
      "embedding_eval_metrics": {
        "metrics": [
          "recall_5",
          "map",
          "P_5",
          "P_10"
        ]
      },
      "custom_evaluations": [
        {
          "name": "custom-eval-dataset",
          "workload_id": "val-set-1",
          "client_id": "mozillacorebugs"
        }
      ]
    }
  }
}'
```

</details>

</details>

<details>
<summary><strong>Step 2: Use the Configuration When Creating a Job</strong></summary>

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "embedding-demo", "client_id": "demo-client", "job_type": "embedding_job", "config_name": "embedding_training_config"}'
```

> **Note:** The example configuration above includes `custom_evaluations` which requires loading custom evaluation data beforehand. Refer to [Custom Evaluations](../../02-configuration.md#custom-evaluations) for details on configuring and loading custom evaluation datasets.

</details>

> **📖 For complete configuration management:** Refer to:
> - [Configuration File Structure](../../02-configuration.md#configuration-file-structure) - Understanding system vs job-level configurations
> - [Embedding Job Configuration](../../02-configuration.md#embedding-job-configuration) - All available embedding job settings
> - [Configuration Management System](../../02-configuration.md#configuration-management-system) - Full API documentation for managing configurations

**Save the job ID for subsequent API requests:**

```bash
# Copy the 'id' from the response above and export it
export job_id="your-job-id-here"
```

### Check Job Status

```bash
curl -X GET http://localhost:8000/api/jobs/${job_id} -H "Content-Type: application/json"
```

### Cancel Running Job

If you need to stop an embedding job that is currently running, you can cancel it:

```bash
curl -X POST http://localhost:8000/api/jobs/${job_id}/cancel
```

> **Note:** Only jobs that have not finished can be cancelled. Cancelled jobs will stop all active tasks and mark the job status as `cancelled`.

### Delete Job

After an embedding job is complete or cancelled, you can delete it to clean up resources:

```bash
curl -X DELETE http://localhost:8000/api/jobs/${job_id}
```

> **⚠️ Important:** Running jobs must be cancelled first before they can be deleted. The delete operation removes the job and all associated resources, including customized embedding models, evaluation jobs, datasets from NMP, MLflow experiments (if enabled), and all database records.

> **📖 For complete API documentation:** Refer to [API Reference](../../06-api-reference.md)

## Evaluation

Embedding models are evaluated using standard information retrieval metrics:

- **MAP** (Mean Average Precision): Measures the quality of the entire ranking
- **Recall@k**: Fraction of relevant documents retrieved in the top k results
- **Precision@k**: Fraction of top k results that are relevant
- **NDCG_CUT@k**: Normalized Discounted Cumulative Gain with position-based weighting

The evaluation compares:

- **Base models**: Pre-trained embedding models evaluated on your data
- **Customized models**: Fine-tuned models after training
- **Custom dataset evaluation**: Tests on domain-specific BEIR format datasets (if configured)

> **📖 For detailed evaluation information:** Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

<details>
<summary>🔬 Training Process</summary>

## Training Process

The embedding pipeline uses contrastive learning:

1. **Triplet Training**: Models learn from query-positive-negative triplets
2. **Contrastive Loss**: Maximizes similarity between queries and positive documents while minimizing similarity with negatives
3. **Hard Negative Mining**: Automatically selects challenging negative examples
4. **Fine-tuning Methods**: Supports both SFT (Supervised Fine-Tuning) and LoRA (Low-Rank Adaptation)

> **📖 Iterative Training:** Want to continue training on an already customized model? Refer to [Iterative Fine-tuning](../../14-embedding-training.md#3-iterative-fine-tuning-training-on-customized-models) in the Embedding Training Guide.

</details>

<details>
<summary>📓 Using Notebooks (Alternative Method)</summary>

## Using Notebooks

1. Launch Jupyter Lab using uv:

   ```bash
   uv run jupyter lab \
     --allow-root \
     --ip=0.0.0.0 \
     --NotebookApp.token='' \
     --port=8889 \
     --no-browser
   ```

2. Access Jupyter Lab in your browser at `http://<your-host-ip>:8889`.
3. Navigate to the `notebooks` directory.
4. Open the embedding example notebook: [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb)

Follow the instructions in the Jupyter Lab notebook to interact with the Data Flywheel services for embedding training.

</details>

<details>
<summary>💡 Best Practices</summary>

## Best Practices

- **Data Quality**: Ensure positive documents are truly relevant to queries
- **Negative Selection**: Include diverse, challenging negative examples
- **Data Volume**: More training data generally leads to better improvements
- **Evaluation**: Use custom evaluation datasets that match your production use case

</details>

## Next Steps

- Ready for advanced embedding configuration? Refer to [Embedding Training Guide](../../14-embedding-training.md)
- Ready to evaluate results? Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)
- Need help with configuration? Refer to [Configuration Guide](../../02-configuration.md)
