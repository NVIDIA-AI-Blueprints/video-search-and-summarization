# Overview

This container provides the main API and orchestration service for the NVIDIA Data Flywheel Foundational Blueprint. It acts as the control plane, coordinating the end-to-end flywheel workflow for continuous improvement of fine-tuned AI models.

## About the Data Flywheel Foundational Blueprint

The Data Flywheel Foundational Blueprint provides a complete reference implementation for a flywheel service that continuously improves fine-tuned AI models by identifying and promoting more efficient candidates.

Leveraging the [NVIDIA NeMo Platform](https://docs.nvidia.com/nemo/microservices/26.03.0/index.html) (NMP) 26.03, this blueprint enables developers to quickly bootstrap a control-plane service and automate end-to-end workflows—utilizing components such as NeMo Customizer and NeMo Evaluator—to discover and promote efficient model variants using real logging and tool-calling data.

## What the Container Does

- **API Service:** Hosts a FastAPI-based REST API (on `/api`) for managing flywheel runs, datasets, and model evaluation workflows.
- **Workflow Orchestration:** Runs Celery workers to execute distributed, multi-stage workflows for data collection, dataset creation, model deployment, evaluation, and promotion.
- **Integration:** Connects to MongoDB (for metadata), Elasticsearch (for logging and data), and Redis (for task queueing).
- **Model Management:** Automates the process of spinning up, evaluating, and managing NeMo Inference Microservices (NIMs) and related model artifacts.
- **Data Handling:** Handles ingestion, deduplication, splitting, and uploading of datasets for training, validation, and evaluation.
- **Monitoring:** Optionally runs Flower for Celery task monitoring and can be extended with Kibana for log/data visualization.

# Links

- [Source Code](../README.md)
- [Documentation](../docs/get-started/index.md)
- [Readme](../README.md)
- [Notebooks](../notebooks)

# Ethical Considerations

NVIDIA believes Trustworthy AI is a shared responsibility, and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their supporting model team to ensure the models meet requirements for the relevant industry and use case and address unforeseen product misuse. For more detailed information on ethical considerations for the models, please see the Model Card++ Explainability, Bias, Safety & Security, and Privacy Subcards. Please report security vulnerabilities or NVIDIA AI concerns here.

# License

Use of the models in this blueprint is governed by the [NVIDIA AI Foundation Models Community License](https://docs.nvidia.com/ai-foundation-models-community-license.pdf)

# Governing Terms

The software and materials are governed by the NVIDIA Software License Agreement (found at https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/) and the Product-Specific Terms for NVIDIA AI Products (found at https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-ai-products/), except that models are governed by the AI Foundation Models Community License Agreement (found at NVIDIA Agreements | Enterprise Software | NVIDIA Community Model License) and the NVIDIA RAG dataset is governed by the NVIDIA Asset License Agreement (found at https://github.com/NVIDIA-AI-Blueprints/rag/blob/main/data/LICENSE.DATA). ADDITIONAL INFORMATION: for Meta/llama-3.1-70b-instruct model the Llama 3.1 Community License Agreement, for nvidia/llama-3.2-nv-embedqa-1b-v2 model the Llama 3.2 Community License Agreement, and for nvidia/llama-3.2-nv-embedqa-1b-v2 model the Llama 3.2 Community License Agreement. Built with Llama.
