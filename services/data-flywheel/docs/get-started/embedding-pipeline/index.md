# Embedding Pipeline

The embedding pipeline helps you continuously improve embedding models for semantic search, retrieval-augmented generation (RAG), and similarity-based applications.

## Overview

The embedding pipeline evaluates and fine-tunes embedding models using your domain-specific data. It supports:

- **Semantic Search**: Finding relevant documents based on query meaning
- **RAG Applications**: Retrieval-augmented generation workflows
- **Similarity Matching**: Finding similar content across your corpus
- **Baseline Model Improvement**: Fine-tuning pre-trained models for your domain

## How It Works

1. **Load Data**: Provide query-positive-negative triplets from your domain
2. **Evaluate Base Models**: Test pre-trained embedding models on your data
3. **Fine-Tune**: Automatically fine-tune models using contrastive learning
4. **Evaluate Customized Models**: Compare fine-tuned models against baselines
5. **Deploy**: Use the improved models for better retrieval accuracy

## Use Cases

- **[📈 Improving Baseline Model Accuracy](improving-accuracy.md)** - Fine-tune pre-trained embedding models to improve accuracy on your domain-specific data.
- **[🔄 Iterative Fine-tuning](../../14-embedding-training.md#3-iterative-fine-tuning-training-on-customized-models)** - Continue training on top of already customized embedding models.

## Prerequisites

Before starting with the embedding pipeline:

- ✅ [Installation and Setup](../install-and-setup.md) completed
- ✅ NMP deployed and running
- ✅ Data Flywheel services started
- ✅ Embedding dataset downloaded and prepared using [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb) notebook
- ✅ Embedding training data prepared in [Supported data format](../../14-embedding-training.md#input-file-formats)

## Quick Start

1. **Load Your Data**:

   This step loads your training data into the system. The details below are for the dataset generated in the [Triplet Format](../../14-embedding-training.md#triplet-format) using the [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb) notebook. For other data formats, refer to the [Supported data format](../../14-embedding-training.md#input-file-formats) guide.

   **Example Dataset (Core Bugs)**

   This format uses two files: a corpus file (CSV) and a mappings file (CSV).

   > **Note**: If you want to use the core-bugs dataset, follow the data curation steps in [embedding-example-launchable.ipynb](../../../notebooks/embedding-example-launchable.ipynb) before using the commands.

   **Corpus File (for example, `Core_bugs.csv`)**
   Contains the text data. Required columns:
   - `bugid`: Unique identifier for the document
   - Other columns are combined to form the text content.

   **Mappings File (for example, `Core_bugs-combined.csv`)**
   Contains the relationships between queries and positive documents. Required columns:
   - `bugid`: The query ID
   - `Duplicates`: A comma-separated string of positive document IDs (for example, "123,456")

   **Usage with `--dataset-type bug_pairs_separated`**:

   ```bash
   # Note: Files can be in data/ directory or use explicit paths
   uv run python src/scripts/load_data.py --type embedding \
     --workload-id embedding-demo \
     --client-id demo-client  \
     --dataset-type bug_pairs_separated \
     --file Core_bugs.csv \
     --chunk-size 5000 \
     --mappings-file Core_bugs-combined.csv
   ```

   > **💡 Path Handling:** You can use `--file Core_bugs.csv` (automatically looks in `data/`) or absolute paths like `--file path/to/Core_bugs.csv`.

   **Other Supported Formats**

   For other supported formats, refer to the [Supported data format](../../14-embedding-training.md#input-file-formats) guide. Use the following command:

   ```bash
   uv run python src/scripts/load_data.py --type embedding \
     --workload-id embedding-demo \
     --client-id demo-client \
     --file your_data.csv
   ```

2. **Start a Job**:

   ```bash
   curl -X POST http://localhost:8000/api/jobs \
     -H "Content-Type: application/json" \
     -d '{"workload_id": "embedding-demo", "client_id": "demo-client", "job_type": "embedding_job"}'
   ```

   > **💡 Using a Custom Configuration?** See [Dynamic Configuration](improving-accuracy.md#running-jobs) to create and use custom embedding configurations via the API.

   **Save the job ID for subsequent API requests:**

   ```bash
   # Copy the 'id' from the response above and export it
   export job_id="your-job-id-here"
   ```

3. **Monitor Progress**:

   ```bash
   curl -X GET http://localhost:8000/api/jobs/${job_id}
   ```

4. **Review Results**: Check evaluation metrics (MAP, Recall@k, Precision@k, NDCG) in the job status response to compare base and customized models.

## Data Format

The embedding pipeline uses triplet format where each record contains:

- **Query**: The search query or question
- **Positive Document**: A relevant/correct document for the query
- **Negative Documents**: Irrelevant documents (1-5 recommended)

This format enables contrastive learning where the model learns to:

1. Maximize similarity between query and positive document
2. Minimize similarity between query and negative documents

> **📖 For complete data format details:** Refer to [Embedding Training Guide](../../14-embedding-training.md#data-format)

<details>
<summary>📊 Evaluation Metrics</summary>

## Evaluation Metrics

The embedding pipeline evaluates models using standard information retrieval metrics:

- **MAP** (Mean Average Precision): Measures the quality of the entire ranking
- **Recall@k**: Fraction of relevant documents retrieved in the top k results
- **Precision@k**: Fraction of top k results that are relevant
- **NDCG_CUT@k**: Normalized Discounted Cumulative Gain with position-based weighting

> **📖 For detailed evaluation information:** Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

</details>

## Related Documentation

- [Embedding Training Guide](../../14-embedding-training.md) - Comprehensive embedding guide
- [Configuration Guide](../../02-configuration.md) - Model and training configuration
- [API Reference](../../06-api-reference.md) - API endpoints and parameters

## Next Steps

After your embedding customization job completes:

- 📊 **Review evaluation metrics** to assess model performance
- 🚀 **Deploy your fine-tuned model** using [embedding-finetunned-model-deployment.ipynb](../../../notebooks/embedding-finetunned-model-deployment.ipynb)
- 🧪 **Test inference** to verify improved retrieval accuracy
- 🔄 **Iterate on training data** based on evaluation results
