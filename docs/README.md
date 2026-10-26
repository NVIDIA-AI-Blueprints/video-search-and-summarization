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
| `fern/docs.yml` | Site config and the list of published versions |
| `fern/redirects.yml` | Redirect rules (see [Redirects](#redirects)) |
| `fern/versions/` | Static versions: `archives.yml` and `archives.mdx` are the Archives version, links to earlier releases' documentation |
| `fern/fern.config.json` | Fern organization and CLI version |
| `.github/scripts/build_fern_site.py` | Builds the Fern input tree for every version |
| `.github/scripts/check_fern_site.sh` | Runs the Fern checks on a built tree before any publish |
| `.github/scripts/publish_fern_docs.sh` | Publishes a built tree to production or a preview, with retries |

## Page URLs

`docs/index.yml` sets no `slug:` except the root page's `/`. Fern derives
each URL segment from the page or section title in kebab case, splitting
letters from digits and camel-case words (`NemoClaw` becomes `nemo-claw`,
`2D Vision AI Profile` becomes `2-d-vision-ai-profile`). Moving a file on disk
never changes a URL; **renaming a title or moving an entry in the navigation
does.** When a published page's URL changes, add a redirect from the old URL
(see [Redirects](#redirects)) and update the links to it. `fern check` and
`fern docs broken-links --strict` report both.

## Links and images

Internal links are site paths without the `/vss` base and without a version
slug, for example `[Quickstart](/getting-started/quickstart)`. Fern adds the
base and the reader's version, so a link on a Dev page stays in Dev and a link
in a release stays in that release. Link to the version's home page with `/`
(or `/#anchor`). Images use a path relative to the page. Each version's
documentation is self-contained and should not link to other documentation
versions.

The build rejects links that name the `/vss` base (including bare `/vss`,
`href=` and `src=` with either quote, and reference definitions) and images
rooted at `/assets/`. These fail the build in the checkout and in the release
being published by a tag push. Older listed tags cannot change, so for them
the build only warns.

## How the site is built

The site at `docs.nvidia.com/vss` has one version per release plus **Dev**
and **Archives**:

| Version | Content from | URL |
|---------|--------------|-----|
| Latest release (first listed) | its `vX.Y.Z` tag | `/vss/...` (default) |
| Older releases | their `vX.Y.Z` tags | `/vss/X.Y.Z/...` |
| Dev | the latest commit on `develop` | `/vss/dev/...` |
| Archives | `fern/versions/archives.*` on `develop` | `/vss/archives` |

`fern/docs.yml` lists them. Each release is a `ref:` entry naming its release
tag, newest first; Dev is the `path: ../docs/index.yml` entry, the checkout
being built. Archives is a static `path:` entry: a one-page navigation,
`fern/versions/archives.yml`, that links to the documentation of releases
published before this site. It is built as-is from `develop` (never from a tag,
never stamped), so update `fern/versions/archives.mdx` there when a release is
archived. Keep it last, so it is never the default:

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
  - display-name: Archives
    slug: archives
    path: ./versions/archives.yml
```

Fern never reads that file directly. `build_fern_site.py` writes a temporary
tree in which every version is a prepared copy: the checkout for Dev, and
`git archive <tag>` for each release. In every copy it fills in that version's
values (below). It does not rewrite links or images; Fern scopes them to each
version, and the build enforces the authoring rule (see
[Links and images](#links-and-images)). It then rewrites the `versions:` block
of the temporary `docs.yml` to those copies. Every Fern command runs inside the
temporary tree; the build never modifies the checkout.

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

Nightly previews use the selected `nightly-*` tag for `VSS_DOCS_GIT_REF`,
`develop-<SHA12>` for `VSS_DOCS_IMAGE_TAG`, and `develop-<SHA12>-sbsa` for
`VSS_DOCS_SBSA_IMAGE_TAG`. SHA12 is the first 12 characters of the tagged
commit, matching the immutable GHCR candidate selected by the nightly.
Annotated tags are resolved to their commit before deriving these values.

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
The same tree runs the checks every docs workflow runs before it publishes:

```bash
(cd "$SITE" && "$OLDPWD/.github/scripts/check_fern_site.sh")
```

## Previews and publishing

There is one Fern site, production. Everything else is a preview; PRs and
release branches get a named preview, a stable URL that each run updates in
place. Every workflow runs `check_fern_site.sh` (`fern check`, `fern docs md
check`, `fern docs broken-links --strict`) on the exact tree it is about to
publish, and the published URL is in the job summary.

| Event | Workflow | Result |
|-------|----------|--------|
| Pull request | `fern-docs-ci.yml` | Checks, then preview `pr-<N>`, linked in a PR comment. A PR into `release/X.Y.Z` previews as that release. A manual run without a PR gets a preview ID Fern generates |
| Push to `release/X.Y.Z` | `preview-release-docs.yml` | Checks, then preview `release-X-Y-Z`, showing "X.Y.Z (preview)" with the release values |
| Tag `nightly-*` pushed | `preview-nightly-docs.yml` | Checks, then preview named after the tag, showing "nightly-* (preview)" as the default version, with pinned nightly values |
| Docs change merged to `develop` | `publish-fern-docs.yml` | Production rebuilt from develop; Dev updates, releases are rebuilt unchanged from their tags |
| Release tag `vX.Y.Z` pushed | `publish-fern-docs.yml` | Production rebuilt from develop with the new release as the default; fails if `ref: vX.Y.Z` is not listed on develop |

All of them need the `DOCS_FERN_TOKEN` repository or organization secret.

Nightly previews use the tagged docs and version list. Rerunning a tag updates
the same preview URL; find the link in the **Preview Nightly Docs** job summary.
The workflow and builder must be present in the tagged commit, so new tags
gain automatic previews after this tooling reaches `develop`. Tag pushes must
use a credential that triggers workflows (a PAT or GitHub App token, rather
than a workflow's `GITHUB_TOKEN`). To rerun a supported tag manually:

```bash
gh workflow run preview-nightly-docs.yml --ref nightly-20261025
```

For a local nightly build, check out the tag and pass
`--nightly nightly-20261025` to `build_fern_site.py`. Nightly tags do not need
entries in `fern/docs.yml` and do not add versions to the production site.

## Redirects

All redirects live in `fern/redirects.yml`, which `fern/docs.yml` references
with `redirects: ./redirects.yml`. Production always takes them from
`develop`, for every version; a tag supplies only its `docs/`.

- The first matching rule wins: specific rules go before generic ones.
- A `source` omits the `/vss` base; a `destination` includes it.
- A source must never be a page the published site serves. Fern would apply
  the redirect and hide the page, so a rule for a URL that is still live in the
  default version waits until that version changes (see
  [Refreshing a release's docs](#refreshing-a-releases-docs)).
- Use one path segment per parameter (`:page`, `:v`), never `[^/]+` or
  `(.*)`; Fern does not match or substitute them. A source without a
  parameter is matched literally, so pattern rules need one.
- Every rule points at a real page in one hop; never chain redirects.

The file covers:

| Rules | From | To |
|-------|------|----|
| Old slugs | URLs that changed when slugs began to come from titles (`/dev/…`, `/nightly/…`, and after a refresh `/…` and `/3.3.0/…`) | the page's current URL |
| Nightly | `/vss/nightly` and `/vss/nightly/<path>`, from the first Fern previews | the Dev version, `/vss/dev/<path>` |
| Legacy Sphinx pages | `/vss/<file>.html` and `/vss/{latest,3.1.0,3.2.0,3.2.1}/<file>.html`, for every file in `docs/index.yml` | the page in the default version |
| Other legacy `latest/` URLs | `/vss/latest` and anything else under it | the home page, `/vss` |
| Aliases | short paths such as `/vss/kafka`, and slugs from the first Fern previews | the page in the default version |

The `latest/` rules take effect only once `docs.nvidia.com` stops redirecting
`/vss/latest/*` to `/vss` at the edge, which today happens before Fern sees the
request. Legacy URLs for pages that are not in `docs/index.yml` (2.x, the 3.0.0
sub-sites, removed pages) are not redirected yet.

`fern docs dev` cannot test redirects: it answers every URL with 200. On a
preview, read only the first hop of a URL; a rule that fires answers `308`
with a `Location` header:

```bash
curl -s -o /dev/null -D - "https://<preview>/vss/3.2.1/quickstart.html"
```

Keep to a few requests at a time; bursts against a preview return transient
errors.

## Releasing a docs version

Publishing a release does not require merging the release branch into
`develop`, and `develop` stays open for docs contributions throughout.
`develop` supplies only the version list in `fern/docs.yml` and the build
tooling. Each release's pages come from its own `vX.Y.Z` tag, so changes made
only on the release branch are published whether or not they ever reach
`develop`.

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
   pushed, because the publish workflow reads the version list from
   `develop`. The release's pages still come from its tag.
3. Push the `vX.Y.Z` tag on the release branch. Production republishes from
   `develop` with the tagged docs added, and `X.Y.Z` becomes the default
   version.

If the tag is pushed before the entry reaches `develop`, that publish fails
with a message naming the missing entry, and the version goes live when the
entry merges to `develop` (every docs merge republishes production).

- **Back-port before tagging.** A release tag's docs need the `docs/index.yml`
  layout, the [link and image rules](#links-and-images), and the current
  `.github/scripts/` and docs workflows. Back-port them to release branches cut
  before they landed: a branch push runs that branch's own workflows and
  builder, so an old branch is not checked by the current rules.
- **Forward-port tooling before tagging.** Tooling that lands on a release
  branch first (the build scripts, docs workflows, `setup-fern`) must also
  reach `develop` before the tag is pushed, because a tag publish runs
  `develop`'s builder. Cherry-picking those commits or opening a matching PR
  against `develop` is enough; merging the release branch is not required.
- **Retiring a version.** Delete its entry from `versions:`.
- **Fixing a published release.** Tags are immutable. A product patch release
  (for example `v3.3.1`) is listed as its own entry; a docs-only fix is a docs
  refresh (below).

## Refreshing a release's docs

A docs refresh republishes a release's documentation from a later commit on
its release branch, without a product release. It is tagged
`docs-vX.Y.Z-N` (`docs-v3.3.0-1`, then `docs-v3.3.0-2`, …). The name is not a
`v*` tag, so it is not a version input (see
[`.github/version-convention.md`](../.github/version-convention.md)), no
workflow is triggered by it, and the docs it publishes are stamped with
release `X.Y.Z`'s values. The product team pushes the tag; docs contributors
name the commit.

1. Land and validate the change on `develop` (the Dev version).
2. Port the `docs/` change to `release/X.Y.Z`, together with the
   `fern/redirects.yml` the refreshed version needs. Pushes to the branch only
   update the `release-X-Y-Z` preview, which shows the branch as the default
   version: validate the refreshed pages and their unversioned redirects there.
   Record the validated commit.
3. The product team pushes `docs-vX.Y.Z-N` on that commit. Nothing publishes.
4. Open a `develop` PR that replaces the version's `ref:` with the docs tag
   (same `display-name` and `slug`) and adds the redirects that are only safe
   once the old pages are gone. Its CI and `pr-N` preview build the complete
   site with the real tag.
5. Merge only after this go/no-go check:
   - the tag's commit is the validated commit:
     `git fetch --no-tags origin refs/tags/docs-vX.Y.Z-N:refs/tags/docs-vX.Y.Z-N`
     then `git rev-parse 'refs/tags/docs-vX.Y.Z-N^{commit}'` (this peels an
     annotated tag to its commit);
   - a strict build of the PR head passes with no `warning:` lines (no skipped
     tag, no unscoped links in the tag):
     `build_fern_site.py --fetch --require-tag docs-vX.Y.Z-N --output "$SITE"`,
     then `check_fern_site.sh` in `$SITE`;
   - no redirect source is a page the built site serves, and the preview's
     first hops land on real pages;
   - no docs change has merged to `develop` or the release branch since.

The merge publishes the refreshed version and its redirects together. Merging
before the tag exists would publish the new redirects against the old pages:
the build skips a missing tag with only a warning.
