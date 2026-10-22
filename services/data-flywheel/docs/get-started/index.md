# Get Started With Data Flywheel Blueprint

The Data Flywheel Blueprint helps you continuously improve your AI models by automatically evaluating, fine-tuning, and deploying better versions based on production data. Choose your path based on your use case.

## Choose Your Pipeline

The Data Flywheel Blueprint supports two major pipeline types:

| LLM Pipeline | Embedding Pipeline |
|--------------|-------------------|
| Use the LLM pipeline to improve language models for: | Use the embedding pipeline to improve retrieval and similarity models for: |
| • **Completion tasks**: Standard text generation and conversation<br>• **Tool calling**: Function calling and structured outputs<br>• **Preference optimization**: Aligning models with human preferences using DPO | • **Semantic search**: Finding relevant documents<br>• **RAG applications**: Retrieval-augmented generation<br>• **Similarity matching**: Finding similar content |

## Quick Navigation

| Resource | Description |
|----------|-------------|
| [🚀 Installation and Setup](install-and-setup.md) | Set up your environment, deploy NMP, configure the blueprint, and start services. |
| [💬 LLM Pipeline](llm-pipeline/index.md) | Improve language models for completion, tool calling, and preference optimization. |
| [🔍 Embedding Pipeline](embedding-pipeline/index.md) | Improve embedding models for semantic search and retrieval applications. |
| [🔄 Automatic Retraining Triggers](../15-retraining-triggers.md) | Set up automatic model retraining based on schedules, data thresholds, or performance. |
| [📚 Additional Resources](../README.md) | Browse complete documentation, API reference, and advanced guides. |

<details>
<summary>📋 Prerequisites</summary>

## Prerequisites

Before you begin, ensure you have:

- **Hardware**: Minimum 2× NVIDIA B200, H100, or A100 GPUs (6× for self-hosted LLM judge)
- **Software**: Python 3.11, Docker Engine, Docker Compose v2
- **Access**: NGC API key and NVIDIA API key
- **Cluster**: Single-node NVIDIA GPU cluster with cluster-admin permissions
- **Skills**: Basic command-line familiarity; Kubernetes/Helm knowledge required for manual deployment

> **📖 For complete system requirements:** Refer to [System Requirements](../02-configuration.md#system-requirements)

</details>

## Getting Started Workflow

1. **Install and Set Up**: Deploy NMP, configure the blueprint, and start services
2. **Choose Your Pipeline**: Select LLM or Embedding based on your use case
3. **Load Your Data**: Prepare and load your training data
4. **Run Jobs**: Start evaluation and fine-tuning jobs
5. **Evaluate Results**: Review metrics and improvements

## Next Steps

- New to the blueprint? Start with [Installation and Setup](install-and-setup.md)
- Working with language models? Go to [LLM Pipeline](llm-pipeline/index.md)
- Working with embeddings? Go to [Embedding Pipeline](embedding-pipeline/index.md)
