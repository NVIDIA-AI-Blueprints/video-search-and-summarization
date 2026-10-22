# Preference Optimization Using DPO

Learn how to align language models with human preferences using Direct Preference Optimization (DPO) training.

## Overview

DPO (Direct Preference Optimization) is a training method that directly optimizes language models to align with human preferences without requiring a separate reward model. Instead of traditional supervised fine-tuning on single responses, DPO trains models using pairs of responses:
- **Chosen response**: The preferred or better response
- **Rejected response**: The less preferred or worse response

The model learns to increase the likelihood of generating chosen responses while decreasing the likelihood of rejected responses.

<details>
<summary>💡 When to Use DPO</summary>

## When to Use DPO

Use DPO training when you have:

1. **Preference Data**: Pairs of responses where one is clearly better than the other
2. **Alignment Goals**: Need to align model behavior with specific quality standards
3. **Comparative Feedback**: Multiple responses to the same prompt with quality rankings
4. **Human Feedback**: Annotated data from human evaluators comparing response quality

**Common Use Cases:**
- Customer service chatbots (professional compared to unprofessional responses)
- Code generation (correct vs buggy implementations)
- Content moderation (safe vs unsafe responses)
- Creative writing (engaging vs bland text)

</details>

<details>
<summary>📊 DPO vs Standard Fine-Tuning</summary>

## DPO vs Standard Fine-Tuning

| Aspect | Standard SFT | DPO |
|--------|-------------|-----|
| **Data Format** | Single response per prompt | Preference pairs (chosen + rejected) |
| **Training Goal** | Maximize likelihood of target response | Maximize preference gap between responses |
| **Use Case** | Task-specific behavior | Alignment with preferences |
| **Evaluation** | Similarity to expected output (1-5) | Preference alignment (1-10) |
| **Data Requirements** | Labeled examples | Comparative judgments |

</details>

## Data Format

For DPO (Direct Preference Optimization) training, use preference pairs:

```json
{
  "prompt": "What is Python?",
  "chosen_response": "Python is a high-level, interpreted programming language known for its readability, simplicity, and versatility. It supports multiple programming paradigms and has a comprehensive standard library.",
  "rejected_response": "Python is a snake.",
  "workload_id": "coding-assistant",
  "client_id": "company-1"
}
```

Each DPO record must contain:
- `prompt`: String containing the user query (or array of messages for multi-turn conversations)
- `chosen_response`: The preferred/better response
- `rejected_response`: The less preferred/worse response
- `workload_id` and `client_id`: Identifiers (can be provided via script args)

> **📖 For complete DPO data format and examples:** Refer to [DPO Training Guide](../../13-dpo-training.md#dpo-data-format)

> **Note**
> If `workload_id` and `client_id` aren't provided in the dataset entries, you can specify them when running a job or when loading data with the DPO script.

> **Note:** Unlike LLM tool-calling use case, there is no pre-existing DPO dataset in the repository. Users must follow the [dpo-dfw-example.ipynb](../../../notebooks/dpo-dfw-example.ipynb) notebook to download and prepare a DPO dataset from HuggingFace before proceeding with data loading.

## Loading Data

Use the unified data loader to load your DPO preference dataset into the system. This loader provides a consistent interface across all pipeline types.

### Load DPO Dataset

**DPO Dataset (Preference Optimization)**

```bash
# Place your file in the data/ directory for easy access
uv run python src/scripts/load_data.py --type dpo \
  --workload-id dpo-demo \
  --client-id demo-client \
  --file my_dpo_dataset.jsonl  # Use your prepared dataset from the notebook
```

### Data Loader Options

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `--type dpo` | Specifies DPO preference data type | ✅ Yes | - |
| `--workload-id` | Unique identifier for the workload | ✅ Yes | - |
| `--client-id` | Unique identifier for the client | ⭐ Recommended | - |
| `--file` | Path to your JSONL data file | ✅ Yes | - |
| `--limit` | Limit number of records to load | Optional | Unlimited |

> **Note:** The `--workload-id` parameter is **required** and enforced at runtime for DPO data. The CLI `--workload-id` takes precedence and will be applied to **all records**, overriding any per-record `workload_id` values in your data file.

> **⚠️ Important - Precedence Behavior:** The CLI `--workload-id` argument always takes precedence over per-record `workload_id` values. When you provide `--workload-id` via CLI, it applies uniformly to all records, effectively ignoring any `workload_id` fields in individual records. This ensures consistent workload identification across your entire dataset.

> **💡 About --limit:** By default, all records in your file are loaded. Use `--limit` to load only the first N records for testing or when working with large datasets.

> **💡 Data File Location:** The `--file` parameter accepts:
> - **Relative paths**: Resolved from the `data/` directory (e.g., `--file dpo_data.jsonl` → looks for `data/dpo_data.jsonl`)
> - **Absolute paths**: Used exactly as provided (e.g., `--file /path/to/dpo_data.jsonl`)

**Example with limit:**

```bash
# Load only the first 500 records
uv run python src/scripts/load_data.py --type dpo \
  --workload-id dpo-demo \
  --client-id demo-client \
  --file my_dpo_dataset.jsonl \
  --limit 500
```

### Getting Help

View all available options and examples:

```bash
# See usage examples for all data types
uv run python src/scripts/load_data.py --examples

# View help
uv run python src/scripts/load_data.py --help
```

> **📖 For complete DPO training guide:** Refer to [DPO Training Guide](../../13-dpo-training.md)

## Running Jobs

Before running a DPO job, you must configure the training type to use DPO. Choose one of the following options:

### Option 1: Using Default Configuration (config.yaml)

**Step 1: Configure DPO in config.yaml**

Edit `config/config.yaml` and set the `training_type` to `"dpo"` under `llm_job_config.training_config`:

```yaml
llm_job_config:
  training_config:
    training_type: "dpo"  # Set to "dpo" for Direct Preference Optimization
    finetuning_type: "all_weights"
    epochs: 3
    batch_size: 64
    learning_rate: 0.00005
    
    # DPO-specific hyperparameters
    dpo:
      ref_policy_kl_penalty: 0.1
      preference_loss_weight: 1.0
      preference_average_log_probs: false
      sft_loss_weight: 0.0
      sft_average_log_probs: false
```

**Step 2: Start the Job**

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "dpo-demo", "client_id": "demo-client"}'
```

### Option 2: Using Dynamic Configuration (API)

Instead of modifying the config file, you can create and use custom DPO configurations dynamically through the API.

**Step 1: Create a DPO Configuration**

Using the example file provided:

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d @config/dpo_config_example.json
```

