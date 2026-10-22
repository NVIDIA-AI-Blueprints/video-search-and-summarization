<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Develop integration, 2026-10-22

Merge parents: evaluation branch `cbca80f24a228987ae252eb9c6b3fdd6a89753fe`
and fetched develop `e8d3632643eb1dc1e84bdc1b32420d7966744222`.
Backup: `backup/search-eval-before-develop-merge-20261022`.
The six published evaluation commits and selective Round8e provenance remain intact.

## Resolution policy

- Preserve the staged runner, three scenarios, offline scoring, artifact loading,
  exact-scan budgets, explicit cleanup confirmation, and configured CLI readiness.
- Preserve validated single-word attributes and the missing-action fusion default.
  Extracted-attribute replay consumes attributes only, without importing source,
  time, object, or route fields. Invalid live/replayed routes remain failures.
- Incorporate source-type-aware decomposition prompts, supported-model thinking
  controls, reasoning-only diagnostics, and failed-call timing. Explicit settings,
  including an empty object, override automatic thinking controls. Effective
  prompts, settings, and source lists participate in resume compatibility.
- Stop queued inference and subsequent retrieval on interruption or fatal CLI
  errors. Live decomposition failures above the unanswered-query tolerance abort
  with durable partial artifacts; they never become embedding fallbacks. Running
  requests finish within their existing backend timeouts before artifacts publish.
- Validate inputs before destructive cleanup. Preserve RTSP/RTSPS sources,
  including case-varied schemes, and refuse cleanup when any source cannot be
  safely classified. Opaque URLs without a recognized video-file suffix require
  source metadata correction before cleanup; guessing their type is unsafe.
- Reused-index inventory checks use the configured source type and all annotated
  sources, including dotted filenames. Other registered sources are reported
  separately; they do not expand the annotation-gallery denominator. Registration
  checks alone do not prove indexed coverage or preprocessing identity.

The upstream background ingestion watcher and automatic-wipe/fallback policies
are not activated by the staged runner. Its standalone utility and tests remain
available for a separately designed configured-CLI integration. Watcher and
fallback-percentage command flags are rejected rather than silently ignored.
The separately discovered adjacent-merge evidence-loss bug is deferred to its own fix.

## Verification

Isolated parent baselines:

| Parent | Benchmark | Search core and CLI | Deployment/preprocessing |
| --- | --- | --- | --- |
| Evaluation `cbca80f24` | 338 passed, 4 optional skips | 1,284 passed | 60 passed |
| Develop `e8d363264` | 218 passed, 4 optional skips | 1,177 passed | Evaluation-specific tests absent |

Integrated result: 485 benchmark tests passed with the same four optional skips;
1,284 search-core/CLI tests and 60 deployment/preprocessing tests passed.
The benchmark suite includes the available local released Drive/PAS reference
fixture check. Skips require the unavailable external `ci-vss-oss` evaluator;
they do not cover those local reference fixtures.

Tests use the checkout's `libs/vss/.venv/bin/python`:

```bash
python -m pytest -q -rs skills/benchmarking/benchmark-video-search/scripts/tests
python -m pytest -q -o addopts= libs/vss/core/tests/unit_test/lib/search_core libs/vss/cli/tests/unit_test/cli
python -m pytest -q deploy/docker/test-scripts/test_evaluation_reset.py deploy/docker/test-scripts/test_evaluation_ingress.py deploy/docker/test-scripts/test_verify_evaluation_embed.py services/rtvi/rt-embed/tests/rtvi_embed/test_cosmos_file_preprocessing.py
```

New integration tests cover parser-to-stage wiring, offline stage isolation,
typed reused-source checks, cleanup classification, decomposition settings,
concurrent cancellation, durable failures, and resume identity changes.
Changed-code Ruff checks, local documentation links, whitespace, and conflict
marker checks pass. The complete merge diff retains five existing upstream
whitespace findings in ReID third-party/source documentation and VIOS codec
license headers, reproduced unchanged against the develop parent; those
unrelated files were not reformatted.
No deployment, ingestion, model inference, or GPU comparison
was performed; no retrieval-quality improvement is claimed.
