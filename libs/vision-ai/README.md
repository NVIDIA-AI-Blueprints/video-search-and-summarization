# vision-ai

Tools for agent harnesses over NVIDIA Cosmos and VSS models. This workspace has two packages:

| Path | Distribution | Import | What it is |
|------|--------------|--------|------------|
| `libs/vision-ai` | `nvidia-vision-ai` | `nvidia_vision_ai` | Library: pydantic in, pydantic out. No config files, no default paths. |
| `apps/cli` | `nvidia-vision-ai-cli` | `nvidia_vision_ai_cli` | The `vision-ai` command: per-tool config, plugins, and a local job store. |

The first tool is `vlm`. It sends one question about one video to an OpenAI-compatible VLM
(RT-VLM, vLLM, Cosmos Reason NIM or a plain OpenAI endpoint).

## Install (development)

```bash
cd libs/vision-ai
uv sync                 # Python >= 3.13; installs both packages editable plus dev tools
uv run vision-ai --help
```

## CLI

```bash
vision-ai configure vlm set base_url=http://localhost:8000 backend=vllm model=nvidia/cosmos3-reasoner
export VISION_AI_VLM_API_KEY=...                       # secrets: env or config file, never a flag
cat key.txt | vision-ai configure vlm set api_key=-    # ...or store it (file is mode 0600)
vision-ai configure vlm check                          # GET /v1/models

vision-ai vlm --prompt "What happens?" --media-url https://example.com/clip.mp4
vision-ai vlm --prompt "What happens?" --file ./clip.mp4 --fps 2   # flags override config for this call

vision-ai memory list --type vlm --limit 5
vision-ai memory get vlm-01K7...
```

- **Settings** resolve as flags > `VISION_AI_<TOOL>_<FIELD>` > `~/.vision-ai/<tool>/config.json` > defaults.
  Set `VISION_AI_HOME` to move `~/.vision-ai`.
- **Jobs.** Every tool call is synchronous and recorded in `~/.vision-ai/memory/main.db` (SQLite):
  first as `running`, then as `completed` or `failed` (Ctrl-C and SIGTERM included). stdout
  carries the job record as one JSON object. A call rejected before it runs prints
  `{"error": ...}` and creates no record. That covers bad flags, an invalid config file, a
  missing `base_url`, and the wrong number of media sources. Failures once the call is under
  way (the backend is unreachable or rejects the request, or the model is ambiguous) are
  recorded as `failed`.
- **Files.** `~/.vision-ai` is created owner-only (0700), and `config.json` and `main.db` are 0600.
- **Exit codes:** 0 ok · 1 error · 2 invalid input · 3 configuration · 4 backend unreachable ·
  5 not found · 6 backend error.

## Library

```python
import asyncio
from nvidia_vision_ai.vlm import Endpoint, VlmRequest, analyze

answer = asyncio.run(
    analyze(
        VlmRequest(
            endpoint=Endpoint(base_url="http://localhost:8000"),
            model="nvidia/cosmos3-reasoner",
            backend="vllm",
            prompt="What happens?",
            media_url="https://example.com/clip.mp4",
            fps=2,
        )
    )
)
print(answer.answer)
```

## Writing a tool

A tool is a subclass of `nvidia_vision_ai_cli.Tool` that declares a `Settings` model, an `Input`
model and `async execute(inputs, settings)`. Register it under an entry point:

```toml
[project.entry-points."vision_ai.tools"]
acme = "acme_vision_ai.tool:TOOL"
```

It then gets `vision-ai acme`, `vision-ai configure acme`, job recording and `vision-ai memory list
--type acme` with no further code. `VISION_AI_DISABLE_PLUGINS=acme` turns it off.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
