# Comprehensive Guide to the Video Search and Summarization Project

## 1. Introduction and Overview

[Briefly describe the project's purpose and its capabilities, leveraging information from the existing README.]

## 2. Project Architecture

This section provides a high-level overview of the system architecture. For a visual representation, please refer to the architecture diagram in `deploy/images/vss_architecture.png`.
The general flow of the system is as follows: Video/Audio input is processed by the Ingestion Pipeline, which includes VLM (Vision Language Model) for captioning, CV (Computer Vision) for metadata extraction, and ASR (Automatic Speech Recognition) for transcription. The outputs are then indexed. The CA-RAG (Context-Aware Retrieval Augmented Generation) Module uses this indexed data to provide context for generating outputs like Search results, Summaries, and Question Answering.

### 2.1. Key Technologies

Key technologies used in this project include: NVIDIA NIM, Llama 3.1 70B, VILA, NVIDIA NeMo Retriever, Docker, Kubernetes, Helm, and Python.

## 3. Repository Structure Deep Dive

[Explain the layout of the repository.]

### 3.1. `/deploy` Directory
    - Purpose: Deployment scripts and configurations.
    - Sub-directories:
        - `docker/`: Docker Compose configurations for various deployment scenarios.
        - `helm/`: Helm chart for Kubernetes deployments.
        - `images/`: Architectural diagrams and other images.

### 3.2. `/src` Directory
    - Purpose: Contains all the source code for the video search and summarization engine.
    - Sub-directories:
        - `vss-engine/`: The core application.
            - `config/`: Application configuration files (e.g., `config.yaml`, guardrails, overrides).
            - `docker/`: Dockerfile for building the `vss-engine` image.
            - `src/`: The main source code for the engine.
                - `client/`: Source code for client interactions or UI.
                - `cv_pipeline/`: Computer Vision pipeline components.
                - `models/`: Implementations and interfaces for various AI models (VLMs, etc.), including `nvila`, `vila15`, and `openai_compat`.
                - `trt_inference/`: TensorRT-specific inference code.
                - `vlm_pipeline/`: Core logic for the Vision Language Model pipeline, including frame selection, embedding generation, and VLM interaction.
            - `TritonGdino/`: Files related to the Triton Inference Server for Grounding DINO models.
            - `start_via.sh`: Script to start the VSS engine.

### 3.3. Other Key Files
    - `README.md`: Main project README.
    - `LICENSE`: Project license.
    - `SECURITY.md`: Security information.

## 4. Core Components and Their Interactions

[Detail the main software components and how they work together.]

### 4.1. NIM Microservices
    - Explanation of NVIDIA NIM: NVIDIA NIM (NVIDIA Inference Microservices) are optimized AI microservices that streamline the deployment of AI models across various environments. They provide pre-built containers with optimized inference engines and APIs for easy integration.
    - List of models used: Example models leveraged by this project include:
        - VILA (Vision Language model)
        - meta / llama-3.1-70b-instruct (Large Language Model for instruction following)
        - llama-3_2-nv-embedqa-1b-v2 (Embedding model for question answering)
        - llama-3_2-nv-rerankqa-1b-v2 (Reranking model for question answering)

### 4.2. Ingestion Pipeline
    - Video/Audio Input: The system supports ingestion of video data from both files and live streams (e.g., RTSP).
    - Decoding and Frame Selection: Incoming video is decoded into manageable chunks. From these chunks, specific frames are selected for detailed analysis by downstream AI models.
    - VLM Processing: Vision Language Models (VLMs) are employed to generate dense, descriptive captions for the video chunks. This process typically involves providing the VLM with selected frames and a specific prompt (e.g., the nature of which can be seen in `summarization.prompts.caption` within the `config.yaml` file) to guide the caption generation.
    - CV Metadata: Computer Vision pipelines analyze video frames to detect objects, scenes, and other visual elements, providing rich metadata.
    - Audio Transcription: Audio tracks from the video are extracted and processed by an Automatic Speech Recognition (ASR) model to generate text transcripts.
    - Indexing: The generated captions from VLMs, text from ASR, and metadata from CV pipelines are all indexed into vector and graph databases. This allows for efficient similarity search and complex querying.

### 4.3. CA-RAG Module (Context-Aware Retrieval-Augmented Generation)
    - Purpose: The CA-RAG module enhances the capabilities of Large Language Models (LLMs) by leveraging both Vector RAG (Retrieval-Augmented Generation using vector databases) and Graph-RAG (using graph databases) for comprehensive video understanding.
    - Use Cases: This module is crucial for functionalities like generating detailed summaries of video content, answering specific questions about the video, and generating alerts based on detected events or anomalies.
    - Context Extraction: It extracts relevant contextual information from the indexed databases. This context is vital for tasks requiring temporal reasoning (understanding events over time), anomaly detection, and other complex analytical tasks.
    - Context Management: The module incorporates mechanisms for managing context, including both short-term memory (for immediate query context) and long-term memory (for retaining knowledge over extended interactions or analyses).

## 5. Data Flow for Video Analysis

[Illustrate or describe the end-to-end data flow.]
    - Input: Video source, which can be a video file or an RTSP stream.
    - Processing:
        - Video is chunked by the `FileSplitter` for files or handled by a live stream processor for RTSP streams.
        - The `DecoderProcess` decodes these video chunks and performs frame selection to pick representative frames for analysis.
        - If enabled (e.g., for VILA-like models), the `EmbeddingProcess` generates embeddings directly from these frames.
        - The `VlmProcess` takes the selected frames (and/or their embeddings) along with a configured prompt (e.g., a captioning prompt) to interact with the chosen VLM (e.g., NVIDIA VILA, or an external OpenAI-compatible model). This generates descriptive text about the video content.
        - If enabled, the `AsrProcess` extracts audio from the video chunks and transcribes it into text.
        - Concurrently, a Computer Vision (CV) pipeline (e.g., implemented in `gsam_pipeline_trt_ds.py` for object detection/segmentation) processes frames to extract CV metadata.
        - All generated results – VLM captions, ASR transcripts, and CV metadata – are then sent for indexing. This is managed by components (often interacting with modules like `summarization.py` for scene descriptions or `chat.py` for QA data) that populate vector and graph databases.
    - RAG Augmentation: When a user poses a query (for Q&A) or requests a summary, the `CA-RAG` module takes this query. It retrieves the most relevant contextual information (captions, ASR text, CV data) from the databases. This retrieved context is then passed, along with the original query, to a Large Language Model (LLM), such as Llama 3.1 70B.
    - Output: The LLM uses the provided context and query to generate the final output, such as a comprehensive summary or a precise answer to a question.

## 6. Integrating Alternative VLMs (Vision Language Models)

This project is designed to be flexible in its choice of Vision Language Models. While it comes with support for specific NVIDIA VLMs, it also provides a mechanism to integrate other VLMs that offer an OpenAI-compatible API.

### 6.1. The `openai-compat` Interface
    - The VLM pipeline (`src/vss-engine/src/vlm_pipeline/vlm_pipeline.py`) includes an option `VlmModelType.OPENAI_COMPATIBLE`. When this model type is selected, the system uses a generic client for OpenAI-compatible VLM APIs.
    - The core of this interface is the `CompOpenAIModel` class located in `src/vss-engine/src/models/openai_compat/openai_compat_model.py`. This class is responsible for formatting requests and sending them to any VLM service that exposes an API endpoint compatible with OpenAI's standards.
    - `CompOpenAIModel` reads specific environment variables to obtain the necessary information to connect to and authenticate with the external VLM service, such as the API endpoint URL, model name, and API key.

## 7. Detailed Guide: Replacing Existing VLMs with Gemini

This section provides specific instructions for configuring the system to use a Google Gemini VLM, leveraging the `openai-compat` interface.

### 7.1. Prerequisites
    - Access to a Gemini VLM endpoint. This is typically available via Google Cloud Vertex AI. Examples include models like "gemini-pro-vision" or "gemini-1.5-flash-latest".
    - An API Key for the Gemini service that grants you permission to make calls to the model.
    - The specific Gemini model name (e.g., "gemini-pro-vision", "gemini-1.5-flash-latest") that you intend to use.

### 7.2. Configuration Steps

    - **Set VLM Model Type**:
        - The VLM model type for the VSS engine is typically determined by the `--vlm-model-type` command-line argument when starting the `VlmPipeline`. This is often configured in startup scripts (like `start_via.sh`) or within deployment configurations (e.g., Docker Compose environment variables that are passed to the application).
        - To use Gemini, you must set this argument to `openai-compat`. For example, if you are using a script, you might modify a line to be `python via_server.py --vlm-model-type openai-compat ...`. If using Docker Compose, you would set an environment variable that the application then uses to set this argument.

    - **Environment Variables**:
        - The `CompOpenAIModel` class relies on several environment variables to connect to the Gemini VLM. Ensure these are set correctly in your deployment environment (e.g., in your shell, Docker Compose file, or Kubernetes deployment manifest):
            - `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`: Set this to your Gemini model's identifier. For example: `gemini-pro-vision` or `gemini-1.5-flash-latest`.
            - `VIA_VLM_ENDPOINT`: This must be the full API endpoint URL for your Gemini model. For Google Cloud Vertex AI, this URL typically follows a pattern like: `https://<REGION>-aiplatform.googleapis.com/v1/projects/<YOUR_PROJECT_ID>/locations/<REGION>/publishers/google/models/<GEMINI_MODEL_ID>:<METHOD>`.
                - Example for `gemini-1.0-pro-vision` using the `:generateContent` method: `https://us-central1-aiplatform.googleapis.com/v1/projects/YOUR_PROJECT_ID/locations/us-central1/publishers/google/models/gemini-1.0-pro-vision:generateContent`.
                - For streaming models like `gemini-1.5-flash-latest`, the method might be `:streamGenerateContent`. Always refer to the official Google Cloud documentation for the precise endpoint URL structure for your chosen model and region.
            - `OPENAI_API_KEY` or `VIA_VLM_API_KEY`: This is your API key for authenticating with the Gemini service on Vertex AI. The `CompOpenAIModel` will first check for `VIA_VLM_API_KEY`; if that's not set, it will fall back to checking for `OPENAI_API_KEY`.
            - `AZURE_OPENAI_ENDPOINT`: Ensure this environment variable is UNSET or left empty if you are not using Azure OpenAI services. The `CompOpenAIModel` contains logic specific to Azure OpenAI, and having this variable set might cause unintended behavior when targeting a non-Azure (like Gemini) endpoint.
            - `OPENAI_API_VERSION` / `AZURE_OPENAI_API_VERSION`: These variables are generally not required for Google's Gemini endpoints on Vertex AI but are checked by the `CompOpenAIModel` due to its Azure OpenAI compatibility. Leaving them unset is usually fine when configuring for Gemini on Vertex AI.
        - *Note*: The VSS engine's `CompOpenAIModel` was initially designed with Azure OpenAI compatibility as a primary use case for its generic OpenAI interface. This explains the naming of some environment variables (e.g., `AZURE_OPENAI_ENDPOINT`). However, by setting these variables appropriately, it can be effectively configured to work with any OpenAI-compatible endpoint, including those provided for Gemini models on Google Cloud Vertex AI.

    - **Image Handling**:
        - The `CompOpenAIModel` is designed to send image data to the VLM. It does this by encoding the image (which is a PyTorch tensor in the VSS engine) into a base64 encoded JPEG string. This string is then embedded within the JSON payload of the API request. This method is compatible with how Gemini's multimodal capabilities expect image input. You can see the relevant encoding logic in the `tensor_to_base64_jpeg` utility function within `src/vss-engine/src/models/openai_compat/openai_compat_model.py`.

    - **Prompt Considerations**:
        - The default prompts used by the VSS engine for VLM tasks (like video chunk captioning) are configured in `src/vss-engine/config/config.yaml` (see, for example, `summarization.prompts.caption`). These prompts were primarily designed and tested with the default VILA model.
        - Gemini models, like any advanced LLM/VLM, may have different optimal prompting strategies. Their sensitivity to prompt phrasing, structure, and specific keywords can vary. It is highly advisable to experiment with and adjust these prompts to achieve the best possible performance (e.g., in terms of caption clarity, conciseness, level of detail) when using Gemini. If Google provides specific prompting guidelines for your chosen Gemini model, consider them during your experimentation.

### 7.3. Verification
    - After configuring the environment variables and setting the VLM model type, run the VSS ingestion pipeline with a sample video.
    - Carefully monitor the application logs (e.g., from the `vss-engine` container or process). Look for:
        - Messages indicating a successful connection to the Gemini endpoint URL you configured.
        - Absence of API key errors, authentication failures, or endpoint connection errors (e.g., 401, 403, 404, connection timeouts).
        - The actual VLM responses (e.g., generated captions for video chunks) in the logs or designated output locations. Assess the quality and relevance of these responses.
    - If you encounter issues:
        - Double-check every environment variable for typos, incorrect values, or missing information (especially the `VIA_VLM_ENDPOINT` and API key).
        - Ensure your machine or container has network connectivity to the Gemini endpoint.
        - Verify that your Gemini API key has the necessary permissions for the model you are trying to access and that the API is enabled in your Google Cloud project.

## 8. Customization and Further Development

[To be expanded with common issues and customization tips.]

## 9. Troubleshooting

[To be expanded with common issues and customization tips.]
