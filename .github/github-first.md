# GitHub-First: onboarding a module to GHCR + the nightly

How to move a first-party component onto the GitHub-built GHCR channel so it
participates in the same build, tagging, and `ci-vss-oss` nightly acceptance
flow as `vss-agent`.

Worked example throughout: **`vss-video-summarization`** (the LVS module), which
is currently staged but not onboarded — `strategy: build`, `ghcr_build: false`.

---

## What onboarding gets you

| | |
|---|---|
| Built on every PR and merge | `pr-<N>-<sha12>` and `develop-<sha12>`, both immutable |
| Content-addressed reuse | unchanged source re-tags instead of rebuilding under arm64 emulation |
| Complete candidate sets | one `VSS_CONTAINER_TAG` names your image alongside every other |
| Nightly acceptance | deployed and tested by `ci-vss-oss` with no per-image wiring |

That last row is the payoff and it is recent. Acceptance used to carry a
hardcoded per-image map that had to be updated for every new image; it now
derives every coordinate from the commit SHA, so **there is nothing to do on the
GitLab side.** Onboarding is entirely a change in this repo.

---

## The five steps

### 1. Flip `ghcr_build` in the inventory

`deploy/docker/container-inventory.json` is the single source of truth for what
gets built. Set `ghcr_build: true` and make sure `source_path`, `platforms` and
`dockerfile` are correct:

```json
{
  "name": "vss-video-summarization",
  "strategy": "build",
  "ghcr_build": true,
  "source_path": "services/video-summarization",
  "dockerfile": "services/video-summarization/docker/Dockerfile",
  "platforms": ["linux/amd64", "linux/arm64"],
  "compose_image_names": ["vss-video-summarization"],
  "tag_variables": ["CONTAINER_IMAGE"]
}
```

`source_path` is load-bearing beyond the build: it is what
`git rev-parse <commit>:<source_path>` hashes to produce the `tree-<sha>` content
tag, and what the change detector diffs to decide whether to build.

### 2. Split image and tag variables

Some modules bundle both into one variable:

```bash
# before — image and tag in a single value
CONTAINER_IMAGE="${CONTAINER_IMAGE:-${VSS_CONTAINER_RELEASE_REGISTRY}/vss-video-summarization:3.2.1}"
```

The shared coordinate needs them separate, because registry and tag move
independently. Split first:

```bash
VSS_VIDEO_SUMMARIZATION_IMAGE="${VSS_VIDEO_SUMMARIZATION_IMAGE:-${VSS_CONTAINER_REGISTRY:-${VSS_CONTAINER_RELEASE_REGISTRY}}/vss-video-summarization}"
VSS_VIDEO_SUMMARIZATION_TAG="${VSS_VIDEO_SUMMARIZATION_TAG:-${VSS_CONTAINER_TAG:-3.2.1}}"
```

**Precedence matters and is easy to get backwards.** The per-image variable must
come *first*, folding the shared tag in as its fallback. Inverting it makes the
per-image override dead — `VSS_CONTAINER_TAG` is always defined in
`containers.env`, so it would always win and nobody could pin one image.

Update `tag_variables` in the inventory to match the new names.

### 3. Point the compose line at the shared coordinate

```yaml
# before — pinned to NGC, ignores the shared coordinate entirely
image: ${CONTAINER_IMAGE:-nvcr.io/nvidia/vss-core/vss-video-summarization:3.2.1}

# after — follows VSS_CONTAINER_REGISTRY / VSS_CONTAINER_TAG
image: ${VSS_VIDEO_SUMMARIZATION_IMAGE:-${VSS_CONTAINER_REGISTRY:-ghcr.io/nvidia-ai-blueprints/vss}/vss-video-summarization}:${VSS_VIDEO_SUMMARIZATION_TAG:-develop-latest}
```

Two rules:

- The **inline literal is `develop-latest`**, not an NGC release tag. A clean
  clone with no env-file must resolve to a coordinate that actually exists in
  GHCR. The NGC tag stays in `containers.env` as the inner fallback.
- The compose line reads **only the per-image tag variable**. `containers.env`
  already folds `VSS_CONTAINER_TAG` into it; naming both here double-handles the
  chain and inverts the precedence.

