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

## Cosmos Reason 3 benchmark profile

`values-cosmos-reason3.yaml` serves `nvidia/Cosmos3-Nano` using the generic
vLLM runtime. Deploy it with:

```bash
helm upgrade --install cosmos-reason3 \
  deploy/helm/benchmark-profiles/vllm \
  --namespace cosmos-reason3 \
  --create-namespace \
  -f deploy/helm/benchmark-profiles/vllm/values-cosmos-reason3.yaml \
  --wait \
  --timeout 30m
```

The preset requests one GPU and retains the Qwen benchmark's BF16 precision,
262,144-token context, 2 FPS sampling, 8,192-frame cap, 16 GiB multimodal
processor cache and 16,384-token output limit. It enables prefix caching and
Cosmos's asynchronous scheduling and data-parallel visual encoder settings.
The one-GPU smoke test used an H200; these resource settings are not a claim
that other GPU models fit the same context or video lengths.

The generation policy uses Cosmos's recommended reasoning settings:
temperature 0.6, top-p 0.95, top-k 20, repetition penalty 1 and presence
penalty 0. It does not pass Qwen's `enable_thinking` or `preserve_thinking`:
the Cosmos checkpoint's chat template ignores those fields. To request
reasoning, append this instruction to each question:

```text
Answer the question using the following format:
<think>
Your reasoning.
</think>
Write your final answer immediately after the </think> tag.
```

Reasoning and the final answer appear together in `message.content`. For
multiple-choice evaluation, validate the closing tag and extract the final
answer after `</think>` before grading. Literal tags are a requested output
format, not a separate hidden-reasoning API contract. See the
[NVIDIA Cosmos prompting guide](https://github.com/NVIDIA/cosmos/blob/main/cookbooks/cosmos3/reasoner/reasoner_prompt_guide.md).

An isolated Helm smoke deployment reached Ready with zero restarts. Three
questions over a three-second video returned correct final answers and
complete reasoning blocks; the multiple-choice answer extracted as `C`.
Multimodal and prefix cache hits were observed across independent requests.
Full-length LVBench inference and accuracy remain untested. Startup can take
several minutes for download, compilation and multimodal warmup.

For the evaluation harness UI, upload a package of this chart, select the
`cosmos-reason3` preset when registering the environment, then deploy it under
a GPU lease. Configure a direct-VLM agent to use the resulting endpoint.
The chart serves inference only; it does not modify multi-step task
instructions or prepare video memories. Direct multi-step tasks must avoid
summary preparation and pass the video before the changing question for a
shared cacheable prefix. Keep a video's questions on the same replica.

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
