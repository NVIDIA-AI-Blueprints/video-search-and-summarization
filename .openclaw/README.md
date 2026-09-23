<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenClaw harness

Everything VSS needs to run on OpenClaw, in one place: Each agent harness has its own self-contained
top-level directory (`.openclaw/`, `.hermes/`); the `SKILL.md` tree at the repo
root (`skills/`) stays harness-neutral, and each directory packages it the way
that harness loads skills.

| Path | What it is |
|---|---|
| `Dockerfile` | The sandbox image: NemoClaw's published managed OpenClaw runtime (digest-pinned) + the `vss` CLI + this plugin, installed with `openclaw plugins install` |
| `plugin/` | The VSS OpenClaw plugin: `openclaw.plugin.json`, `package.json` + lockfile, `src/index.ts` (tool, workspace seeding, skill-selection shim), `stage-assets.sh` |
| `workspace/` | The OpenClaw workspace instruction files, the `_nemoclaw/` overlay for the normal durable agent, and the `_vss-ui/` overlay for isolated UI chats |

## The plugin

Declared in `plugin/openclaw.plugin.json`, built with `defineToolPlugin` from
the OpenClaw SDK:

- **`vss_cli` tool** — runs the pinned `vss` CLI with an argument array and
  returns exit code, stdout and stderr. The agent drives the VSS backends
  through a typed tool call instead of a free-form shell.
- **Skills, selected from the deployment** — the manifest points OpenClaw at
  `skills-active/`; `skills/` holds everything shipped. What is shipped, and what
  each skill needs, comes from the skills themselves: a skill whose `SKILL.md`
  frontmatter declares `metadata.vss-requires` (a `vss` command group such as
  `search`, `summarize`, `vlm`; `alerts`; or `always` for a skill every
  deployment gets, such as `vss-manage-video-io-storage`) is an operation skill
  and is staged.
  At register time, and on demand via
  `vss-openclaw-sync`, the shared `sync_skills.py` (staged from `skills/vss-build-vision-ai/scripts/` at the pinned `VSS_REF`) runs `vss configure check`, which reports
  the command groups the recorded deployment can serve (the CLI joins the routes
  it recorded, such as `lvs`, `rt_vlm`, `elasticsearch` + `rt_embed`, with what
  each group needs), probes the Alert Bridge at `<base_url>/alerts` or the
  Compose port `9080`, and copies exactly the qualifying skills into
  `skills-active/`. With no recorded deployment every shipped skill is active, so
  the agent can still run `vss configure`; `skillSelection: "all"` in the plugin
  config, or `VSS_SKILL_SELECTION=all`, forces that. Only operation skills are
  shipped: the ones that drive a live deployment through the CLI's operation
  commands, which is what the tool exposes. Deploy, benchmark and build skills
  are not in this image.
- **Workspace seeding** — at register time the plugin copies `workspace/*.md`
  into the agent's configured workspace (`agents.defaults.workspace`) when the
  files are not there yet, applying the `_<variant>` overlay first.
  `VSS_WORKSPACE_VARIANT` selects the overlay; unset, it is `nemoclaw` when
  running under NemoClaw's runtime. Existing files are never overwritten: the
  workspace is the agent's memory.

## VSS UI conversation isolation

The image keeps the normal `main` agent and its durable workspace unchanged.
It also configures a dedicated `vss-ui` agent for VSS UI traffic. That agent
uses `/sandbox/.openclaw/workspace-vss-ui`, which contains no user profile,
long-term memory, daily memory, or first-run bootstrap file. Its tool policy
denies memory lookup, other-session access, automation, shell execution, and
workspace mutation. Its only allowed tools are the workspace-scoped reader and
the typed `vss_cli` tool. Each UI conversation still gets a distinct OpenClaw
session key, so a new chat has neither transcript nor durable user context from
an earlier chat.

The VSS UI adapter always routes OpenClaw traffic to this `vss-ui` agent. An
OpenClaw deployment connected to the VSS UI must therefore define that agent;
the adapter never falls back to the durable `main` agent.

### Working on it

```
cd .openclaw/plugin
npm ci && npm run build          # type-check and compile against the pinned OpenClaw SDK
npm run stage                    # stage skills/ and workspace/ from this checkout
```

`dist/`, `node_modules/`, `skills/`, `skills-active/` and `workspace/` under
`plugin/` are build products and are not committed. The manifest must list every tool in
`contracts.tools`. Keep `openclaw` a devDependency (release-matched) and
peerDependency, never a dependency: the image build prunes it so the installed
plugin links to the image's own runtime, and fails if that link is missing.

