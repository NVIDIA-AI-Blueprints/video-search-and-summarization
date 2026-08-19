<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Resources server and prerequisites

## The resources server

Scoring is performed by a NeMo Gym **resources server**, `vss_ask_video`, which
implements Gym's `verify()`: it asks the running VSS about a video sensor through
an `ask_vss` tool and grades the answer against the task's rubric with an
independent judge.

**It is not vendored here.** It is a Gym component — it implements Gym's
interface, imports Gym, and runs inside Gym's runtime, which VSS never executes.
Keeping it in the Gym repository means its CI tests it against the API it targets;
an earlier copy maintained outside Gym silently broke when that API changed and
nobody noticed until it was run.

Obtain it from the Gym repository and stage it into a Gym checkout as
`resources_servers/vss_ask_video`, then follow [`references/run.md`](run-lifecycle.md).
That document is the operational contract: staging, the two-phase lifecycle, the
judge settings the server requires at startup, and the reward traps worth knowing
before trusting a number.

## Prerequisites

| Requirement | Check |
|---|---|
| A running VSS deployment, or a Foundation profile you can deploy | `docker ps --format '{{.Names}}' \| grep -qx vss-agent` |
| `VSS_APPS_DIR` set to the repo's `deploy/docker` | `test -d "${VSS_APPS_DIR}/developer-profiles"` |
| A **post-#2376** `nemo-gym` image tag | run the image gate in the next section; it exits non-zero on a tag that fails |
| NGC credentials — for pulling the image once a tag passes the gate, not for the gate itself | `test -n "${NGC_CLI_API_KEY}"` |

