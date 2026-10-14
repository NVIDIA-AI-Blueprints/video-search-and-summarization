<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# vLLM Helm chart

This chart deploys a configurable model behind vLLM's OpenAI-compatible API. The
base values use a small text model and omit model-specific multimodal,
generation, and chat-template flags. Put model-specific settings in an
additional values file.

## Prerequisites

- Kubernetes 1.25 or newer
- Helm 3
- NVIDIA drivers and the NVIDIA Kubernetes device plugin
- Egress to the model registry on first start, or a cache claim containing the model

The default image is `vllm/vllm-openai:v0.28.0`. Change the image and resources
to match the model, GPU, and required vLLM version.

## Deploy a model

From the repository root:

```bash
helm upgrade --install vllm \
  deploy/helm/benchmark-profiles/vllm \
  --namespace vllm \
  --create-namespace \
  --set-string vllm.model=mistralai/Mistral-7B-Instruct-v0.3 \
  --wait \
  --timeout 30m
```

By default, resource names do not include the Helm release name. Install only
one release per namespace, set `useReleaseNamePrefix=true` when installing
multiple releases in one namespace, or assign each release a distinct
`nameOverride`/`fullnameOverride`.

Use `vllm.extraArgs` for flags that are not represented directly by the chart.
For example:

```bash
helm upgrade --install vllm \
  deploy/helm/benchmark-profiles/vllm \
  --namespace vllm \
  --set-string vllm.model=some-org/some-model \
  --set-json 'vllm.extraArgs=["--trust-remote-code","--quantization","awq"]'
```

If the model requires a Hugging Face token, create a Secret and set
`huggingFace.existingSecret`. Use `persistence.existingClaim` to reuse an
existing model cache.

## Qwen benchmark profile

`values-qwen.yaml` preserves the validated `Qwen/Qwen3.8-27B` configuration,
including video sampling, thinking mode, resource sizing, and the locked
request policy:

```bash
helm upgrade --install qwen-vllm \
  deploy/helm/benchmark-profiles/vllm \
  --namespace qwen \
  --create-namespace \
  -f deploy/helm/benchmark-profiles/vllm/values-qwen.yaml \
  --wait \
  --timeout 30m
```

The optional request-policy middleware replaces configured fields on
`/v1/chat/completions` and returns `x-vllm-policy-sha256`. It is disabled in the
base values and enabled by the Qwen profile. `requestPolicy.maxBodyBytes` bounds
buffered request bodies; oversized requests receive HTTP 413. The base limit is
16 MiB, while the long-video Qwen profile raises it to 64 MiB.

## Test the API

```bash
kubectl port-forward -n vllm service/vllm 8000:8000
curl http://127.0.0.1:8000/v1/models
```

## Configuration

- `vllm.model` selects the Hugging Face model ID or mounted model path.
- Null scalar options and empty processor/configuration maps are not emitted as
  vLLM command-line arguments.
- `vllm.tensorParallelSize` and `resources.*.nvidia.com/gpu` must agree for
  tensor-parallel deployments.
- `vllm.extraArgs` supplies model- or version-specific vLLM flags.
- `extraEnv`, `extraVolumes`, and `extraVolumeMounts` support custom runtimes
  and mounted model artifacts.
- Resource, persistence, shared-memory, and probe defaults are starting points;
  size them for the selected model.
