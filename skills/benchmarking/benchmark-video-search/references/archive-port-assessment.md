<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Selective Round8e ports

Source: `vss-minimal-reproduction.tar.gz`, refactor Round8e patch pinned to
`5c904a9e87743831a49848e567e0e4fdcf5ccfff`. Patch SHA-256:
`9b23c88a51e2c9d606160cb9514f565b6967edad16a1b176048fb2af84459554`.
The complete gzip stream and both patch hashes were checked; all 33 refactor
files were reconstructed in memory and matched their manifest hashes before
selecting changes. No archive program was run against a deployment.

## Incorporated behavior

The product prompt now requires explicit source/time constraints and avoids
filename matching or invented midnight filters. The validator accepts the
product's nullable object-ID field. Explicit, strict JSON chat-template settings
are submitted and captured with decomposition evidence; defaults remain intact.
Returned candidate-pool controls are separate from exact scan limits and metric
cutoffs. Optional indexed-content assertions are integrated into the existing
staged runner and CLI rather than importing a separate ingestion/evaluator stack.
See [content validation](ingestion-content.md).

The archive's fresh-ingestion helper imports a missing
`embedding_comparison/prepare_subset.py`, hardcodes 80 clips/600 queries and one
embedding document per clip, and only inspects the first 1,000 raw documents per
source. Its direct REST, container polling, index write blocks and ILM removal
were not imported. Current validation supports complete explicitly bounded scans,
arbitrary cohort sizes, and multiple chunks/vectors.

## Clip diversity: assessed separately

Selecting distinct source clips before spending the critic budget is promising
for clip-level attribute benchmarks. The archive implements heuristic retrieval
headroom (typically 200 hits, capped at 1,000); that cannot guarantee enough
unique clips under heavy object duplication. A follow-up must select from the
existing complete ranking after aggregation/deduplication, preserve its order,
report coverage and handle budget exhaustion. Start with an explicit opt-in
attribute-only file-gallery policy. Production object/segment search and fusion
must retain their existing semantics. No clip-diversity or fusion policy is
introduced by these correctness ports.

## Cosmos v1.2: compatibility assessment

The supplied release differs from the current optimized contract: FP32 video
`[B,C,T,H,W]` divided by 255 into `[0,1]` instead of uint8 `[B,T,C,H,W]`, fixed 128-token INT64
text inputs, singular output names and normalized 768-dimensional vectors.
Compatibility belongs in the existing RT-Embed implementation with explicit,
verified checkpoint metadata; it must not silently reinterpret the current
checkpoint or introduce an independent reference-inference mode.

The archive's seal validator accepts an empty artifact table. Before reuse, it
must require all four engine/configuration artifacts, check nonempty files and
source/adapter/build identities, reject stale or incomplete engines, and retain
supported configurable batch sizes instead of forcing the historical 64. Only
independently validated precisions should be supported. Existing frame selection,
resizing and streaming behavior must remain intact.

A follow-up requires supplied ONNX graphs and processor files, strict manifest
and graph checks, tokenizer/layout tests, actual engine builds and real-frame
numerical parity on the target GPU. The archive contains manifests and adapter
source, not those release weights/graphs. Eight archive CPU contract tests passed
in a temporary directory; one Torch-dependent test was skipped. That establishes
neither GPU parity nor retrieval quality. No release adapter is enabled here.

## Experimental results

The package reports raw overall mAP@10 changes of 36.71 to 40.25 for attributes
and 62.30 to 65.49 for live routing on the same Round8d gallery/checkpoint.
Round8e combines clip diversity, attribute-complement fusion, original-query
attribute text and phrase-based routing guards. Its 20-query pilot cannot assign
the full-cohort gains to individual changes. Full captured rankings are separate
from this bundle, so these metrics were not independently rescored.

The combined policies, phrase guards, legacy three-criterion critic, anchor
removal, fixed six-second scopes, frozen evaluator copies and machine-specific
settings were not adopted. Raw retrieval remains primary; native critic anchors
and full-constraint verification remain enabled when the critic is selected.
