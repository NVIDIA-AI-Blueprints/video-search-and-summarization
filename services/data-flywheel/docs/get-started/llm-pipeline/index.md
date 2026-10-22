# LLM Pipeline

The LLM pipeline helps you continuously improve language models for text generation, tool calling, and preference alignment. Choose your use case to get started.

## Overview

The LLM pipeline evaluates and fine-tunes language models using your production data. It supports three main use cases:

- **Completion**: Standard text generation and conversation tasks
- **Tool Calling**: Function calling and structured outputs
- **Preference Optimization**: Aligning models with human preferences using DPO

## How It Works

1. **Load Data**: Provide your production logs or training data
2. **Evaluate Base Models**: Test multiple candidate models on your data
3. **Fine-Tune**: Fine-tune configured models using your training data
4. **Evaluate Customized Models**: Compare fine-tuned models against baselines
5. **Deploy**: Deploy improved models using NMP deployment services

## Use Cases

| Use Case | Description | Best For |
|----------|-------------|----------|
| [💬 Completion](completion.md) | Standard text generation and conversation tasks | Chat, Q&A, content generation |
| [🔧 Tool Calling](tool-calling.md) | Function calling and structured outputs | API calls, RAG, structured data extraction |
| [⭐ Preference Optimization (DPO)](dpo.md) | Align models with human preferences using preference pairs | Response quality improvement, alignment |

## Prerequisites

Before starting with the LLM pipeline:

- ✅ [Installation and Setup](../install-and-setup.md) completed
- ✅ NMP deployed and running
- ✅ Data Flywheel services started
- ✅ Training data prepared in the correct format

## Quick Start

Choose your use case above, then follow these steps:

1. **Load Your Data**:

   ```bash
   # Place your JSONL file in the data/ directory
   uv run python src/scripts/load_data.py --type llm \
     --workload-id your-workload \
     --client-id your-client \
     --file your_data.jsonl
   ```

   > **💡 Data File Location:** Place your data files in the `data/` directory, or provide an absolute path.

   Refer to the [Completion](completion.md#loading-data), [Tool Calling](tool-calling.md#loading-data), or [DPO](dpo.md#loading-data) guides for format-specific instructions.

2. **Start a Job**:

   ```bash
   curl -X POST http://localhost:8000/api/jobs \
     -H "Content-Type: application/json" \
     -d '{"workload_id": "your-workload", "client_id": "your-client"}'
   ```

   **Save the job ID for subsequent API requests:**

   ```bash
   # Copy the 'id' from the response above and export it
   export job_id="your-job-id-here"
   ```

3. **Monitor Progress**:

   ```bash
   curl -X GET http://localhost:8000/api/jobs/${job_id}
   ```

4. **Review Results**: Check evaluation metrics in the job status response to compare base and customized models.

## Data Format

The LLM pipeline supports multiple data formats depending on your use case:

- **Standard Completion**: JSONL with messages array
- **Tool Calling**: JSONL with request/response including tool definitions
- **DPO**: JSONL with prompt, chosen_response, and rejected_response

See the specific use case pages for detailed data format requirements.

<details>
<summary>📊 Evaluation Metrics</summary>

## Evaluation Metrics

The LLM pipeline evaluates models using:

- **Similarity Score** (1-10): For completion tasks, measures how similar model outputs are to reference answers
- **Tool Calling Accuracy**: For tool calling tasks, measures function name and argument accuracy
- **Preference Score** (1-10): For DPO tasks, measures alignment with preferred responses

> **📖 For detailed evaluation information:** Refer to [Evaluation Types and Metrics](../../05-evaluation-types-and-metrics.md)

</details>

## Related Documentation

- [DPO Training Guide](../../13-dpo-training.md) - Comprehensive DPO guide
- [Configuration Guide](../../02-configuration.md) - Model and training configuration
- [API Reference](../../06-api-reference.md) - API endpoints and parameters
- [Production Deployment](../../09-production-deployment.md) - Deploy models in production environments
