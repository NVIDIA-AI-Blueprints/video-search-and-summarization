# Pinned Hugging Face acquisition inventory

This inventory covers active public model acquisitions that can use a
provider-aware `HF_ENDPOINT`. It records implementation readiness only.
Production cache coverage remains unproven until a drained-fleet canary records
an identical cold miss and warm hit for the same immutable revision.

## Reviewed public revisions

The unauthenticated Hugging Face model metadata API returned the exact public,
ungated, enabled commits below on 2026-08-24:

- [`nvidia/Cosmos-Embed1-448p@f60ec73636eb7c9cc25267367713b7b1b0cffaf3`](https://huggingface.co/nvidia/Cosmos-Embed1-448p/tree/f60ec73636eb7c9cc25267367713b7b1b0cffaf3)
- [`nvidia/Cosmos-Embed1-448p-anomaly-detection@3b1455ed97c7b1d5419c0c3129b7199ca4cd9382`](https://huggingface.co/nvidia/Cosmos-Embed1-448p-anomaly-detection/tree/3b1455ed97c7b1d5419c0c3129b7199ca4cd9382)
- [`Qwen/Qwen3-VL-8B-Instruct@0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/tree/0c351dd01ed87e9c1b53cbc748cba10e6187ff3b)
- [`nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8@8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0`](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8/tree/8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0)
- [`nvidia/NVIDIA-Nemotron-Nano-9B-v2@6533e8de2c68e4536bf7c411d7a3ce5734111476`](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-9B-v2/tree/6533e8de2c68e4536bf7c411d7a3ce5734111476)

Reverify without credentials before changing a pin:

```bash
curl -fsS \
  https://huggingface.co/api/models/OWNER/REPO/revision/COMMIT |
  jq '{id,sha,private,gated,disabled}'
```

Private or gated repositories are not approved by this list. They require a
separate immutable revision, protected read-token configuration, and
authorization-scope review.

## Runtime mapping

| Consumer | Acquisition and layout | Client contract | Status |
| --- | --- | --- | --- |
| RT-Embed standalone Compose, RT-Embed source Compose, and Helm chart | `hf:nvidia/Cosmos-Embed1-448p@f60ec...`; snapshot is atomically materialized as the existing `.../ngc_model_cache/Cosmos-Embed1-448p` directory before Triton repository generation | Source image lock pins `huggingface_hub==0.36.2`; `HF_ENDPOINT` is forwarded; Xet and `hf_transfer` are disabled; Hub cache is temporary | Implementation-ready; canary-required |
| Search profile Docker and Helm values | `hf:nvidia/Cosmos-Embed1-448p-anomaly-detection@3b1455...`; same RT-Embed local-directory and Triton semantics | Same RT-Embed client contract | Implementation-ready; canary-required |
| Integrated RT-VLM Qwen selection in `deploy/docker/scripts/dev-profile.sh` | `hf:Qwen/Qwen3-VL-8B-Instruct@0c351d...`; snapshot becomes the existing `.../ngc_model_cache/Qwen3-VL-8B-Instruct` local vLLM model directory | RT-VLM source image lock pins `huggingface_hub==0.36.2`; endpoint/token forwarding and Xet controls match RT-Embed | Implementation-ready; canary-required |
| Standalone Qwen vLLM services | Hub model ID plus `--revision 0c351d...`; ephemeral `HF_HOME` | Startup wrapper requires exactly `huggingface_hub==0.36.2` before exec | Implementation-ready; canary-required |
| Nemotron FP8 vLLM services | Model uses `--revision 8bc5ee...`; parser init uses `hf_hub_download` for `nemotron_toolcall_parser_no_streaming.py` at `6533e8...` and preserves `/out` volume layout | Both paths require exactly `huggingface_hub==0.36.2`, forward `HF_ENDPOINT`/`HF_TOKEN`, and disable Xet | Implementation-ready; canary-required |

The three current skills-eval inventory candidates
`vss-deploy-video-embedding/standalone_deploy`,
`vss-deploy-profile/search`, and `vss-search-archive/search` are therefore
implementation-ready but still canary-required. This document does not claim
production cache coverage or a GPU workload result.

## Failure and compatibility behavior

- `hf:` model sources require `owner/repo@<40-64 lowercase hex commit>`.
  Missing or mutable revisions fail before any request.
- An unsupported client or malformed `HF_ENDPOINT` fails before model startup.
  There is no Git/raw-URL fallback.
- `HF_TOKEN`, when present, is passed to the Hub client and is never written to
  the revision marker or command arguments.
- The temporary Hub cache is discarded after acquisition. The existing
  materialized RT model directory, file hierarchy, generated Triton repository,
  and read-only/local model consumption remain unchanged.
- A warm RT model directory is reused only when `.hf-revision` exactly matches
  the requested commit. Existing unmarked or mismatched directories fail
  closed instead of silently serving different bytes.
- Direct mode remains supported by leaving `HF_ENDPOINT` unset or setting it to
  `https://huggingface.co`; the same revision and client checks apply.
