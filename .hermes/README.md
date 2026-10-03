<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Hermes harness

Everything VSS needs to run on Hermes, in one place: Each agent harness has its own self-contained
top-level directory (`.openclaw/`, `.hermes/`); the `SKILL.md` tree at the repo
root (`skills/`) stays harness-neutral, and each directory packages it the way
that harness loads skills.

| Path | What it is |
|---|---|
| `Dockerfile` | The sandbox image: NemoClaw's managed Hermes runtime (digest-pinned) + the `vss` CLI + the VSS operation skills (activated per deployment, see below) + the workspace docs |
| `Dockerfile.base`, `base-config.py` | The base image: the same runtime with no skills or VSS CLI, for evaluating an agent users extend (see [Base image](#base-image)) |

Hermes has no plugin or tool layer to add: it drives the deployment through the
`vss` CLI on `PATH`, loads skills from `$HERMES_HOME/skills`
(`/sandbox/.hermes/skills`, per NemoClaw's `agents/hermes/manifest.yaml`), and
reads its instruction docs (`SOUL.md`, `AGENTS.md`, `TOOLS.md`, `ENV.md`, …)
from `/sandbox`. The image puts each in place:

- **Skills** — every skill in the pinned checkout whose `SKILL.md` frontmatter
  declares `metadata.vss-requires` (the same rule the OpenClaw plugin uses),
  shipped in Hermes' bundled library as `/opt/hermes/skills/vss/`. Every
  `hermes chat` copies that library into `$HERMES_HOME/skills/`, so the skills
  appear under whatever `HERMES_HOME` the agent runs with. Unlike the OpenClaw image, all
  of them are active; each skill begins with `vss configure check` and reports
  what the deployment cannot serve.
- **Workspace docs** — `.openclaw/workspace/*.md` with the
  `_nemoclaw/` overlay applied, flattened into `/sandbox/`, exactly where
  `deploy_nemoclaw.ipynb` used to upload them for Hermes. The docs are shared
  with the OpenClaw harness and come from the pinned checkout, not from this
  directory, so there is one copy to maintain.
- **`vss` CLI** — from the same pinned commit, in its own venv
  (`/usr/local/vss`), never touching `/opt/hermes/.venv`.

## The base image

NemoClaw publishes the complete Hermes image, `ghcr.io/nvidia/nemoclaw/hermes-sandbox`,
by publication cohort only — no release tag
([NVIDIA/NemoClaw#11228](https://github.com/NVIDIA/NemoClaw/issues/11228)). The
cohort a release selects is the one its `openclaw-sandbox` release tag is
labeled with:

```
docker buildx imagetools inspect ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.127 --format '{{json .}}' \
  | jq -r '.image | to_entries[0].value.config.Labels["io.nvidia.nemoclaw.managed-image.cohort"]'
# ghrun-35246345308-1
docker buildx imagetools inspect ghcr.io/nvidia/nemoclaw/hermes-sandbox:cohort-ghrun-35246345308-1
# Digest: sha256:26899bd0…  ← BASE_IMAGE
```

Move `BASE_IMAGE` together with `../openclaw/Dockerfile`'s and the notebook's
`NEMOCLAW_INSTALL_REF`: NemoClaw requires the CLI and the images to be one
release.

## Building

```
git archive HEAD:.hermes | docker build -t <registry>/vss-harness-hermes:<tag> -
```

`deploy_nemoclaw.ipynb` does this through `nemohermes onboard --from
.hermes/Dockerfile` when `AGENT_RUNTIME=hermes`. The eval harness's
Provision panel lists this Dockerfile next to the OpenClaw one.

From a clean build context, the build packages skills and CLI wheels from one
ref of this repo (`VSS_REF`: `develop` by default; CI builds the GHCR image
with `VSS_REF=<commit being built>`, and a rebuild is triggered by changes under
`.hermes/`, `skills/`, `libs/vss` or `.openclaw/workspace`). The wheels are versioned by `hatch-vcs` from the nearest `v*`
tag, so `vss --version` in the sandbox matches the agent's `GET /api/v1/version`
for that commit. Only the installed CLI, skills, workspace instructions, and
runtime helpers enter the final image; its build-stage source checkout does
not. At runtime, use `/usr/local/bin/vss` and the installed Hermes skills.
The archive includes only committed files, so local build inputs cannot override
the source ref. To build a published or reproducible image, add
`--build-arg VSS_REF=<v* tag or commit sha>` to the archived-context build above.

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE` | `ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:2689…` (v0.0.127 cohort) | the managed runtime |
| `VSS_REPO`, `VSS_REF` | this repo, `develop` (a `v*` tag for a published image; a commit sha still works) | skills, workspace docs and the `vss` CLI |
| `BUILDER_IMAGE` | `node:24.18.1-trixie-slim@sha256:ac39…` | the checkout stage |

## Trial paths and sandbox contract

Identical to the OpenClaw image: `/task /output /logs /tests /solution` as real
world-writable directories, NemoClaw's per-shell `ulimit -u` hooks removed,
`CMD sleep infinity` so an exec-driven sandbox stays up, final `USER sandbox`
and `WORKDIR /sandbox`. `LABEL harness.agent=hermes`.

## Skill discovery

Same machinery as the OpenClaw plugin, same file: the image carries
`skills/vss-build-vision-ai/scripts/sync_skills.py` (staged from the pinned
`VSS_REF` checkout) at `/opt/vss-skills/sync_skills.py`, with the shipped
skill set read-only under `/opt/vss-skills/skills/`. Hermes loads skills
from `$HERMES_HOME/skills` only, and seeds it from its bundled library
(`/opt/hermes/skills`) on every `hermes chat`, tracking what it copied in
`.bundled_manifest`. The image fills `/opt/hermes/skills/vss/` from the
shipped set, so no caller has to run anything first, whichever `HERMES_HOME`
it picks. `vss-hermes-sync` passes `$HERMES_HOME/skills/vss` as
`--active-dir` (`/sandbox/.hermes/skills/vss` when `HERMES_HOME` is unset), so
it never touches Hermes' own skills, and Hermes does not re-add a skill that
`vss-hermes-sync` deselected. Stdlib-only python; the same file
serves the OpenClaw plugin and host tooling, with its behavior pinned by unit
tests beside it.

At build, `--all` activates every shipped skill. After `vss configure` records
a deployment, run `vss-hermes-sync` in the sandbox to re-select: each skill's
`vss-requires` frontmatter is matched against `vss configure check` (plus the
alert-bridge probe), exactly like `vss-openclaw-sync`.

## Base image

`Dockerfile.base` builds NemoClaw's managed Hermes runtime with nothing VSS
added, for evaluating an agent that users extend with their own skills and
plugins. CI publishes it as `ghcr.io/nvidia-ai-blueprints/vss/vss-harness-hermes`
with the `-base` tag suffix (`develop-latest-base`, `develop-<sha12>-base`, …).

| | |
|---|---|
| Tools | `terminal`, `process`, `read_file`, `write_file`, `patch`, `search_files`, `skills_list`, `skill_view`, `skill_manage`, `execute_code` |
| Skills | none: Hermes' bundled library (`/opt/hermes/skills`) is emptied |
| Instruction docs | one empty `/sandbox/AGENTS.md`; `SOUL.md` empty; `HERMES_ENVIRONMENT_HINT` cleared |
| VSS | no `vss` or NGC CLI |

Every other toolset, NemoClaw's plugin toolsets included, is switched off by
name (`agent.disabled_toolsets` in `base-config.py`); the build fails unless the
agent resolves to exactly the tools above. Nothing is allowlisted, so toolsets a
user adds later stay on. Memory and tool search are off, so the prompt carries no
memory and a plugin's tools are sent to the model directly.

To extend it in an evaluation:

- **Skills** — copy the skill directory to `$HERMES_HOME/skills/<name>/`
  (`/sandbox/.hermes/skills/` by default). The skills tools stay on, so Hermes
  lists it in the prompt.
- **Plugins** — install into `$HERMES_HOME/plugins/<name>/` and
  `hermes plugins enable <name>`; its toolset is on by default.

```
docker build -f .hermes/Dockerfile.base -t <registry>/vss-harness-hermes:<tag>-base .hermes
```
