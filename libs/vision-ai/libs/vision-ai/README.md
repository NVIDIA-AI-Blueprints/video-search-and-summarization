# nvidia-vision-ai

The library behind the `vision-ai` CLI. Every entry point takes a pydantic model and returns one.

- `nvidia_vision_ai.vlm`: `async analyze(VlmRequest) -> VlmResponse` and `async list_models(Endpoint) -> ModelsResponse`.
  Supports the RT-VLM, vLLM, Cosmos Reason NIM and plain OpenAI request dialects.
- `nvidia_vision_ai.memory`: `JobRecord`, the `JobStore` protocol and `SqliteJobStore(path)`.
- `nvidia_vision_ai.knowledge`: `async get_retriever(backend, config) -> BackendAdapter`, with adapters for
  `frag_api`, `es_caption`, `frag_lib`, `llama_index`, `langchain` and `arango_graph`; each backend's
  dependencies are an extra of the same name. `langchain` and `llama_index` require `persist_dir`.
  This code reads no environment variables, but some backend SDKs do when a value is unset:
  `NVIDIA_API_KEY` (`langchain`, `llama_index`) and `NVIDIA_RAG_*` (`frag_lib`).

Errors: `ConfigurationError`, `BackendUnreachableError` (transport, timeout, 429/5xx after
retries) and `BackendResponseError` (4xx, malformed or empty response).

See the [repository README](../../README.md).
