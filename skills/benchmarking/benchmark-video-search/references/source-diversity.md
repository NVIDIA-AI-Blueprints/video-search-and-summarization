<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Optional source diversity for attribute galleries

`--max-hits-per-source N` allocates a bounded return and verification pool across
uploaded sources. It is disabled by default and supports only orchestrated
attribute search over `video_file` sources with an explicit exact-scan budget:

```bash
vss search run attribute --attribute "person wearing a red shirt" \
  --candidate-limit 100000 --top-k 20 --max-hits-per-source 1 --no-critic
```

Use the configured deployment and CLI from this checkout. The option is not
supported by standalone primitives, other search modes, or RTSP sources.
In a gallery campaign, add the option to the existing staged runner invocation;
the selected scenarios must include `attribute-only`. Only that scenario applies
the cap. The runner checks CLI support before ingestion and never silently
falls back to an older implementation. Offline scoring reads saved selection;
do not pass the retrieval option again to `--stage score`.

## Budgets and complete rankings

The indexed-document budget (`--candidate-limit`), returned-pool size
(`--retrieval-pool-size`, or CLI `--top-k`), metric cutoff, and critic verification
count have different meanings. Selection inspects the complete eligible ranking
within the existing exact-scan budget, before applying the returned-pool limit.
There is no implicit 200-hit, tenfold, or 1,000-hit oversampling policy.

The original rank order is preserved. Opt-in attribute hits carry `source_id`,
the original indexed sensor identity captured before VST enrichment. This keeps
a missing-frame hit and a frame-refined hit from the same source under one cap,
even if only one resolves to a VST UUID. Where that identity is unavailable, use
the exact sensor ID, falling back to the exact video name. Missing/empty identities and the literal
`unknown` sentinel do not merge unrelated subjects. Unidentified rows remain
individually eligible and are counted. No annotation or relevance label is used
in online selection.

Before selection, strict processing accounts for thresholding, conversion,
deduplication, exclusions, and limits. Unexpected conversion losses, partial
attribute failures, truncated rankings, and aggregate-budget overflow fail rather
than produce a supposedly complete result. Multiple attributes with unknown
object identities can exceed the aggregate budget; increasing only the return
pool does not fix that failure.

Exact scan completeness covers eligible vector-bearing documents. Scoped
documents without vectors and annotation-gallery coverage are separate facts;
unmeasured missing-vector counts remain unknown. No detections in a clip can be
a legitimate coverage gap. Known valid empty searches remain empty answers.

Strict scans record index UUIDs and a digest of all eligible document IDs and
versions before score filtering. Index recreation or different document versions
between attribute scans fails the request even when document counts match.
This does not make frame lookups or VST metadata part of an atomic snapshot.
Use a quiet, unchanged gallery for controlled comparisons.

## Verification and persisted evidence

CLI output contains selected hits in `data` and an optional `source_selection`
envelope with the original pool, selected positions, effective limits, stage
counts, and completeness evidence. The envelope is absent when disabled.

Only selected hits consume critic work. The original query, subject evidence,
intervals, and critic settings are preserved. Explicit rejections are removed;
unverified hits remain. A rejected person does not trigger verification of a
second person from the same clip. Underfilled pools are reported without further
retrieval or backfill.

Retrieval artifacts and query checkpoints retain the original normalized ranking
once in `hits`, plus selected positions, selection metadata, and `selected_hits`.
Absent selected hits mean the policy was disabled; an empty list means enabled
selection with no results. Selected positions and payload relationships are
validated. Changed policy/settings invalidate resume identity. Old artifacts
remain readable, and offline scoring makes no deployment calls.

## Interpret the reported views

The full raw ranking remains primary. Clip scoring separately reports the first
declared number of original hits (`raw_returned`) and selected raw/critic-filtered
hits (`source_selected`). These bounded views retain the existing scorer,
eligibility rules, AP denominators, grouping, and metric cutoff. Verification
coverage uses the selected pool, never the unverified original ranking.

The original Drive backend keeps its complete-gallery requirements. With Drive,
selected-pool metrics are explicitly unavailable; selection and verification
coverage remain available. Explicitly rescore with `clip-relevance` for selected
metrics. The runner never changes the requested scoring backend silently.

Clip scoring already keeps the first occurrence of each clip. Source diversity
alone therefore does not improve the same complete clip ranking. Its purpose is
to avoid repeated subjects consuming bounded return/verification budgets. Compare
equal-sized original and selected pools derived from that complete ranking.
The reference original pool is an ordinary exact run at full budget C, not an
ordinary bounded run at R: the latter can truncate before final frame-score
sorting. The normal bounded-search implementation is unchanged. Claims about critic-filtered gains
require separate selection-off/on runs with equal verification budgets; one run
does not automatically perform both sets of critic calls.

## Patch provenance and prompt deployment

These ports come from `post-8e-consolidated.patch`, SHA-256
`f6af5f49e03f615d213a7f3560e6c1af0066f3566406e22dc476d8d3f34af06c`,
reviewed against branch revision `1993b942d`. Only shared prompt correctness and
optional attribute-gallery diversity are adopted. Cosmos release adapters,
reranker/training tools, fusion complements, phrase guards, fallback retrieval,
legacy critic policies, and lifecycle replacements are excluded.

The shared prompt now distinguishes person appearance from generic nouns and
vehicle/scenery colours and requests typed required fields. Its examples follow
the same contract. Routing code is unchanged, but changed decomposition output
can select a different route. Capture those transitions and the actual prompt
hash. The local benchmark reads the checkout prompt; an existing published agent
image requires a source build from the reviewed revision to receive it. The
existing temporal compatibility shim is unchanged.

No deployed quality or end-to-end latency improvement is established by unit,
fixture, or scale tests. Record controlled deployment comparisons separately.


## Local scale verification

The opt-in 100,000-hit model → CLI parser → artifact check passed and verified
that each artifact contains one full original pool plus its selected rows. Its
synthetic CLI JSON was about 23.9 MB, the normalized artifact about 35.7 MB, and
peak process RSS about 597 MiB (baseline about 56 MiB). A separate synthetic
100,000-document snapshot scan peaked near 189 MiB. These include fixture,
validation, parser and serialization buffers, and are not per-query deployment
memory guarantees. Use an appropriate explicit budget and measure campaign
concurrency on the actual host.

[The scale receipt](source-diversity-validation.json) records measurements and
limits. Reproduce the optional checks in the installed library environment:

```bash
VSS_SOURCE_SELECTION_STRESS=1 libs/vss/.venv/bin/python -m pytest -q \
  skills/benchmarking/benchmark-video-search/scripts/tests/test_source_selection_scale.py
```

The real Elasticsearch 9.4.4 fixture also checks the full 1,617-candidate
attribute pipeline, baseline ranking parity, two-attribute snapshot consistency,
source selection and selected-only critic handling. It uses an explicit local
fixture port and owns only test indices; it accesses no deployment data.


The final local regression pass completed 1,399 core/CLI, 532 benchmark,
20 real Elasticsearch, and 60 deployment/preprocessing checks. The two opt-in
scale checks passed separately. Four existing reference-parity tests require
`ci-vss-oss/run_eval.py` and remain skipped; optional scale tests are skipped in
ordinary suites. Changed Python files pass Ruff. The changed agent file passes
mypy; the full agent check reports an existing unused-ignore error in the
unchanged `llm_based_rule_validator.py:123`. No source deployment, ingestion,
or live quality campaign was performed.
