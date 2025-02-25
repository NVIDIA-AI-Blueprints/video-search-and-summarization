<h2><img align="center" src="https://github.com/user-attachments/assets/cbe0d62f-c856-4e0b-b3ee-6184b7c4d96f">NVIDIA AI Blueprint: Video Search and Summarization</h2>

### Overview
This repository is what powers the [build experience](https://build.nvidia.com/nvidia/video-search-and-summarization), showcasing video search and summarization agent with NVIDIA NIM microservices.

Insightful, accurate, and interactive video analytics AI agents enable a range of industries to make better decisions faster. These AI agents are given tasks through natural language and can perform complex operations like video summarization and visual question-answering, unlocking entirely new application possibilities. The NVIDIA AI Blueprint makes it easy to get started building and customizing video analytics AI agents for video search and summarization — all powered by generative AI, vision language models (VLMs) like Cosmos Nemotron VLMs, large language models (LLMs) like Llama Nemotron LLMs, NVIDIA NeMo Retriever, and NVIDIA NIM.

### Software Components
<div align="center">
  <img src="deploy/images/vss_architecture.jpg" width="700">
</div>

1. **NIM microservices**: Here are models used in this blueprint:

    - [cosmos-nemotron-34b](https://build.nvidia.com/nvidia/cosmos-nemotron-34b)
    - [meta / llama-3.1-70b-instruct](https://build.nvidia.com/meta/llama-3_1-70b-instruct)
    - [llama-3_2-nv-embedqa-1b-v2](https://build.nvidia.com/nvidia/llama-3_2-nv-embedqa-1b-v2)
    - [llama-3_2-nv-rerankqa-1b-v2](https://build.nvidia.com/nvidia/llama-3_2-nv-rerankqa-1b-v2)

2. **Ingestion Pipeline**: 

    The process involves decoding video segments (chunks) generated by the stream handler, selecting frames, and using a vision-language model (VLM) along with a caption prompt to generate detailed captions for each chunk. These dense captions are then indexed into vector and graph databases for use in the Context-Aware Retrieval-Augmented Generation workflow.

3. **Context Manager**: 

    Efficiently incorporates tools — a vision-language model (VLM) and a large language model (LLM), using them as required. Key functions including a summary generator, an answer generator, and an alert handler. The tools and functions are used in summary generation, handling Q&A, and managing alerts. In addition, context manager effectively maintains its working context by making efficient use of both short-term memory, such as chat history, and long-term memory resources like vector and graph databases, as needed.

4. **CA-RAG module**:

    The Context-Aware Retrieval-Augmented Generation (CA-RAG) module leverages both Vector RAG and Graph-RAG as the primary sources for video understanding. During the Q&A workflow, the CA-RAG module extracts relevant context from the vector database and graph database to enhance temporal reasoning, anomaly detection, multi-hop reasoning, and scalability, thereby offering deeper contextual understanding and efficient management of extensive video data.

<!-- ### Target Audience
Target audience of the blueprint -->

### Prerequisits

#### NVAIE developer license

#### Obtain API Keys

1. Apply for [Early Access Program](https://developer.nvidia.com/ai-blueprint-for-video-search-and-summarization-early-access/join)  for Video Search And Summarization NVIDIA AI Blueprint.
2. Login to [NGC Portal](https://ngc.nvidia.com/) with the same account you applied for early access.
3. Follow the [steps here](https://docs.nvidia.com/ngc/gpu-cloud/ngc-user-guide/index.html#generating-personal-api-key) to obtain an NGC API Key.

This API Key (```NVIDIA_API_KEY```) will be used to pull the blueprint container and other models that will be used as part of the blueprint.

<div align="center">
  <img src="deploy/images/vss_ea_page.png" width="700">
</div>

### Hardware Requirements

The following Nvidia GPUs are supported:
- 8 x H100 (80 GB)
- 8 x A100 (80 GB)
- 8 x L40S (48 GB)

500+ GB system memory (for running all models locally)

### Quickstart Guide

#### Docker Compose Deployment

Follow the notebooks in [deploy](deploy/) directory to complete all pre-requisites and deploy the blurpeint.
- [deploy/1_Deploy_VSS_docker_Crusoe.ipynb](deploy/1_Deploy_VSS_docker_Crusoe.ipynb): This notebook is tailored spacifically for the Crusoe CSP which uses Ephemeral storage.
- [deploy/1_Deploy_VSS_docker.ipynb](deploy/1_Deploy_VSS_docker.ipynb): This notebook is meant for other CSPs which might also require a driver update.

##### System Requirements

- Ubuntu 22.04
- NVIDIA driver 535.161.08 (Recommended minimum version)
- CUDA 12.2+ (CUDA driver installed with NVIDIA driver)
- Docker Compose v2.32.4


#### Helm Chart Deployment

Once approved for Early Access, the [Members page](https://developer.nvidia.com/ai-blueprint-for-video-search-and-summarization-early-access/members) will contain a link to 'Download helm chart from NGC' and 'Documentation'. Follow the guide to deploy the blueprint with Helm Chart.

##### System Requirements

- Ubuntu 22.04
- NVIDIA driver 535.161.08 (Recommended minimum version)
- CUDA 12.2+ (CUDA driver installed with NVIDIA driver)
- Kubernetes v1.31.2
- NVIDIA GPU Operator v23.9
- Helm v3.x


### License
This NVIDIA AI BLUEPRINT is licensed under the [Apache License, Version 2.0.](./LICENSE.md)



