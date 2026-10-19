# nvidia-vision-ai

The library behind the `vision-ai` CLI. Every entry point takes a pydantic model and returns one.

- `nvidia_vision_ai.vlm`: `async analyze(VlmRequest) -> VlmResponse` and `async list_models(Endpoint) -> ModelsResponse`.
  Supports the RT-VLM, vLLM, Cosmos Reason NIM and plain OpenAI request dialects.
- `nvidia_vision_ai.memory`: `JobRecord`, the `JobStore` protocol and `SqliteJobStore(path)`.

Errors: `ConfigurationError`, `BackendUnreachableError` (transport, timeout, 429/5xx after
retries) and `BackendResponseError` (4xx, malformed or empty response).

See the [repository README](../../README.md).
