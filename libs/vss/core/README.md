# VSS core

`nvidia-vss-core` provides reusable libraries without a CLI, agent framework,
GPU runtime or deployment discovery dependency. The point-call CLI uses the
same multimodal request serializer and chat transport described below. Its
existing video inputs, configuration, output and job lifecycle remain unchanged.

## Multimodal chat

Inject an already resolved endpoint, backend, model and runtime credential.
Core does not read deployment configuration or environment variables, discover
models, probe capabilities, parse application answers or substitute routes.

```python
from vss_core.vlm import (
    ChatMessage, ChatRequest, GenerationOptions, ImageBytes, ImagePart,
    TextPart, VLMChatClient,
)

async def compare(endpoint: str, model: str, api_key: str, png_bytes: bytes):
    request = ChatRequest(
        messages=(
            ChatMessage("system", "Describe the supplied images."),
            ChatMessage("user", (
                TextPart("Reference:"),
                ImagePart("https://media.example/reference.png"),
                TextPart("Target:"),
                ImagePart(ImageBytes(png_bytes, "image/png")),
            )),
            ChatMessage("assistant", "The target"),
        ),
        model=model,
        generation=GenerationOptions(top_p=0.9, top_k=20, repetition_penalty=1.1),
        continuation=True,
    )
    async with VLMChatClient(endpoint, "vllm", api_key=api_key) as client:
        completion = await client.complete(request)
        return completion.text, completion.metadata()
```

Models are frozen dataclasses; unknown constructor fields fail. `ChatMessage`
accepts a string or a nonempty tuple of `TextPart`, `ImagePart` and `VideoPart`.
Roles are `system`, `user` and `assistant`; media requires `user`. Requests need
at least one user message. Message roles, order, string whitespace and part
order reach the HTTP boundary unchanged. No text parts are joined in requests.

`ImagePart.source` is an HTTP(S) URL, validated image data URI, or `ImageBytes`.
Optional `detail` is `auto`, `low` or `high`. Embedded images support PNG, JPEG
and WebP with MIME/signature checks; there is no download or reencoding step.
`VideoPart.source` is an HTTP(S) URL, MP4 data URI, or `VideoFile(Path(...))`.
Local video streams JSON in 192 KiB raw chunks with a fresh file per attempt.
Media references reject file URLs and embedded userinfo. Local/loopback HTTP
references are allowed for deployments that need them.

Limits: 256 messages, 1024 content parts, 32 images, 20 MiB decoded bytes per
embedded image, 64 MiB total embedded image bytes, and 512000 text characters.
These bound client requests; provider context/token limits can still reject them.

`GenerationOptions` omits unset fields. Temperature is finite in `[0,2]`,
`top_p` in `[0,1]`, `top_k` a strict integer (`-1` or `>=1`), and
`repetition_penalty` finite and positive. `max_tokens` is an integer in
`1..1000000`; `seed` is in `1..2**32-1`. The video CLI retains its narrower
existing temperature range and does not expose these new Python controls.

`continuation=True` requires a nonempty final assistant text message and sends
`add_generation_prompt=false` and `continue_final_message=true`. The returned
text is the generated suffix, preserved exactly; core does not prepend the
prefix or parse reasoning tags, JSON or classifications. A caller that needs
a full answer combines its own prefix and suffix.

## Backend contracts

| Backend | Contract |
|---|---|
| `vllm` | Ordered text/images/history, multiple videos, generation controls and continuation; reasoning maps to `chat_template_kwargs.enable_thinking`. |
| `openai` | Ordered messages/media; `top_p` and explicit `top_k`, repetition penalty and continuation are sent top-level. Compatible gateways may reject extensions; there is no retry with removed fields. |
| `rt_vlm` | Exact string text history with at most one nonempty leading system message, or one user turn containing one media and one text part with an optional leading string system message. Text arrays, media history, multiple media, continuation and repetition penalty reject locally. `top_k` must be `1..1000`. |
| `cosmos_reason_nim` | Alpha legacy single-video mapping only. New image/chat/continuation and generation extensions reject locally until verified mappings exist. |

RT-VLM may discard older history to fit its server context budget even for the
accepted string subset. Static client validation preserves the transmitted
request; it does not promise model-side retention or prove endpoint capability.
Application-specific compatibility probes belong to callers.

`VideoOptions(fps, max_frames, total_pixels, chunk_duration)` requires video.
Existing sampling translations are preserved: vLLM gets both loader frame caps,
`num_frames=-1` for uncapped FPS, and processor resampling disabled; RT-VLM/NIM
prefer FPS over a simultaneous fixed frame count, with a warning. Pixel budgets
carry both size edges. Positive chunk duration is limited to RT-VLM/NIM. The
`openai` backend warns and omits existing engine-specific sampling/reasoning
options, preserving its plain-completion behavior.

## Results, errors and ownership

`ChatCompletion` contains raw `text`, requested/reported model, completion ID,
finish reason, `truncated` (`finish_reason == "length"`), optional `TokenUsage`
(prompt/completion/total tokens), attempts, latency and separate optional
`reasoning_content`. Missing metadata is allowed; wrong-typed present metadata
fails as `response_format`. `complete(..., allow_text_parts=False)` retains string-only response acceptance
for existing consumers such as the video CLI. Empty/truncated completions are representable;
callers decide what to do. `metadata()` omits answer text. No raw provider
response is exposed as telemetry. No new persistent schema is introduced.

`ChatError` carries `kind`, `status_code`, `attempts` and `latency_s`.
Kinds are validation, lifecycle, rejected, rate_limited, timeout, transport and
response_format. Diagnostics do not echo request content, media, prefixes or
credentials. Validation/lifecycle failures make zero HTTP attempts. HTTP status
is retained on rejection and retry exhaustion. Latency covers serialization,
HTTP attempts and backoff, excluding caller-side media lookup or persistence.

Clients lazily initialize one transport, bind to one event loop and support
sequential and overlapping calls, including failure followed by success. Use
`async with` or explicitly `await client.aclose()`. Per-call body/response
resources close on success, failure and cancellation. An injected HTTP client
remains caller-owned; owned clients close only at shutdown. Shutdown is
idempotent when idle, rejects active calls and cross-loop use, and does not
implicitly cancel calls. Finish or cancel-and-await calls before shutdown.
Closed clients cannot reopen. The optional monotonic `clock` enables tests.

The default is one attempt. Callers can explicitly select bounded retries.
`retry_statuses=None` uses 429/all 5xx; an immutable explicit collection replaces
that policy, and an empty collection disables status retries. Selected transport
errors still retry. There is one retry loop in `OpenAIChatTransport`, with
replayable body factories for local video. No response parsing retries occur.
`OpenAIChatRejectedError` and `OpenAIChatRequestError` remain available to
existing transport consumers, with optional structured HTTP status.

`VLMAnalyzer` and `OpenAIVLMAnalyzer` remain the sensor-window interfaces used by
search/introspection. Their contracts and persistence adapters are unchanged.
They are separate from the new generic `VLMChatClient` point-call API.
