# VSS documentation

The Fern site is assembled from `develop` and maintained release branches.
Release branches do not merge into develop. Each branch owns its documentation,
navigation, images, and OpenAPI specification; develop owns the production site
configuration and the list of versions.

| File | Purpose |
|------|---------|
| `docs/index.yml` | Navigation for this branch; paths are relative to this file |
| `fern/docs.yml` | Shared site configuration, redirects, and registered versions |
| `.github/scripts/build_fern_site.py` | Prepare all versions in one temporary tree |
| `fern/fern.config.json` | Pinned Fern CLI version |

## Branch-backed versions

Production reads the version list on **develop**:

```yaml
versions:
  - display-name: "3.3.0"
    slug: "3.3.0"
    branch: release/3.3.0
    release: "3.3.0"
  - display-name: Dev
    slug: dev
    path: ../docs/index.yml
```

`branch` selects the documentation source. `release` independently selects the
installation references. These fields are consumed by the builder, which emits
only Fern-native `path` versions. A branch need not be named after its release.
The first listed version is the default at `/vss/...`; the others use their
version slug, including `/vss/dev/...` for Dev.

Every `--fetch` build refreshes each branch and resolves its commit before
extracting its `docs/` directory and OpenAPI specification. The commit IDs appear
in the build summary. A missing branch or failed fetch aborts the build, even if
a stale local copy exists. Branch corrections therefore update an existing docs
version without a new software tag. Branch-backed docs remain mutable.

For a frozen version, use `ref: vX.Y.Z` instead of `branch` and `release`. Tags
must contain this documentation layout. A tag that does not exist yet is skipped
with a warning; `--require-tag vX.Y.Z` makes it mandatory. Never list both a
branch and tag for the same version slug. Remove an entry to retire a version.

## Version-specific installation values

Author these markers in Markdown/MDX:

```bash
git checkout "${VSS_DOCS_GIT_REF:-develop}"
export VSS_CONTAINER_TAG=${VSS_DOCS_IMAGE_TAG:-develop-latest}
export VSS_RT_VLM_TAG=${VSS_DOCS_SBSA_IMAGE_TAG:-develop-latest-sbsa}
```

| Marker | Dev | `release: "3.3.0"` and its preview |
|--------|-----|-----------------------------------|
| `VSS_DOCS_GIT_REF` | `develop` | `v3.3.0` |
| `VSS_DOCS_IMAGE_TAG` | `develop-latest` | `3.3.0` |
| `VSS_DOCS_SBSA_IMAGE_TAG` | `develop-latest-sbsa` | `3.3.0-sbsa` |

The builder replaces `VSS_DOCS_*` markers in `${NAME:-default}` form in the output only. No
workflow environment variables or stamp commits are needed. Other shell and
Compose variables remain unchanged. Fern's own environment substitution is off;
unknown or malformed docs markers fail the build. The install tag and images
must exist before directing users to install the release; previews may show
these intended values before release artifacts are available.

## Links and images

Internal page links omit the `/vss` base and any version prefix, for example
`[Quickstart](/getting-started/quickstart)`. Fern adds the selected version and
base. Use `/` for the version's home page. Images use paths relative to their
source page: `./assets/images/example.png` at the docs root, or
`../assets/images/example.png` from a nested directory.

Links beginning `/vss` and assets rooted at `/assets/` fail the build in the
checkout and all branch-backed versions. Older immutable tags produce warnings;
`--require-tag` applies the same strict authoring rules to that tag. Each version
keeps its own assets and API specification.

## Local preview

Run from the repository root, using the CLI version in `fern/fern.config.json`:

```bash
SITE="$(mktemp -d)/fern-site"
python3 .github/scripts/build_fern_site.py --no-releases --output "$SITE"
(cd "$SITE" && fern docs dev)
```

Open `http://localhost:3000/vss`. Drop `--no-releases` and add `--fetch` to
assemble all registered versions. Without `--fetch`, the builder uses locally
available remote-tracking branches, falling back to local branches. Use
`--release 3.3.0` to build your checkout as the default **3.3.0 (preview)**;
that release's configured branch is replaced by the checkout, so unmerged PR
content is previewed and does not require fetching itself.

Before publishing, run `.github/scripts/check_fern_site.sh` inside the output
tree. It runs `fern check`, MDX validation, and strict broken-link checks.

## CI and publishing

| Event | Result |
|-------|--------|
| PR mirror push | Check the assembled tree, then update preview `pr-<N>`; PRs targeting a release branch use that release's values |
| Docs push to `release/X.Y.Z` | Update preview `release-X-Y-Z`; also rebuild production if the branch is registered on develop |
| Docs push to `develop` | Rebuild production with Dev and all registered sources |
| Manual production workflow | Rebuild the registered site from develop |

Production always checks out develop and fetches its registered branches.
The workflow must also exist on release branches, because their pushes run
those branches' workflows. Changes to the docs, Fern configuration, OpenAPI
specification, or relevant docs tooling trigger these workflows. Publishing
requires the repository's `DOCS_FERN_TOKEN` secret. Active uploads are not
cancelled; PR preview failures warn independently of the required check.

## Rollout for 3.3.0

Two PRs are required because `release/3.3.0` does not merge into develop:

1. Merge the **release/3.3.0 PR** first. It adds `docs/index.yml`, version-neutral
   links and page-relative images, plus the builder and workflows. Its PR preview
   uses the release checkout directly. On merge, its release preview is published;
   production skips safely while develop still lacks the new builder or source
   registration.
2. Merge the **develop PR**. It installs the assembler and registers
   `branch: release/3.3.0` with `release: "3.3.0"` as the default. The merge rebuilds
   production using develop for Dev and the prepared release branch for 3.3.0.
3. Verify an internal link, image, and installation commands in both versions.
   Subsequent release-branch docs changes republish 3.3.0 automatically. No
   release-to-develop merge or new software tag is needed for docs corrections.

Do not merge the develop registration before its source preparation: builds
intentionally fail if a configured source lacks `docs/index.yml` or valid links.
For another release, prepare its branch with this layout and workflows, then add
its branch and explicit release version to develop. A source on a branch outside
`release/**` also needs that branch added to the production workflow's push filter.
