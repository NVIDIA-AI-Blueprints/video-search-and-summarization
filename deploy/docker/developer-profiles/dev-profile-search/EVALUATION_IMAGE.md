<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Build and verify the evaluation image

The normal Compose file pulls its published image. A checkout alone does not
update a running service. The opt-in `compose.eval.yml` override builds RT-Embed
from the chosen checkout and enables the two file preprocessing controls, with
one model process and batch size one. It never pulls a substitute image.

From a clean, committed checkout, export its identity and enter `deploy/docker`:

```bash
export VSS_EVAL_REPO_ROOT="$(git rev-parse --show-toplevel)"
export VSS_EVAL_REVISION="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)" || { echo 'Commit the source before building'; exit 1; }
cd "$VSS_EVAL_REPO_ROOT/deploy/docker"
```

Use exactly the same environment files, profile, project and override for the
build and the [isolated reset](EVALUATION_RESET.md). The runtime env file must
contain the deployment's actual ports, credentials and GPU settings. On ARM,
set `ARM_PLATFORM` to the appropriate existing Dockerfile target (`igpu` or
`sbsa`). Building requires access to its NVIDIA base images and model assets.

```bash
docker compose --project-name vss-eval-search \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-search/.env \
  --env-file /srv/vss-eval-search-runtime.env \
  -f compose.yml \
  -f developer-profiles/dev-profile-search/compose.eval.yml \
  build rtvi-embed
```

After the reset/startup succeeds, read the actual container handle with the same
Compose invocation's `ps -q rtvi-embed`. Verify that handle rather than assembling
a container name:

```bash
python3 scripts/verify_evaluation_embed.py \
  --container "$EMBED_CONTAINER_ID" --revision "$VSS_EVAL_REVISION" \
  > /srv/vss-eval-search-image.json
```

Verification checks the running immutable image ID against its configured tag,
its source revision label, the effective preprocessing/process settings, and the
startup diagnostic from this container start (within the last 10,000 log lines).
Run verification immediately after startup; a missing or rotated startup record
fails verification rather than assuming settings. Save this receipt beside the reset
receipt. A build label records the declared revision; building from a clean
checkout is required for that label to describe the source accurately.

Both preprocessing flags are enabled together. Resizing before frame retention
reduces 150 frames of 1920×1080 (or portrait 1080×1920) RGB from approximately
890 MiB to approximately 86 MiB at 448×448. The subset's MP4 sample tables contain
16 such clips. These are retained-RGB estimates, not measured GPU peaks. The
512 MiB byte limit and 1,024-frame limit apply **per decoder**; decoding,
transforms, model tensors and concurrent requests need additional memory.
Index sampling without resizing still retains native frames and needs a budget
sized for the actual chunk. Do not remove the bounds or infer whole-process
memory use from them. Set `COSMOS_FILE_MAX_RETAINED_BYTES` and
`COSMOS_FILE_MAX_RETAINED_FRAMES` explicitly if another cohort needs larger bounds.

Preprocessing changes require fresh ingestion. Keep the gallery, models,
preprocessing, ingestion strategy, query cohort, candidate budgets and critic
settings fixed across compared rounds. Verify `vss configure check` and the
runner's embedding/attribute readiness gates, then run a small end-to-end smoke
campaign before the full dataset. Real GPU memory, throughput, pixel parity and
retrieval quality must be measured on that deployment.