To use the plugin with a desktop OpenClaw instead of the sandbox image:

```
cd .openclaw/plugin && npm ci && npm run prepare-local
openclaw plugins install "$PWD" && openclaw plugins enable vss
openclaw skills list | grep vss-
```

## The image

The layout is NemoClaw's documented custom-image workflow (NemoClaw docs →
*Install OpenClaw Plugins*): name the completed managed runtime
`nemoclaw-runtime`, build the plugin from its lockfile in a separate stage,
install it as the sandbox user, refresh the managed config hash, end with
`USER sandbox`. The doc builds the runtime from NemoClaw's stock Dockerfile with
a version-matched NemoClaw checkout as context; that Dockerfile copies from a
dozen places in the NemoClaw tree and cannot live here, so this image starts
from the image that Dockerfile produces, which NemoClaw publishes to
`ghcr.io/nvidia/nemoclaw/openclaw-sandbox`.

Skills and the `vss` CLI come from one pinned commit of this repo (`VSS_REF`),
so they always match. `nvidia-vss` is on no reachable index, so the checkout
stage builds it as wheels and the runtime installs those by name — no VSS
source ships in the image. The workspace files come from this directory.

```
docker build -t <registry>/vss-harness-openclaw:<tag> .openclaw
```

**Dev loop — build from the local checkout instead of the pin.** NemoClaw's
`onboard --from` fixes the build context to this directory, so the Dockerfile
cannot COPY the repo's `skills/` directly; the pinned fetch is how it normally
gets them. To put uncommitted local edits into the image, pre-stage a
working-tree snapshot (skills, workspace docs, `vss` CLI source) into
`.openclaw/.vss-src/` — the next build uses it instead of fetching `VSS_REF`:

```
python3 skills/vss-build-vision-ai/scripts/stage_vss_src.py           # stage (both harnesses)
python3 skills/vss-build-vision-ai/scripts/stage_vss_src.py --clean   # back to the pin
```

The snapshot is git's view of the tree (tracked + untracked-unignored files),
and the image logs its provenance (`sha`, `dirty`, pretend version) from the
snapshot's `STAGED` marker at build time. Gitignored, never committed;
shipping builds stay on the pin.

The eval harness's Provision panel does the same: it shows this Dockerfile,
lets an operator edit it for a variant experiment, and builds a
content-addressed image with this directory as context. Real changes belong in
a PR here.

Pins are build args:

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE` | `ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:25f4…` (v0.0.114, the release `deploy_nemoclaw.ipynb` installs) | the managed runtime |
| `OPENCLAW_VERSION` | `2026.7.1` | the OpenClaw the base carries; the build fails if the plugin lockfile pins a different one |
| `VSS_REPO`, `VSS_REF` | this repo, a commit sha | the skills and the `vss` CLI |
| `BUILDER_IMAGE` | `node:22-trixie-slim@sha256:db8a…` | the plugin build stage (same as NemoClaw's) |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv@sha256:2bb3…` (0.12.10) | uv, for the `vss` venv |
| `NEMOCLAW_TOOL_DISCLOSURE` | `progressive` | NemoClaw tool disclosure mode |

Moving `BASE_IMAGE` to another NemoClaw release means moving `OPENCLAW_VERSION`
to the OpenClaw that release pins and regenerating the plugin lockfile
(`npm install --package-lock-only` in `plugin/` after editing its
`devDependencies.openclaw`). NemoClaw's doc is explicit that a plugin image must
not mix one release's runtime with another's OpenClaw.

### Trial paths

`WORKDIR /sandbox` makes `/sandbox` the OpenShell workspace, which the sandbox
user owns and `openshell sandbox download` serves. Harbor and its agent adapters
address `/task /output /logs /tests /solution` by name; the image pre-creates
them as real, world-writable directories. They cannot be symlinks into the
workspace: OpenShell chowns every `read_write` policy path at sandbox start and
refuses a symlink. The eval harness moves files in and out with exec + tar, so
they do not need to live in the workspace.

## One image, one harness

The image installs exactly one agent runtime and declares it with
`LABEL harness.agent=openclaw`, so an eval run pairs `(image, agent)`
unambiguously. Harbor (the eval orchestrator) runs **outside** the sandbox and
supplies the command; the image inherits NemoClaw's `nemoclaw-start` entrypoint
and gateway health check.
