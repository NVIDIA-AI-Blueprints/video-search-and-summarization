---
name: vss-build-vision-pipeline
description: >-
  Guide agents to create GPU-accelerated computer vision inference pipelines with
  NVIDIA DeepStream SDK for
  image classification, object detection, object tracking, semantic segmentation,
  and instance segmentation.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
---

# Build a Vision Inference Pipeline

Status: skeleton with foundational guidelines and a model-integration workflow
outline; remaining sections and supporting resources are not yet implemented.

## Scope

GPU-accelerated inference pipelines for image classification, object detection,
object tracking, semantic segmentation, and instance segmentation.

## Foundational Guidelines

1. **Build with NVIDIA DeepStream SDK.** Optimize throughput and performance
   through multi-stream batching, in-place GPU processing where supported, and
   TensorRT-accelerated inference. Keep frame processing on the GPU where possible
   and avoid unnecessary CPU/GPU copies. Tune batching for the workload's latency
   requirements; verify performance rather than assuming a configuration is fastest.

2. **Use the latest released DeepStream SDK.** Resolve the current release from
   the official GitHub releases when building a pipeline instead of hardcoding a
   version in this skill. Select an older compatible release only when the
   deployment environment's older NVIDIA driver/CUDA constraints prevent using
   the latest release and upgrading is not possible. Check the release's platform
   and compatibility requirements and record the reason for the fallback.
   Distinguish the container's CUDA runtime from the host CUDA toolkit: an older
   host toolkit alone does not establish that a container is incompatible.

3. **Verify and test in Docker by default.** Use an official DeepStream container
   image matching the selected SDK release and deployment architecture. Invoke a
   host-local DeepStream installation only when the user explicitly requests it;
   do not silently fall back to local installation if Docker is unavailable.
   Containers still require a compatible host NVIDIA driver and GPU access.

   Inspect the container's existing dependencies and reuse compatible packages
   before installing additional ones. Preserve live build logs and command exit
   status (for example, `--progress=plain` with `tee` and `pipefail`). Diagnose slow
   steps from their logs before waiting or retrying; piping a running build to
   `tail` hides progress until it finishes.

4. **Use official GitHub sources as ground truth.** For DeepStream 7.1 and later,
   start with [NVIDIA/DeepStream](https://github.com/NVIDIA/DeepStream) and its
   [releases](https://github.com/NVIDIA/DeepStream/releases). Consult source,
   samples, release notes, and documentation for the selected release, following
   official documentation links from the repository where necessary. Verify APIs,
   configuration files and their parameter semantics, and compatibility against
   the matching tag.

   When investigating repository code or APIs, prefer a shallow checkout of the
   selected release and search it locally. If direct fetches repeatedly fail
   because paths are unknown, inspect the repository tree rather than continuing
   to guess URLs.

5. **Use MediaExtractor for decoding and sampling only.** When the requested
   feature is limited to accelerated decoding and frame sampling, use
   PyServiceMaker's `MediaExtractor` rather than adding inference stages. The
   official [MediaExtractor reference](https://github.com/NVIDIA/DeepStream/blob/main/skills/deepstream-dev/references/media_extractor_advanced.md)
   documents `from pyservicemaker.utils import MediaExtractor`; verify the import
   and API against the selected SDK release. If a required older release lacks
   this API, report the compatibility limitation rather than inventing a substitute
   API or silently changing the implementation.

## Workflow

For model inference, follow these steps. Decoding/frame-sampling-only requests
use `MediaExtractor` as described above and do not require model conversion or
an output parser.

### 1. Investigate the proposed model and establish compatibility

- Locate the authoritative model source, documentation, weights, and export code;
  record the model revision and artifact format. Inspect the actual artifacts
  rather than inferring compatibility from the model name or file extension.
- Analyze the architecture, operators, input/output tensor names, shapes, dtypes,
  layouts, batching, and dynamic dimensions. Identify preprocessing, output
  decoding, and any postprocessing already included in the model.
- Check compatibility with the selected DeepStream/TensorRT release and target
  environment using the matching official sources. Identify unsupported operators,
  export limitations, plugin requirements, and parser/metadata integration needs.
- Summarize what works directly, what needs adaptation, and what remains unverified.
  Distinguish documented compatibility from successful conversion and execution.

### 2. Propose conversions to close compatibility gaps

- For gaps identified above, propose the required export or adaptation path to
  DeepStream/TensorRT, explaining relevant tradeoffs.
- Preserve the requested model's semantics and provide reproducible conversion
  commands or scripts.
- Validate converted outputs against the source implementation.

### 3. Create the model-output parser and metadata integration

- Implement the parser needed to transform model outputs into the task-appropriate
  standard DeepStream metadata for generalized downstream processing. Reuse a
  compatible official parser when available and verify it against the actual
  output contract before writing a custom one.
- Decode tensors according to the model's documented semantics, including class
  mapping, scores, boxes, or masks. Handle batch association, empty results, and
  coordinate transforms; avoid duplicating decoding or suppression already
  performed by the model or inference plugin.
- Find official samples for the corresponding task: detection, classification,
  semantic segmentation, or instance segmentation. Use the selected release's
  parser interface and metadata path along with the found samples.
- Supply the parser source, required build instructions, and inference configuration
  that loads it. Validate decoded results and the resulting metadata in the
  DeepStream pipeline so downstream components can consume them without interpreting
  model-specific tensors themselves.

## Validation and Evaluation

Evaluate the correctness of the generated software and pipeline. Verify the
build, real GPU execution, preprocessing, output parsing, DeepStream metadata,
coordinate transforms, and task-specific data flow.

Use executed tests with known expected outputs. Labeled synthetic model-output
fixtures may test the production parser and metadata integration, including empty
results and geometry edge cases. Retain inputs, assertions, and results separately
from real containerized GPU-run evidence. Investigate output discrepancies using
the model contract and source implementation as needed.

Keep reproducible build/run commands, runtime output, test results, and a concise
report of completed checks and unresolved issues.
