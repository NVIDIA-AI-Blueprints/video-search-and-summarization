<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Isolated evaluation reset

`--clear` in the benchmark removes registered videos. It does not reset an index
lifecycle or establish an empty search archive. Use the administrative reset below
for a clean campaign on a **dedicated evaluation deployment**. Do not use the
ordinary developer helper's destructive startup against a shared deployment.

`evaluation_reset.py` stops the entire selected Compose project before removing
its named volumes and its `data_log` and `agent_eval` directories. This removes
retained Kafka records, VST registrations/uploads, Redis and analytics state, and
Elasticsearch storage together. Input `videos` and downloaded `models` are
preserved. Producers outside this project must not publish to this deployment.

The tool refuses to take ownership of an existing nonempty directory, refuses
external volumes and out-of-scope state mounts, and checks every Docker container
for shared paths/volumes or conflicting static container names. These Compose
files use static container names, so a production stack using those names cannot
coexist on the same Docker daemon. Use a separate host/daemon in that case.

From `deploy/docker`, create a new directory and ownership marker once:

```bash
python3 scripts/evaluation_reset.py \
  --project vss-eval-search --data-dir /srv/vss-eval-search --initialize
```

Prepare your normal search profile runtime environment (credentials, host/GPU
settings and ports), using the checked-in `overrides.env` as the template. Use a
separate evaluation runtime env file; do not reuse a production data directory.
Build the evaluation image using [the image build instructions](EVALUATION_IMAGE.md). Then
validate the reset plan with the same Compose and environment file order used
for deployment:

```bash
python3 scripts/evaluation_reset.py \
  --project vss-eval-search --data-dir /srv/vss-eval-search \
  --compose-file compose.yml \
  --compose-file developer-profiles/dev-profile-search/compose.eval.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-search/.env \
  --env-file /srv/vss-eval-search-runtime.env \
  --retention-hours 336
```

To execute this reviewed plan, repeat the command with
`--execute --report /srv/vss-eval-search-reset.json`. The report must live outside
the data directory. Review its `status: complete` and zero-document evidence
before ingestion. Do not proceed after a failure; the receipt records the error.
The tool does not retry a failed reset or fall back to public administrative REST.

The tool forces `VSS_DATA_DIR` to the owned directory and explicitly forwards the
chosen retention to the existing Elasticsearch initialization service. It
builds the existing Elasticsearch initialization image before stopping anything,
then reinitializes the revision's ILM policies, mappings, and ingest pipelines on fresh
storage; the three upload-anchor indices for embeddings, behavior, and raw detections
are created explicitly from those templates before configuration or ingestion.
Additional concrete indices are created from the same templates during ingestion. It verifies zero `mdx-*` documents both before and after starting the
application services. The verification runs through the existing internal
Elasticsearch initialization container, not through an expanded ingress admin
surface. Ordinary CLI operations remain through `vss`.

The default evaluation retention is 336 hours (14 days); the minimum accepted
value is 72 hours. Choose a window longer than the full campaign, including
recovery and offline investigation. This changes only the isolated deployment's
policies; the production search profile keeps its existing 48-hour default.
Stopping this project and replacing Elasticsearch storage resets the index age as
well as its documents. A document-only cleanup does neither.

After reset, configure and check `vss` against this deployment's public origin,
verify the pinned RT-Embed image/settings, and perform a small ingestion smoke
run. Use the benchmark's embedding and attribute readiness checks before the
full gallery. Save the reset receipt with campaign provenance. The reset itself
has no access to annotation data and makes no changes to benchmark scoring.
