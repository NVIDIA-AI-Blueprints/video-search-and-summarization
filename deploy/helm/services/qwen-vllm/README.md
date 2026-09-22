<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Qwen vLLM Helm chart

This chart deploys `Qwen/Qwen3.8-27B` behind vLLM's OpenAI-compatible API. Its defaults configure vLLM 0.28.0 with deterministic generation, thinking disabled, 2 FPS video sampling, exact torchcodec seeking, and the configured multimodal processor size.

## Prerequisites

- Kubernetes 1.25 or newer
- Helm 3
- NVIDIA drivers and the NVIDIA Kubernetes device plugin
- A node with enough GPU memory for the model and a storage class capable of provisioning the 200 GiB model-cache claim
- Egress to Hugging Face on the first start, or a cache claim that already contains the model

The default image is the official `vllm/vllm-openai:v0.28.0` CUDA 13.0 image. The validated host used PyTorch 2.13.0+cu130 and torchcodec 0.16.0. vLLM's CUDA dependency set includes torchcodec, which is required by the default video backend.

## Install

From the repository root:

```bash
helm upgrade --install qwen-vllm \
  deploy/helm/services/qwen-vllm \
  --namespace qwen \
  --create-namespace \
  --wait \
  --timeout 30m
```

If the model requires a Hugging Face token, create a Secret and refer to it without putting the token in a values file:

```bash
kubectl create secret generic hf-token \
  --namespace qwen \
  --from-literal=HF_TOKEN='<token>'

helm upgrade --install qwen-vllm \
  deploy/helm/services/qwen-vllm \
  --namespace qwen \
  --set huggingFace.existingSecret=hf-token
```

Use an existing model-cache claim with:

```bash
helm upgrade --install qwen-vllm \
  deploy/helm/services/qwen-vllm \
  --namespace qwen \
  --set persistence.existingClaim=qwen-model-cache
```

## Test the API

```bash
kubectl port-forward -n qwen service/qwen-vllm 8022:8022
curl http://127.0.0.1:8022/v1/models
```

A chat request can omit the locked fields because the middleware applies them at the API boundary:

```bash
curl http://127.0.0.1:8022/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3.8-27B",
    "messages": [{"role": "user", "content": "Reply with only A."}]
  }'
```

The response contains `x-qwen-policy-sha256`, which identifies the effective policy. The middleware replaces conflicting values for these fields on every `/v1/chat/completions` request:

```json
{
  "temperature": 0,
  "max_tokens": 8192,
  "seed": 1,
  "chat_template_kwargs": {"enable_thinking": false},
  "mm_processor_kwargs": {
    "size": {"shortest_edge": 4096, "longest_edge": 469762048}
  }
}
```

Disable this behavior with `--set requestPolicy.enabled=false` when callers must choose their own sampling settings.

## Video and GPU settings

The default video loader asks the Qwen processor to sample at 2 FPS and caps a video at 768 frames. Keeping `num_frames: -1` lets the FPS setting choose the sample count; setting a positive `num_frames` would replace FPS-based sampling. `max_frames: 768` preserves the validated Qwen processor cap and prevents very long videos from growing without bound.

Kubernetes assigns the physical GPU through the NVIDIA device plugin. `resources.limits.nvidia.com/gpu: 1` requests one GPU; use `nodeSelector`, `affinity`, and `tolerations` to target a GPU node. For tensor parallelism, raise both `vllm.tensorParallelSize` and the GPU request and limit to the same count.

The chart mounts a 32 GiB memory-backed `/dev/shm`, uses a 20-minute startup window for model loading, and defaults to a `Recreate` rollout so two copies do not compete for the same GPU and `ReadWriteOnce` cache claim.

## Important values

| Value | Default | Purpose |
|---|---:|---|
| `enabled` | `true` | Render the chart workloads and supporting resources |
| `global.useReleaseNamePrefix` | `false` | Prefix resource names with the Helm release name |
| `vllm.model` | `Qwen/Qwen3.8-27B` | Hugging Face model ID or mounted model path |
| `vllm.maxModelLen` | `262144` | Maximum model context length |
| `vllm.gpuMemoryUtilization` | `0.75` | Fraction of GPU memory reserved by vLLM |
| `vllm.mediaIoKwargs.video.fps` | `2` | Video sampling rate |
| `vllm.mediaIoKwargs.video.max_frames` | `768` | Upper bound on sampled frames |
| `requestPolicy.payload.max_tokens` | `8192` | Maximum generated tokens per chat request |
| `persistence.size` | `200Gi` | Hugging Face and compile cache claim size |
| `resources.limits.nvidia.com/gpu` | `1` | GPUs assigned to each pod |
