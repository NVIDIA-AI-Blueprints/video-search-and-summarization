# Completion Use Case

Learn how to improve language models for standard text generation and conversation tasks using the LLM pipeline.

## Overview

The completion use case focuses on improving language models for:
- Chat and conversation applications
- Question answering
- Content generation
- General text completion

The pipeline evaluates multiple candidate models, fine-tunes the best performers, and compares results to help you select the optimal model for your use case.

> **💡 Pro Tip:** To get the best results from this pipeline, use your own production or domain-specific dataset. The quality and relevance of your training data directly impacts model performance. Generic examples shown in this guide are for illustration only—replace them with real data from your actual use case.

## Data Format

> **⚠️ Important:** Prepare your own dataset for completion tasks. Use real conversations, queries, and responses from your application or domain. The examples below demonstrate the required format—adapt them with your actual data.

For standard completion tasks, use JSONL format with a messages array. Each line in your dataset file should contain a JSON object with a `messages` array.

### Required Fields

Each record must contain:

| Field | Type | Description |
|-------|------|-------------|
| `messages` | Array | Array of message objects with `role` and `content` fields |

### Message Roles

Supported message roles:
- `system`: System instructions or context (optional, typically first message)
- `user`: User query or input
- `assistant`: Model response or completion

### Format Examples

> **📝 Note:** The examples below use generic content for illustration. For production use, replace these with actual conversations from your application, customer support logs, domain-specific Q&A, or other relevant sources.

**Simple Single-Turn Conversation:**

```json
{"messages": [
  {"role": "user", "content": "What is machine learning?"},
  {"role": "assistant", "content": "Machine learning is a subset of artificial intelligence that enables systems to learn from data without being explicitly programmed."}
]}
```

**With System Message:**

```json
{"messages": [
  {"role": "system", "content": "You are a helpful AI assistant."},
  {"role": "user", "content": "Explain quantum computing in simple terms."},
  {"role": "assistant", "content": "Quantum computing uses quantum mechanical phenomena like superposition and entanglement to perform computations..."}
]}
```

**Multi-Turn Conversation:**

```json
{"messages": [
  {"role": "user", "content": "What is Python?"},
  {"role": "assistant", "content": "Python is a high-level programming language known for its simplicity and readability."},
  {"role": "user", "content": "What are its main use cases?"},
  {"role": "assistant", "content": "Python is widely used for web development, data science, machine learning, automation, and scientific computing."}
]}
```

> **Note:** The `workload_id` and `client_id` fields are optional in the dataset file. If not provided, they can be specified when loading data with the script or when running a job.

### Creating Your Dataset

To maximize the effectiveness of the pipeline:

1. **Collect Real Data**: Gather actual user interactions, queries, and ideal responses from your application
2. **Ensure Quality**: Review and filter your data to include only high-quality examples
3. **Maintain Consistency**: Use consistent formatting and terminology throughout your dataset
4. **Include Variety**: Cover different scenarios, question types, and edge cases relevant to your use case
5. **Minimum Size**: Aim for at least 500 examples for meaningful evaluation and fine-tuning

**Example data sources:**
- Production chat logs (with PII removed)
- Customer support conversations
- Curated Q&A pairs from your domain
- Expert-generated examples for your specific use case

## Loading Data

> **🎯 Action Required:** Before loading data, prepare your own JSONL file with your domain-specific completion examples. Replace `your_completion_data.jsonl` with the path to your actual dataset file.

Use the unified data loader to load your completion dataset into the system. This loader provides a consistent interface across all pipeline types.

### Load Completion Dataset

**Standard Completion Dataset**

```bash
# Place your file in the data/ directory for easy access
uv run python src/scripts/load_data.py --type llm \
  --workload-id your_workload_id \
  --client-id your_client_id \
  --file your_completion_data.jsonl
```

### Data Loader Options

| Parameter | Description | Required |
|-----------|-------------|----------|
| `--type llm` | Specifies LLM/completion data type | ✅ Yes |
| `--workload-id` | Unique identifier for the workload | ⭐ Recommended |
| `--client-id` | Unique identifier for the client | ⭐ Recommended |
| `--file` | Path to your JSONL data file | ✅ Yes |

> **💡 Data File Location:** The `--file` parameter accepts:
> - **Relative paths**: Resolved from the `data/` directory (e.g., `--file your_data.jsonl` → looks for `data/your_data.jsonl`)
> - **Absolute paths**: Used exactly as provided (e.g., `--file /path/to/your_data.jsonl`)

