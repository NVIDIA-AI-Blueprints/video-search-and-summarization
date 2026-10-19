# nvidia-vision-ai-cli

The `vision-ai` command: tools over [`nvidia-vision-ai`](../../libs/vision-ai), each with its own config
in `~/.vision-ai/<tool>/config.json`. Every call is recorded in `~/.vision-ai/memory/main.db`.

```bash
vision-ai configure vlm set base_url=http://localhost:8000
vision-ai vlm --prompt "What happens?" --media-url https://example.com/clip.mp4
vision-ai memory list --type vlm
```

See the [repository README](../../README.md) for settings precedence, exit codes and the plugin contract.
