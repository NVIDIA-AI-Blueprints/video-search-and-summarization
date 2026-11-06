<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Regenerating development images

`Build Dev Images (GHCR)` builds the inventory entries with `strategy: build`
and `ghcr_build: true`, including both standard and native platform builds.
External pinned images and mirrors are not rebuilt from this repository.

Builds retain SLSA provenance (`mode=max`) and explicitly use
`oci-artifact=false`. This stores BuildKit attestations in the image index
rather than as OCI subject referrers. NGC's current publisher classifies
referrers by `dev.sigstore.bundle.predicateType`, while BuildKit puts its
predicate in the in-toto statement and the layer's `in-toto.io/predicate-type`
annotation. Increasing provenance detail alone does not change that format.
The `com.nvidia.vss.attestation_policy` label also changes platform digests,
so old incompatible referrers in dev NGC are not attached to the new platforms.

The workflow checks that every platform retains a legacy SLSA attestation
before emitting a release-set fragment. This checks the exported structure;
GHCR-to-dev-NGC copying and dev-to-staging publishing still need an end-to-end
validation. It does not create Sigstore bundles or certify LaunchAPI acceptance.

## One-time rollout

Merging the workflow change into `develop` automatically selects every buildable
GHCR image and bypasses the source-content reuse shortcut. Docker layer caches
remain enabled; each image is exported again with the new provenance policy.
The new merge commit gets its usual immutable `develop-<sha12>` tags, complete
`release-set` artifact, refreshed `tree-*` content aliases and `develop-latest`.
Use that new commit's successful release set for the normal nightly promotion.
A rerun of an already completed push retains the usual idempotent preflight.

## Regenerate again at the same commit

After merging, run the workflow manually against `develop`:

```bash
gh workflow run build-dev-images.yml --ref develop
```

Or choose **Actions → Build Dev Images (GHCR) → Run workflow → develop**.
Dispatches on other branches are skipped. Each dispatch rebuilds all buildable
GHCR images without the `tree-*` reuse shortcut, using candidate tags such as:

```text
develop-<sha12>-rebuild-<run_id>-<run_attempt>
develop-<sha12>-rebuild-<run_id>-<run_attempt>-sbsa
```

The planning job generates that ID once and passes it to every build and
publication job. **Re-run failed jobs** or retrying only publication retains
the original ID, so successful platforms' staging tags remain usable.
**Re-run all jobs** reruns planning with a new `run_attempt`, giving fresh
coordinates consistently across every platform. Existing immutable
`develop-<sha12>` tags are preserved. Content aliases and `develop-latest` are
refreshed only as the existing publication steps succeed.

Download the manual run's `release-set` artifact to obtain the regenerated tags
and digests. Consumers that derive `develop-<sha12>` solely from the commit will
still use the original images; use the regenerated release set explicitly, or
make a new develop commit for the normal commit-based promotion path. Manual
dispatch does not itself trigger NGC promotion.