> **⭐ About workload-id and client-id:**
> 
> These parameters are **technically optional** but **strongly recommended**:
> - **Required for job creation**: Jobs use `workload_id` to query and organize your data
> - **Optional only when**: Your JSONL file already contains `workload_id` and `client_id` fields (CLI args will override them)
> - **Best practice**: Always provide these values to ensure proper data organization and tracking
> 
> **When omitted**: If your data file doesn't have these fields and you don't provide CLI arguments, records will have empty identifiers, making them difficult to query and organize.

### Getting Help

View all available options and examples:

```bash
# See usage examples for all data types
uv run python src/scripts/load_data.py --examples

# View help
uv run python src/scripts/load_data.py --help
```

> **📖 For complete data loading guide:** Refer to [Data Logging for AI Apps](../../data-logging.md)

## Running Jobs

Once you've loaded your own dataset, you can start the pipeline to evaluate and fine-tune models.

### Start Job

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "your_workload_id", "client_id": "your_client_id"}'
```

> **💡 Tip:** Use meaningful `workload_id` and `client_id` values that reflect your actual use case (e.g., `customer_support_bot`, `legal_qa_assistant`). This helps organize results when running multiple experiments.

### Using a Dynamic Configuration

Instead of using the default configuration from `config.yaml`, you can create and use custom configurations dynamically through the API. This allows you to manage different configurations for different use cases without modifying the config file.

<details>
<summary><strong>Step 1: Create a Custom Configuration</strong></summary>

```bash
curl -X POST http://localhost:8000/api/configs \
-H "Content-Type: application/json" \
-d '{
  "name": "completion-custom-config",
  "description": "Custom configuration for completion tasks",
  "config_type": "llm_job",
  "config_data": {
    "nims": [
      {
        "model_name": "meta/llama-3.2-1b-instruct",
        "model_type": "llm",
        "context_length": 8192,
        "gpus": 1,
        "pvc_size": "25Gi",
        "tag": "1.8.3",
        "customization_enabled": true,
        "customizer_configs": {
          "target": "meta/llama-3.2-1b-instruct@2.0",
          "gpus": 1,
          "max_seq_length": 8192
        }
      }
    ],
    "data_split_config": {
      "eval_size": 100,
      "val_ratio": 0.1,
      "min_total_records": 50,
      "random_seed": null,
      "limit": 1000,
      "parse_function_arguments": true
    },
    "icl_config": {
      "max_context_length": 8192,
      "reserved_tokens": 4096,
      "max_examples": 3,
      "min_examples": 1,
      "example_selection": "uniform_distribution"
    },
    "training_config": {
      "training_type": "sft",
      "finetuning_type": "lora",
      "epochs": 2,
      "batch_size": 16,
      "learning_rate": 0.0001,
      "lora": {
        "adapter_dim": 32,
        "adapter_dropout": 0.1
      }
    }
  }
}'
```

</details>

<details>
<summary><strong>Step 2: Use the Configuration When Creating a Job</strong></summary>

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "your_workload_id", "client_id": "your_client_id", "config_name": "completion-custom-config"}'
```

</details>

> **📖 For complete configuration management:** Refer to:
> - [Configuration File Structure](../../02-configuration.md#configuration-file-structure) - Understanding system vs job-level configurations
> - [LLM Job Configuration](../../02-configuration.md#llm-job-configuration) - All available LLM job settings
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

If you need to stop a job that's currently running, you can cancel it:

```bash
curl -X POST http://localhost:8000/api/jobs/${job_id}/cancel
```

> **Note:** Only jobs that have not finished can be cancelled. Cancelled jobs will stop all active tasks and mark the job status as `cancelled`.

### Delete Job

After a job is complete or cancelled, you can delete it to clean up resources:

```bash
curl -X DELETE http://localhost:8000/api/jobs/${job_id}
```

> **⚠️ Important:** Running jobs must be cancelled first before they can be deleted. The delete operation removes the job and all associated resources, including customized models, evaluation jobs, datasets from NMP, MLflow experiments (if enabled), and all database records.

> **📖 For complete API documentation:** Refer to [API Reference](../../06-api-reference.md)

## Evaluation

Completion tasks are evaluated using:

- **Similarity Score** (1-10): LLM-judged similarity between candidate and reference answers. Higher is better.

The evaluation compares:
- **Base models**: Pre-trained models evaluated on your data
- **Customized models**: Fine-tuned models after training
- **In-context learning**: Performance with example-based prompting

> **📖 For detailed evaluation information:** Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

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
4. Browse the available example notebooks to see different use cases and workflows.

> **Note:** For completion-specific examples, adapt the data format shown in this guide. The available notebooks demonstrate tool calling, DPO, and embedding workflows that you can reference for API usage patterns.

</details>

## Next Steps

- Need function calling? Refer to [Tool Calling](tool-calling.md)
- Want preference optimization? Refer to [Preference Optimization (DPO)](dpo.md)
- Ready to evaluate results? Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