> **💡 Tip:** Ensure the `"name"` field in [`config/dpo_config_example.json`](../../../config/dpo_config_example.json) matches the `config_name` you'll use when starting the job.

<details>
<summary><strong>Or create inline with full configuration</strong></summary>

```bash
curl -X POST http://localhost:8000/api/configs \
-H "Content-Type: application/json" \
-d '{
  "name": "dpo_training_config",
  "description": "Custom configuration for DPO preference optimization",
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
          "max_seq_length": 4096
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
      "reserved_tokens": 2048,
      "max_examples": 2,
      "min_examples": 1,
      "example_selection": "uniform_distribution"
    },
    "training_config": {
      "training_type": "dpo",
      "finetuning_type": "all_weights",
      "epochs": 3,
      "batch_size": 64,
      "learning_rate": 0.00005,
      "micro_batch_size": 1,
      "tensor_parallel_size": 1,
      "pipeline_parallel_size": 1,
      "data_parallel_size": 1,
      "training_precision": "bf16-mixed",
      "dpo": {
        "ref_policy_kl_penalty": 0.1,
        "preference_loss_weight": 1.0,
        "preference_average_log_probs": false,
        "sft_loss_weight": 0.0,
        "sft_average_log_probs": false
      }
    }
  }
}'
```

</details>

**Step 2: Start the Job with Your Configuration**

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "dpo-demo", "client_id": "demo-client", "config_name": "dpo_training_config"}'
```

> **📖 For more information:** Refer to:
> - [DPO Training Guide - Data Format](../../13-dpo-training.md#dpo-data-format) - Complete DPO data format and examples
> - [DPO Training Guide - Configuration](../../13-dpo-training.md#configuration) - Detailed DPO configuration options
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

If you need to stop a DPO job that's currently running, you can cancel it:

```bash
curl -X POST http://localhost:8000/api/jobs/${job_id}/cancel
```

> **Note:** Only jobs that have not finished can be cancelled. Cancelled jobs will stop all active tasks and mark the job status as `cancelled`.

### Delete Job

After a DPO job is complete or cancelled, you can delete it to clean up resources:

```bash
curl -X DELETE http://localhost:8000/api/jobs/${job_id}
```

> **⚠️ Important:** Running jobs must be cancelled first before they can be deleted. The delete operation removes the job and all associated resources, including customized models, evaluation jobs, datasets from NMP, MLflow experiments (if enabled), and all database records.

> **📖 For complete API documentation:** Refer to [API Reference](../../06-api-reference.md)

## Evaluation

DPO tasks are evaluated using:

- **Preference Score** (1-10): Measures alignment with preferred responses over rejected ones. The score is calculated by an LLM judge that compares the model's response to both the chosen (preferred) and rejected (less preferred) responses. 10=excellent alignment, 1=poor alignment.

The evaluation compares:
- **Base models**: Pre-trained models evaluated on your preference data
- **Customized models**: DPO fine-tuned models after training
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
4. Open the DPO example notebook: [dpo-dfw-example.ipynb](../../../notebooks/dpo-dfw-example.ipynb)

Follow the instructions in the Jupyter Lab notebook to interact with the Data Flywheel services for DPO training.

</details>

## Next Steps

- Need standard completion? Refer to [Completion](completion.md)
- Need tool calling? Refer to [Tool Calling](tool-calling.md)
- Ready for advanced DPO configuration? Refer to [DPO Training Guide](../../13-dpo-training.md)
- Ready to evaluate results? Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

