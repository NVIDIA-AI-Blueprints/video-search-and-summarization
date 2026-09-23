# Version convention: one version per commit, from git tags

Companion to [`github-first.md`](github-first.md) (how a module joins the GHCR
build). This page records how anything in this repository gets a version, and
why. Every rule below has one input: **git**. Nothing is typed into a file and
read back as fact.

Decided September 2026 in
[#2373](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/pull/2373),
on top of the version endpoint from #2241 / #2290.

---

## The invariant

For any commit on `develop`, these agree, and none is hand-written:

| Surface | Reads | Example at develop head today |
|---|---|---|
| `GET /api/v1/version` on a deployment (the HAProxy edge, Compose and Helm) | `VSS_VERSION` in `containers.env` / `vssVersion` in the profile chart's values, stamped by CI | `3.3.0-rc0` |
| `GET /api/v1/version` at the agent's own origin | installed `nvidia-vss-core` metadata | `3.3.0-rc0+tree.<sha>` (image) |
| `vss --version` (CLI) | installed `nvidia-vss-cli` metadata | `3.3.0-rc0+tree.<sha>` (harness image), `3.3.0-rc0.dev.N+g<sha>` (checkout) |
| `SKILL.md` `metadata.version` (skills) | the file, stamped by CI | `3.3.0-rc0` |

"Agree" means the release line and pre-release match. The agent and CLI carry
extra build metadata (`+tree.<sha>`, `+g<sha>`) that identifies *which build*;
the stamped fields carry none, because their identity is the commit they ship
in.

## Tags

| Tag | Meaning | Who pushes it |
|---|---|---|
| `vX.Y.Z` | a release | maintainers, manually, **after** stamping (below) |
| `vX.Y.ZrcN` (also `aN`, `bN`) | develop now targets X.Y.Z; annotated, on develop | maintainers, once per line, when the line starts |
| `nightly-*`, `dev-*`, `pr*`, anything else | bookmarks for other jobs | not a version input; **invisible to every rule here** |

Everything derives from `git describe --tags --match 'v[0-9]*'`: the nearest
`v*` tag *reached*, never the next one. Develop reports `3.2.1-…` until
`v3.3.0rc0` exists and `3.3.0-rc0…` until `v3.3.0` does, because a release is
not a fact before its tag. The current line's tag: `v3.3.0rc0` on
`4c432f24cf`.

A tag must be a bare PEP 440 release or pre-release (`v3.3.0`, `v3.3.0rc0`).
Anything carrying its own `+local` segment collides with the image stamp and
fails the build.

## Package versions (hatch-vcs)

Every `pyproject.toml` (`libs/vss/core`, `libs/vss/cli`, `services/agent`,
`services/agent/packages/vss_agents`) uses `hatchling` + `hatch-vcs` with the
same describe and `version_scheme = "no-guess-dev"`:

| Checkout state | hatch-vcs emits (PEP 440) | rendered as SemVer |
|---|---|---|
| exactly on `v3.3.0rc0` | `3.3.0rc0` | `3.3.0-rc0` |
| 20 commits past it | `3.3.0rc0.post1.dev20+g73f7244` | `3.3.0-rc0.dev.20+g73f7244` |
| exactly on `v3.3.0` | `3.3.0` | `3.3.0` |
| dirty tree | `…+g<sha>.d<date>` / `….dirty` | build metadata, kept |

`vss_core.version.pep440_to_semver` does the rendering; precedence comes out
right without a comparator (`3.3.0-rc0.dev.20` < `3.3.0-rc0` < `3.3.0`). The
wire contract stays strict SemVer 2.0.0 (`SEMVER_PATTERN`, duplicated
byte-for-byte in `services/agent/scripts/check_vss_version.py`).

## Images

An image build has no `.git`, so `build-dev-images.yml` derives the release
line itself (`git describe --tags --abbrev=0 --match 'v[0-9]*'`) and passes

    VSS_PACKAGE_VERSION=<release line>+tree.<source tree sha>

as `SETUPTOOLS_SCM_PRETEND_VERSION`. Two properties, both deliberate:

- **release segment from the tag** — changes only when a tag is pushed, so an
  image and a checkout install of one commit agree;
- **build metadata from the source tree, never the commit** — the workflow
  re-tags an already-published manifest onto a later commit whose tree is
  identical, and a commit-derived stamp would go stale the moment that reuse
  fired.

A bare `docker build` with no build arg stamps `0.0.0+local`: valid SemVer that
satisfies no skill's range and cannot be mistaken for a shipped build.

**The source tree sha covers every path the Dockerfile `COPY`s.** An
inventory entry's `source_path` is one path or a list
(`["services/agent", "libs/vss"]`); the hash is `git rev-parse <commit>:<path>`
for one path and one `git mktree` over all of them otherwise
(`check_container_tag_source.source_tree_sha`, asked via
`release_set.py tree-sha`). A path a Dockerfile reads but the entry omits is a
stale-image bug; `test_detect_changed_images.py` audits for it.

**Registry tags are names for builds, not versions.** `develop-<sha12>`,
`tree-<sha>`, `develop-latest` on GHCR and `X.Y.Z` on NGC identify manifests;
the version lives inside the image and is what the image reports.

## The deployment's version: the edge answers

`GET /api/v1/version` on a deployment is answered by the **HAProxy edge**, on
every profile, whether or not the agent is deployed — so a lean LVS stack, a
warehouse profile with no agent route and a full base stack all report one.
The edge returns `{"service":"vss","version":"<stamped>"}` from a value CI
wrote into the deployment files from the git tag:

| Path | Where the value lives | How it reaches HAProxy |
|---|---|---|
| Compose | `deploy/docker/containers.env`: `VSS_VERSION="3.3.0-rc0"` (no `${…:-}` default, so no shell variable overrides it) | `compose.yml` passes `VSS_VERSION` into the `vss-haproxy-ingress` container; `haproxy.cfg.template` returns `%[env(VSS_VERSION)]` for `/api/v1/version` before `/api` is routed to the agent |
| Helm | `values.yaml` of every chart that owns `templates/vss-ingress.yaml` (the four developer profiles, the three warehouse apps): `vssVersion: "3.3.0-rc0"` | the ingress template renders it into a `haproxy.org/frontend-config-snippet` `http-request return`, scoped to the ingress host |

The agent's own `/api/v1/version` remains — reachable at the agent's origin and
from a checkout — and reports the installed `nvidia-vss-core` version with its
`+tree.<sha>`. Same release line, same contract; the edge is the deployment's
answer, the agent's is the agent's.

**Deployments report, they do not decide.** There is **no**
`VSS_DEPLOYMENT_VERSION` override and **no** runtime `git describe` anywhere: a
version anyone can edit at deploy time is one nobody can trust, and a version
derived from whatever checkout a process sits in is not the version of the code
that was installed. A wrong image stamp is fixed by rebuilding; a wrong edge
value is fixed by the stamp run. `VSS_AGENT_VERSION` is an image tag and a
telemetry label; it is not a version.

The compatibility checker (`check_vss_version.py`, `requires-vss` in the
benchmark skills) and `vss configure check` read the edge's answer and compare
`X.Y.Z` only: a prerelease of X.Y.Z counts as X.Y.Z, so `3.3.0-rc0` satisfies
`>=3.3.0,<4.0.0`.

## Stamped fields

`.github/scripts/stamp_versions.py` writes the release line **with** its
pre-release marker (`3.3.0-rc0`) and nothing after it into every hand-readable
field, from the nearest `v*` tag:

- `skills/**/SKILL.md` — `metadata.version`;
- `deploy/docker/containers.env` — `VSS_VERSION="…"`;
- `values.yaml` of every chart with a `templates/vss-ingress.yaml` — `vssVersion: "…"`
  (a chart that renders the route but declares no key fails the run — the
  field is required, never invented).

`version-stamp.yml` runs it on every `v*` tag push and every merge to
`develop` and **commits straight to `develop`**. Ordinary merges are no-ops;
the number moves only when a tag moves the line. `develop` admits pushes only
from the `vss-admins` team (ruleset *Protect develop*), so the workflow pushes
with a fine-grained PAT of a member — repository secret
`SKILLS_VERSION_PUSH_TOKEN`, sign-off from repository variables
`SKILLS_VERSION_GIT_NAME` / `SKILLS_VERSION_GIT_EMAIL`. Without them it stops
with a clear error and pushes nothing.

**The gate:** `stamp_versions.py --check` runs on every PR (`ci.yml`, job
*Container Coordinates Golden*, on a full-depth checkout so the tags are
reachable) and fails when any stamped field disagrees with the nearest `v*`
tag, or a skill / ingress chart lacks its field. A hand edit therefore cannot
merge; the fix is `python3 .github/scripts/stamp_versions.py`, never the
number. One known cost: a PR opened before a new `v*` tag lands fails the gate
until it is rebased onto develop — once per line. The compliance checker
(`vss-playbook-compliance`) additionally accepts pre-release skill versions.

### Release order: stamp first, tag second

Tags are immutable. The pipeline can make `develop` agree the moment a tag
lands; it can never make the *tagged commit* agree afterwards. Release tags are
cut by maintainers, so:

```bash
python3 .github/scripts/stamp_versions.py --version 3.3.0   # before the tag exists
git commit -s -am "chore(version): stamp 3.3.0 for the release"
# land that commit on develop, then
git tag -a v3.3.0 <that commit> && git push origin v3.3.0
```

The tag-triggered run then finds the tagged tree already agreeing and does
nothing. If a `v*` tag is pushed onto an unstamped commit, that tree ships
skills and edge values saying the previous line forever. The run still stamps
develop, and says so in three places: the **run's annotation** (Actions → the
`Version Stamp` run for the tag; also on the tag commit's checks), the **run's
step summary**, and the log. It does not fail and does not post elsewhere.
Release deployments and harness images are then built from the develop commit
the run pushed, not from the tag.

## Harness images (OpenClaw, Hermes)

`.openclaw/Dockerfile` and `.hermes/Dockerfile` fetch one ref of this repo,
`VSS_REF`: `develop` by default, a `v*` tag for a published image
(`--build-arg VSS_REF=v3.3.0`), a commit sha for a reproducible rebuild. The
two defaults must match and must be `develop` or a `v*` tag
(`test_sync_skills.py`). The fetch is a `tree:0` partial clone with tags, so
hatch-vcs versions the CLI wheels inside from the same describe and
`vss --version` in the sandbox equals the deployment's answer for that commit.

## Docs

`publish-fern-docs.yml` derives `VSS_DOCS_GIT_REF` / `VSS_DOCS_IMAGE_TAG` /
`VSS_DOCS_SBSA_IMAGE_TAG` from the release that triggered it (`v3.3.0` →
`v3.3.0` / `3.3.0` / `3.3.0-sbsa`); a manual run may name a tag; a pre-release
tag is refused because no image is published under it. PR previews and staging
are develop and stay `develop` / `develop-latest`. These are checkout refs and
registry tags, not package versions — the rc marker never appears in docs.

## What is deliberately out of scope

- **`Chart.yaml` `version:`** is the chart artifact's version, hand-bumped at
  the start of a line, and is **not** what a deployment reports: ci-vss-oss's
  `package_dev_profiles.py` rewrites it at packaging time (`3.3.0-nightly-…`
  for nightlies, the tag for releases) and keeps only its `X.Y.Z` core, so it
  cannot carry the pre-release marker. The edge reads `vssVersion` from the
  chart's values, which packaging leaves untouched. `test_agent_config_defaults.py`
  still requires the chart version to be strict SemVer.
- `requires-vss` ranges in the benchmark skills: owned by each skill's author;
  a new VSS line does not widen them.
- Registry tags (`develop-latest`, `develop-<sha12>`, `tree-<sha>`, NGC
  `X.Y.Z`): names for image manifests, produced by `build-dev-images.yml` and
  the GitLab promotion, derived from commits and trees, not from the version.
- Version-shaped image *coordinates* that stay hand-pinned because the images
  are built elsewhere: `vssAgentVersion: "3.3.0-65576357eb80"` (NGC staging
  tag), `VSS_AUTO_CALIBRATION_TAG=3.3.0-6`, `VSS_REID_EMBED_TAG=3.3.0-26.09.2`.
- The NGC release image tag format assumed by the docs publish (`X.Y.Z`,
  `X.Y.Z-sbsa`) was inferred from `containers.env` conventions when this was
  written; confirm against the promotion pipeline before the first release
  build relies on it.
