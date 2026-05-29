# Troubleshooting — Nemotron Omni on port 30082

## Startup

| Symptom | Cause | Fix |
|---|---|---|
| CUDA OOM during load | Insufficient VRAM or shared GPU | Use NVFP4 checkpoint; free GPU; increase `CUDA_VISIBLE_DEVICES` to a larger card; lower `--max-model-len` |
| `trust_remote_code` error | Flag omitted | Add `--trust-remote-code` |
| Unknown flag (`--reasoning-parser`, etc.) | vLLM too old | Upgrade vLLM to a build that supports Nemotron Omni per model card |
| Bind: address already in use | Port 30082 taken | `ss -ltnp \| grep 30082`; stop Cosmos NIM or other service |

## Hugging Face

| Symptom | Cause | Fix |
|---|---|---|
| 401 / 403 on download | Missing or invalid `HF_TOKEN` | Export token; `huggingface-cli login` |
| Gated repo | No access granted | Accept license on model card with the same HF account |

## Runtime API

| Symptom | Cause | Fix |
|---|---|---|
| Empty `/v1/models` | Server still loading | Wait; tail vLLM logs |
| VSS HTTP 404 on chat | `VLM_NAME` ≠ served `id` | `curl http://HOST:30082/v1/models`; copy exact `id` |
| Audio ignored in VSS | Non-Omni VLM or flag off | Use Omni id; `ENABLE_AUDIO=true`; `omni` in `VLM_NAME` |

## Reachability

From the VSS or client host:

```bash
curl -fsS "http://${VLM_HOST}:30082/v1/models"
```

If this fails, check firewall, `VLM_HOST` (use reachable IP, not `127.0.0.1` from remote clients), and that vLLM bound `--host 0.0.0.0`.
