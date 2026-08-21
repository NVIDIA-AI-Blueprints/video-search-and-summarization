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

## The seven steps

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
  "compose_image_names": ["vss-video-summarization"]
}
```

`tag_variables` is descriptive metadata — nothing in this repo or in
`ci-vss-oss` reads it. Keep it accurate if an entry already has one, but a new
entry does not need it and nothing depends on it.

`source_path` is load-bearing beyond the build: it is what
`git rev-parse <commit>:<source_path>` hashes to produce the `tree-<sha>` content
tag, and what the change detector diffs to decide whether to build.

### 2. Declare a namespaced image/tag pair in `containers.env`

`deploy/docker/containers.env` is where every first-party coordinate is
declared. A module joining the shared set needs exactly two variables:

```bash
VSS_VIDEO_SUMMARIZATION_IMAGE="${VSS_VIDEO_SUMMARIZATION_IMAGE:-${VSS_CONTAINER_REGISTRY:-${VSS_CONTAINER_RELEASE_REGISTRY}}/vss-video-summarization}"
VSS_VIDEO_SUMMARIZATION_TAG="${VSS_VIDEO_SUMMARIZATION_TAG:-${VSS_CONTAINER_TAG:-3.2.1}}"
```

These replace whatever the module used before. LVS currently has:

```bash
# remove — bundles image and tag, and is not namespaced
CONTAINER_IMAGE="${CONTAINER_IMAGE:-${VSS_CONTAINER_RELEASE_REGISTRY}/vss-video-summarization:3.2.1}"
```

Two reasons it cannot stay:

- **It welds the tag onto the image.** The shared coordinate needs them
  separate, because `VSS_CONTAINER_REGISTRY` and `VSS_CONTAINER_TAG` move
  independently. A shared tag cannot be folded into a value that already ends
  `:3.2.1`.
- **It is unnamespaced.** Every other first-party variable is `VSS_*`. A bare
  `CONTAINER_IMAGE` set in the environment for any unrelated reason silently
  redirects the image.

**Precedence is easy to get backwards.** The per-image variable comes *first*,
with the shared one folded in as its fallback. Inverting it makes the per-image
override dead — `VSS_CONTAINER_TAG` is always defined here, so it would always
win and nobody could pin a single image.

**Then remove the old variable everywhere, not just here:**

```bash
git grep -n CONTAINER_IMAGE -- deploy/
```

For LVS that is five files — `containers.env`, `container-inventory.json`, the
service `compose.yml`, `lvs.env`, and the service README. `lvs.env` is the easy
miss: the profile keeps setting a variable nothing reads, so the override
silently stops working.

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
- The compose line reads **only the variables declared in step 2**, by the same
  names. `containers.env` already folds `VSS_CONTAINER_TAG` into the per-image
  tag; naming both here double-handles the chain and inverts the precedence.

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

### 5. Make the GHCR package public

A newly published GHCR package is **private by default**. The nightly deploys the
mirrored images from `nvstaging`, and the mirror cannot read a private package —
so a private image fails the nightly, not your PR.

Check before merging:

```bash
IMG=nvidia-ai-blueprints/vss/<image-name>
TOK=$(curl -s "https://ghcr.io/token?scope=repository:$IMG:pull" | jq -r .token)
curl -s -H "Authorization: Bearer $TOK" "https://ghcr.io/v2/$IMG/tags/list?n=1"
```

A JSON body with `"tags"` means public. `{"errors":[{"code":"DENIED"}]}` means it
is still private — ask **Sarath** to flip it. Visibility is per package
name and sticky, so this is a one-time action per image.

### 6. Register the image for mirror + promotion

`deploy/docker/container-inventory.json` says *how the image is built*. It does
**not** say where it is mirrored or promoted. That is a second inventory, in the
devops repo:

[`ci/vss-ghcr-images.yml`](https://gitlab-master.nvidia.com/metromind/ci-vss-oss/-/blob/main/ci/vss-ghcr-images.yml)

Copy an existing `images:` block and edit it. Ask the agent what the entry should
contain if you are unsure — the fields route the mirror and pick the
artifacts-promotion config, and a wrong `promotion_config` promotes into the
wrong team.

Nothing fails if you skip this. The image builds and publishes to GHCR
perfectly; it is simply invisible to the mirror, so `nvstaging` never receives
it and the nightly deploy of that profile fails looking for an image that was
never promoted. **An absent entry is not an error — it is silence.**

### 7. Land it, then check the first build

With steps 5 and 6 done, merging is all that remains. On merge,
`build-dev-images.yml` picks the image up from the inventory and publishes:

- `develop-<sha12>` — immutable per-commit candidate
- `tree-<tree_sha>` — content-addressed, drives reuse and the post-merge retag
- OCI labels `com.nvidia.vss.{image_name,source_path,source_tree_sha}`

Both tags are pushed in a single `build-push-action` call with `push: true`, so
they name the same manifest and therefore the same digest. Nothing posts a
digest separately — the digest *is* the content hash of what was pushed, so it
exists the moment the push lands.

Nothing in `ci-vss-oss` needs changing.

#### Later commits usually publish nothing new

Only commits that touch your `source_path` rebuild. On every other develop
commit your image is **re-tagged, not rebuilt**: the post-merge retag points that
commit's `develop-<sha12>` at the existing manifest, sourced from `tree-<sha>`.

So this is expected, not a bug:

```bash
docker manifest inspect .../vss-video-summarization:develop-aaaaaaaaaaaa   # sha256:1234...
docker manifest inspect .../vss-video-summarization:develop-bbbbbbbbbbbb   # sha256:1234...  same
```

Several `develop-<sha>` tags sharing one digest means the source did not change
between those commits. It is what makes the candidate set complete at every tip
without rebuilding 5 images per push.

One consequence worth knowing: the release set records `digest: null` for images
it did not rebuild, even though GHCR holds the digest perfectly well. *"GHCR has
the digest"* and *"the release set recorded the digest"* are different questions.
The retag sources from `tree-<sha>` precisely so it never needs the recorded one.

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
| Build inventory | `deploy/docker/container-inventory.json` — how the image is built |
| Mirror inventory | `ci-vss-oss` → `ci/vss-ghcr-images.yml` — where it is mirrored and promoted |
| Shared coordinate | `deploy/docker/containers.env` |
| Golden | `deploy/docker/test-scripts/compose-images.golden` |
| Build | `.github/workflows/build-dev-images.yml` |
| Tag derivation | `.github/scripts/container_build_plan.py` |
| Retag | `.github/scripts/advance_ghcr_alias.py` |
| Acceptance | `ci-vss-oss` → `ci/setup-eval-overrides.sh` |
