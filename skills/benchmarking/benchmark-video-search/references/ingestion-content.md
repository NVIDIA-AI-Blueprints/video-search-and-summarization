<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Indexed-content validation

Coverage counts establish that documents exist. Optional content checks also
establish that the scoped documents satisfy an explicitly declared embedding and
media contract. They use the existing `vss search readiness` command; they run no
model and do not read query annotations, attribute labels or ground-truth crops.

Create an ingestion contract using the exact **requested model ID** expected from
the deployment and its documented vector dimension. For example:

```json
{
  "schema_version": 1,
  "embedding": {
    "model": "cosmos-embed1-448p",
    "dimensions": 768,
    "unit_normalized": true,
    "norm_tolerance": 0.001
  },
  "document_limit": 100000
}
```

Replace the example model with the intended model from your configured deployment
and checkpoint provenance. `model` asserts `llm.info.requestedModel`; this is not
a checksum of model weights, nor an assertion of the independently named
`modelId`. Keep the image/checkpoint receipts as separate runtime identity proof.
`unit_normalized: false` allows non-unit vectors but still rejects zero and
nonfinite vectors. All values and keys are checked before ingestion. The document
limit bounds each inspected index separately, independently of retrieval budgets.

Pass `--ingestion-validation-contract contract.json` to gallery `--stage ingest`
or `--stage run`. Index probes must be enabled and ingestion cannot be skipped. Gallery ingestion
defaults to checking dependencies for all three scenarios; select
`--scenarios embed-only` explicitly when preparing only an embedding benchmark.
Choose the existing ingestion strategy explicitly, or acknowledge the checked
webhook configuration for `vst-direct`. The contract is optional; ordinary search
and existing commands preserve their defaults.

Before cleanup or upload, the runner invokes local `ffprobe -count_frames` for
each declared gallery file. `ffprobe` must be installed on the runner host. Only
positive `nb_read_frames` counts are accepted; duration times frame rate and the
container's declared frame count are not substitutes. Failed decoding or malformed
contracts abort before destructive work. Counts are associated with exact source
names read from VIOS after upload.

After coverage checks, validation scans every scoped embedding document and,
for attribute scenarios, every scoped raw-frame document. It checks:

- Exact requested model, vector dimensions, numeric finiteness, nonzero L2 norm
  and optional unit normalization for every nested vision vector.
- Integer raw frame IDs in the half-open interval `[0, decoded_frame_count)`.
- Complete bounded snapshot coverage, exact totals, successful shards, unique
  document identities and proper pagination cleanup.

Several chunks or vectors in one clip are supported. There are no fixed clip,
query, document-per-clip or frame-rate assumptions. Attribute detection gaps
remain reported gaps; a clip need not contain a detected object. Content budgets,
partial responses and transport failures abort rather than certify a partial
scan. Deterministic content violations stop immediately rather than retrying the
same invalid data.

Each ingestion attempt writes a resolved contract and a validation receipt beside the
requested gallery mapping; the receipt updates on each readiness poll. Receipts contain the contract hash, decoded-frame
method/tool identity, scan coverage and bounded violation examples; vectors are
not copied into receipts. A successful mapping links its receipt by path and
SHA-256. Failed attempts keep their diagnostic receipt and preserve the preceding
mapping and its receipt. Standalone ingestion therefore retains proof even when
no retrieval artifact is produced. The artifact also carries these checks in its
ingestion context when retrieval runs in the same pipeline.

For direct diagnostics, `vss search readiness --content-contract resolved.json
--video-source NAME ...` accepts the resolved schema above plus `frame_counts`, a
mapping of the exact listed source names to positive decoded counts. Include all
sources when requiring attributes. For embedding-only checks use
`--no-require-attributes`; raw data and its permissions are not required.

These checks do not prove that all upstream work has completed or that no writer
can change the data later. Use the isolated reset/retention procedure, readiness
checks and stable campaign inputs together. Temporal annotation eligibility and
critic verification remain separate from indexed-content validity.