Registry and tag must move **together**. `3.2.1` does not exist in GHCR and
`develop-latest` does not exist in `nvcr.io`, so changing one alone resolves to
a coordinate that 404s.

### 4. Regenerate the golden file

```bash
python3 .github/scripts/compose_image_golden.py --update
python3 .github/scripts/compose_image_golden.py        # verify
python3 .github/scripts/release_set.py closure         # inventory coverage
```

The golden diff is the point of review: it shows the resolved coordinate moving
from NGC to GHCR in one line, which is exactly the change a reviewer should be
asked to approve.

### 5. Land it, then check the first build

Nothing else is required. On merge, `build-dev-images.yml` picks the image up
from the inventory and publishes:

- `develop-<sha12>` — immutable per-commit candidate
- `tree-<tree_sha>` — content-addressed, drives reuse and the post-merge retag
- OCI labels `com.nvidia.vss.{image_name,source_path,source_tree_sha}`

Nothing in `ci-vss-oss` needs changing.

---

## Verify

```bash
cd ~/VSS/vss-gh/video-search-and-summarization
git fetch origin develop
TAG="develop-$(git rev-parse --short=12 origin/develop)"
REG=ghcr.io/nvidia-ai-blueprints/vss

docker manifest inspect "$REG/vss-video-summarization:$TAG" >/dev/null \
  && echo "published" || echo "missing $TAG"
```

Then confirm the shared knob reaches it:

```bash
cd deploy/docker
VSS_CONTAINER_TAG="$TAG" docker compose --env-file containers.env \
  -f services/.../compose.yml config --images
```

The output must name `ghcr.io/...` at `$TAG`. If it still shows `nvcr.io`, step 3
is incomplete.

---

## Pitfalls

Each of these has actually happened.

**`ghcr_build: true` without steps 2–3.** The image builds to GHCR on every
merge and nothing consumes it — deployments keep pulling NGC. The build output is
orphaned and the drift is invisible until someone compares registries.

**Twelve characters, not seven.** Tags use `commit_sha[:12]`. `git rev-parse
--short` defaults to 7 *and widens as the repo grows*, so it cannot name a tag.
Always `--short=12`.

**Inverted tag precedence.** Writing `${VSS_CONTAINER_TAG:-${VSS_<C>_TAG:-...}}`
in compose makes the per-image override unreachable, because the shared variable
is always set. Per-image first, shared as its fallback, chained in
`containers.env`.

**Registry moved without the tag.** Resolves to a real registry with a tag that
does not exist there. The golden test catches it — read the diff rather than
regenerating past it.

**Expecting a tag at a commit that built nothing.** A `develop-<sha12>` only
exists once the image has a published `tree-<sha>` to retag from. For a
newly-onboarded module that means after its first real build.

---

## How it fits together

```
inventory (ghcr_build: true)
   │
   ├─ detect_changed_images  →  build matrix   (source diff, plus a missing tree-<sha>)
   │
   ├─ build-dev-images       →  develop-<sha12>  ·  tree-<tree_sha>  ·  OCI labels
   │                            pr-<N>-<sha12>   on PR branches
   │
   ├─ advance_ghcr_alias     →  retags the complete candidate set from tree-<sha>
   │
   └─ ci-vss-oss acceptance  →  VSS_CONTAINER_TAG=<develop|pr-N>-<sha12>
                                containers.env resolves every image from it
```

The invariant the whole chain rests on: **at any develop tip or PR head, every
GHCR image carries that ref's tag.** Complete sets are what let a consumer derive
coordinates from a commit SHA instead of reading a manifest — which is why
step 3 is not optional. An image that builds to GHCR but is not on the shared
coordinate breaks the invariant for everyone.

## Reference

| | |
|---|---|
| Inventory | `deploy/docker/container-inventory.json` |
| Shared coordinate | `deploy/docker/containers.env` |
| Golden | `deploy/docker/test-scripts/compose-images.golden` |
| Build | `.github/workflows/build-dev-images.yml` |
| Tag derivation | `.github/scripts/container_build_plan.py` |
| Retag | `.github/scripts/advance_ghcr_alias.py` |
| Acceptance | `ci-vss-oss` → `ci/setup-eval-overrides.sh` |
