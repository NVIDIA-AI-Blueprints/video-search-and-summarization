<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# VSS documentation (Fern)

Author MDX in `docs/`. Fern configuration lives in `fern/` only.

| Path | Role |
|------|------|
| `docs/` | MDX pages (landing page is `index.mdx`) |
| `docs/assets/images/` | Images, referenced by a path relative to the page (`./assets/images/...`, `../assets/images/...`) |
| `docs/index.yml` | Navigation (TOC) for the version being built (`path: ./...`) |
| `fern/docs.yml` | Site config, redirects, and the list of published versions |
| `fern/fern.config.json` | Fern organization and CLI version |
| `.github/scripts/build_fern_site.py` | Builds the Fern input tree for every version |

## Links and images

Internal links are site paths without the `/vss` base and without a version
slug, for example `[Quickstart](/getting-started/quickstart)`. Fern adds the
base and the reader's version, so a link on a Dev page stays in Dev and a link
in a release stays in that release. Images use a path relative to the page. Only
a deliberate cross-version link names the version, as in `/vss/3.3.0/...`.

## How the site is built

The site at `docs.nvidia.com/vss` has one version per release plus **Dev**:

| Version | Content from | URL |
|---------|--------------|-----|
| Latest release (first listed) | its `vX.Y.Z` tag | `/vss/...` (default) |
| Older releases | their `vX.Y.Z` tags | `/vss/X.Y.Z/...` |
| Dev | the latest commit on `develop` | `/vss/dev/...` |

`fern/docs.yml` lists them. Each release is a `ref:` entry naming its release
tag, newest first; Dev is the single `path:` entry, the checkout being built:

```yaml
versions:
  - display-name: "3.4.0"
    slug: "3.4.0"
    ref: v3.4.0
  - display-name: "3.3.0"
    slug: "3.3.0"
    ref: v3.3.0
  - display-name: Dev
    slug: dev
    path: ../docs/index.yml
```

Fern never reads that file directly. `build_fern_site.py` writes a temporary
tree in which every version is a prepared copy: the checkout for Dev, and
`git archive <tag>` for each release. In every copy it fills in that version's
values (below), points each release at its own images, and versions internal
links in the non-default versions so readers stay in the version they are
reading. It then rewrites the `versions:` block of the temporary `docs.yml` to
those copies. Every Fern command runs inside the temporary tree; the build never
modifies the checkout.

## Version-specific values

Write the checkout ref and image tags with these markers. They stay runnable as
shell commands in the source, and the build replaces them for each version:

```mdx
git checkout "${VSS_DOCS_GIT_REF:-develop}"
export VSS_CONTAINER_TAG=${VSS_DOCS_IMAGE_TAG:-develop-latest}
export VSS_RT_VLM_TAG=${VSS_DOCS_SBSA_IMAGE_TAG:-develop-latest-sbsa}
```

| Marker | Dev | Release `vX.Y.Z` and its preview |
|--------|-----|----------------------------------|
| `${VSS_DOCS_GIT_REF:-develop}` | `develop` | `vX.Y.Z` |
| `${VSS_DOCS_IMAGE_TAG:-develop-latest}` | `develop-latest` | `X.Y.Z` |
| `${VSS_DOCS_SBSA_IMAGE_TAG:-develop-latest-sbsa}` | `develop-latest-sbsa` | `X.Y.Z-sbsa` |

These markers are the only text the build replaces. Fern's own `${...}`
substitution is turned off (`substitute-env-vars: false`), so every other
`${NAME}` (shell, Compose, and configuration examples) renders exactly as
written and needs no escaping. The build checks every Markdown file under
`docs/` and fails on any other `${…}` use of the `VSS_DOCS_` prefix, and on
Fern's backslash-escaped form of `${…}`, which would now render literally. The
runtime knob behind the image tags is `VSS_CONTAINER_TAG`; the markers exist
only for the docs.

## Local preview

```bash
fern login   # NVIDIA org auth is required for global-theme: nvidia
SITE="$(mktemp -d)/fern-site"
python3 .github/scripts/build_fern_site.py --no-releases --output "$SITE"
(cd "$SITE" && fern docs dev)
```

Open `http://localhost:3000/vss`. `--no-releases` builds only your checkout,
including uncommitted edits, as Dev. Drop it to include every listed release
tag that exists locally (add `--fetch` to download missing ones). To preview a
release branch the way readers will see the release, pass `--release X.Y.Z`.
The same tree runs the checks CI runs:

```bash
(cd "$SITE" && fern check && fern docs md check && fern docs broken-links --strict)
```

## Previews and publishing

There is one Fern site, production. Everything else is a named preview, a
stable URL that each run updates in place.

| Event | Workflow | Result |
|-------|----------|--------|
| Pull request | `fern-docs-ci.yml` | Checks, then preview `pr-<N>`, linked in a PR comment. A PR into `release/X.Y.Z` previews as that release |
| Push to `release/X.Y.Z` | `preview-release-docs.yml` | Preview `release-X-Y-Z` (`https://nvidia-preview-release-X-Y-Z.docs.buildwithfern.com/vss`), showing "X.Y.Z (preview)" with the release values |
| Docs change merged to `develop` | `publish-fern-docs.yml` | Production rebuilt from develop; Dev updates, releases are rebuilt unchanged from their tags |
| Release tag `vX.Y.Z` pushed | `publish-fern-docs.yml` | Production rebuilt from develop with the new release as the default; fails if `ref: vX.Y.Z` is not listed on develop |

All of them need the `DOCS_FERN_TOKEN` repository or organization secret.

## Releasing a docs version

1. Cut `release/X.Y.Z` from `develop`. Every docs push to the branch refreshes
   the `release-X-Y-Z` preview.
2. On `develop`, add the release at the top of `versions:` in `fern/docs.yml`:

   ```yaml
     - display-name: "X.Y.Z"
       slug: "X.Y.Z"
       ref: vX.Y.Z
   ```

   This is safe before the tag exists: the build skips listed tags it cannot
   find, with a warning. The entry can also be added on the release branch
   and back-ported; what matters is that it is on `develop` when the tag is
   pushed, because production is always built from `develop`.
3. Push the `vX.Y.Z` tag on the release branch. Production republishes from
   `develop` with the tagged docs added, and `X.Y.Z` becomes the default
   version.

If the tag is pushed before the entry reaches `develop`, that publish fails
with a message naming the missing entry, and the version goes live when the
entry merges to `develop` (every docs merge republishes production).

A release tag's docs must have the `docs/index.yml` layout and the current
workflows, so release branches cut before this layout landed need it
back-ported before they are tagged. To retire a version, delete its entry.
Tags are immutable: a docs fix to a published release ships in a patch release
(for example `v3.3.1`), listed as its own entry.
