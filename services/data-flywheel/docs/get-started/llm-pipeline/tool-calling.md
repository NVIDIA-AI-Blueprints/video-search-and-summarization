# Tool Calling Use Case

Learn how to improve language models for function calling and structured outputs using the LLM pipeline.

## Overview

The tool calling use case focuses on improving language models for:
- Function calling and API integration
- Structured data extraction
- Retrieval-augmented generation (RAG) with tool use
- Multi-step workflows requiring tool invocations

The pipeline evaluates models on their ability to correctly identify which functions to call and with what arguments, then fine-tunes models to improve accuracy.

## Data Format

For tool calling tasks, use JSONL format with request/response including tool definitions:

```json
{
  "request": {
    "model": "meta/llama-3.1-70b-instruct", 
    "messages": [
      {"role": "system", "content": "You are a chatbot that helps with purchase history."},
      {"role": "user", "content": "Has my bill been processed?"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "structured_rag",
          "parameters": {
            "properties": {
              "query": {"type": "string"},
              "user_id": {"type": "string"}
            },
            "required": ["query", "user_id"]
          }
        }
      }
    ]
  },
  "response": {
    "choices": [{
      "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
          "type": "function",
          "function": {
            "name": "structured_rag",
            "arguments": {"query": "account bill processed", "user_id": "4165"}
          }
        }]
      }
    }]
  },
  "workload_id": "primary_assistant",
  "client_id": "aiva-1",
  "timestamp": 1746138417
}
```

Each line in your dataset file should follow this structure, which is compatible with the OpenAI API request and response format.

## Loading Data

Use the unified data loader to load your tool calling dataset into the system. This loader provides a consistent interface across all pipeline types.

### Load Tool Calling Dataset

**AIVA Dataset**

```bash
uv run python src/scripts/load_data.py --type llm \
    --workload-id primary_assistant \
    --client-id aiva-1 \
    --file aiva_primary_assistant_dataset.jsonl
```

### Data Loader Options

| Parameter | Description | Required |
|-----------|-------------|----------|
| `--type llm` | Specifies LLM/tool calling data type | ✅ Yes |
| `--workload-id` | Unique identifier for the workload | ⭐ Recommended |
| `--client-id` | Unique identifier for the client | ⭐ Recommended |
| `--file` | Path to your JSONL data file | ✅ Yes |

> **💡 Data File Location:** The `--file` parameter accepts:
> - **Relative paths**: Resolved from the `data/` directory (e.g., `--file aiva_dataset.jsonl` → looks for `data/aiva_dataset.jsonl`)
> - **Absolute paths**: Used exactly as provided (e.g., `--file /path/to/aiva_dataset.jsonl`)

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

### Start Job

```bash
curl -X POST http://localhost:8000/api/jobs \
-H "Content-Type: application/json" \
-d '{"workload_id": "primary_assistant", "client_id": "aiva-1"}'
```

### Using a Dynamic Configuration

Instead of using the default configuration from `config.yaml`, you can create and use custom configurations dynamically through the API. This allows you to manage different configurations for different use cases without modifying the config file.

<details>
<summary><strong>Step 1: Create a Custom Configuration</strong></summary>

```bash
curl -X POST http://localhost:8000/api/configs \
-H "Content-Type: application/json" \
-d '{
  "name": "tool-calling-custom-config",
  "description": "Custom configuration for tool calling tasks",
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
-d '{"workload_id": "primary_assistant", "client_id": "aiva-1", "config_name": "tool-calling-custom-config"}'
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

If you need to stop a tool calling job that's currently running, you can cancel it:

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

Tool calling tasks are evaluated using:

- **Function Name Accuracy** (0-1): Accuracy of function name prediction
- **Function Name and Arguments Accuracy** (0-1): Accuracy of both function name and arguments
- **Tool Calling Correctness** (Integer 0 or 1): LLM-judged correctness of tool call output (1=correct, 0=incorrect)

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
4. Open the example notebook: [data-flywheel-bp-tutorial.ipynb](../../../notebooks/data-flywheel-bp-tutorial.ipynb)

Follow the instructions in the Jupyter Lab notebook to interact with the Data Flywheel services.

</details>

## Next Steps

- Need standard completion? Refer to [Completion](completion.md)
- Want preference optimization? Refer to [Preference Optimization (DPO)](dpo.md)
- Ready to evaluate results? Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

