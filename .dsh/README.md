<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# DeepSeek Harness (dsh) harness

VSS on [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`), an
open-source, Cordis-based "everything is a plugin" agent harness from DeepSeek AI.

> [!NOTE]
> DeepSeek Harness is in developer preview. Expect compatibility-breaking changes
> between releases.

A thin OpenShell sandbox. `dsh` ships no official Docker image and has no NemoClaw
sandbox of its own, so `Dockerfile.base` owns the whole base layer: a plain pinned
Node image, `npm install -g @deepseek-ai/dsh`, the `vss` CLI (same wheel-install
pattern as `../.hermes`), and the `vss_cli` tool plugin baked directly into the
`headless` profile. The OpenShell supervisor is injected into the pod by OpenShell, so
all the image adds for it is its contract: `iproute2` (the supervisor refuses to start
a sandbox without `ip`) and the non-root `sandbox` user with `HOME=/sandbox`. The
generic `openshell-community/sandboxes/base` also meets that contract but is ~3.4 GB,
two-thirds of it other agents' CLIs that dsh never runs; this image is ~1.1 GB
(~450 MB compressed).

## The `headless` profile

`dsh`'s `headless` profile is a one-shot runner: `dsh --profile headless "<task>"` runs
one task, prints the final answer to stdout, and exits — no server, no GUI, no open
port. That's the entrypoint this image is built and tested against; it is the CLI-driven
shape, closer to Hermes than to OpenClaw's persistent gateway. Other `dsh` profiles
(`web`, `sdk`, `acp`, …) are not wired into this image and were not tested.

## The `vss_cli` tool plugin

`plugin/` is a Cordis plugin package (`@nvidia-vss/dsh-plugin`), registered the same way
every first-party `dsh` tool package is — `export function apply(ctx)` calling
`ctx.tools.register(defineTool({...}))` (see `dsh`'s own
`packages/shell/tool-bash` for the reference shape; there is no separate plugin SDK
beyond Cordis itself). It mirrors the OpenClaw plugin
(`../.openclaw/plugin/src/index.ts`) and the Hermes plugin
(`../.hermes/plugin/__init__.py`) exactly: same tool name (`vss_cli`), same
`{args, cwd, timeoutSec}` parameter schema, the pinned `vss` CLI run as a no-shell
subprocess (`node:child_process.execFile`, never a shell string), the same
200,000-char per-stream output cap, and the same
`{command, exitCode, signal, timedOut, stdout, stderr, truncated}` result shape. A skill
written against "the vss_cli tool" works unchanged on any of the three harnesses.

Cordis composes a profile from layered config: each bundle in the profile's listed
order, then the profile's own `cordis.patch.yml`, then a home-level one, then any
`--patch` overlay. The build bakes our plugin in at the profile layer — it adds
`@nvidia-vss/dsh-plugin` as a `file:` dependency of `$DSH_HOME/profiles/headless/package.json`
and an `insert:` row to that profile's `cordis.patch.yml` — so it reaches every
`dsh --profile headless` invocation under this image's `$DSH_HOME`
(`/sandbox/.dsh`, i.e. `$HOME/.dsh` with `HOME=/sandbox`) with no further setup. This
covers the `headless` profile specifically, not every profile `dsh` ships.

## Memory

`dsh` has no memory subsystem comparable to OpenClaw's `memory-core` plugin or Hermes'
`memory_enabled`/`user_profile_enabled` toggle — a real `npm install @deepseek-ai/dsh`
tree carries no `@deepseek-ai/dsh-*memory*` package. There is nothing to turn on here.

## Build

```
docker build -f .dsh/Dockerfile.base -t <registry>/vss-harness-dsh:<tag> .dsh
```

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE`, `BUILDER_IMAGE` | `node:24.18.1-trixie-slim@sha256:ac39…` | the Node runtime (same pin as `../.hermes`'s builder stages) |
| `VSS_REPO`, `VSS_REF` | this repo, `develop` | the `vss` CLI only; this base ships no skills |

The `dsh` CLI itself — developer preview, move deliberately — is pinned in
`cli/package.json`/`cli/package-lock.json`, not a build arg: `npm ci` installs
exactly what the lockfile says, so a second pin here would just be a second
source of truth that could drift from it. Bump the version by editing
`cli/package.json` and regenerating `cli/package-lock.json`
(`npm install --package-lock-only` in `.dsh/cli/`). The lockfile also makes
the CLI's own dependency tree visible to the OSRB license scanner, which
reads `package.json`/`package-lock.json` by filename — a bare `npm install -g`
would not be.

## Verification

The build-time `RUN` that bakes the plugin into the `headless` profile also proves it
actually mounts — `--dump-config` alone is not enough (it composes the config rows
without instantiating any plugin, so a plugin `apply()` error never surfaces there; this
caught a real `schema.required is not supported` bug during development). Instead the
build runs one real `dsh --profile headless` task with no model credentials configured,
and asserts the failure is `MISSING_CREDENTIAL` — i.e. every plugin, including ours,
loaded cleanly and boot reached the point of calling a model — not a plugin-activation
failure (`did not activate`, `JsonSchemaError`, a missing module) before that point.

| | |
|---|---|
| CLI | `dsh` (`/usr/local/bin` via npm global install), `vss` |
| Tool plugin | `vss_cli`, baked into the `headless` profile's `$DSH_HOME` |
| Skills | none |
| Memory | not applicable — `dsh` has no memory subsystem |
| Profile | `headless` only |

## Usage

```
openshell sandbox create --from dsh   # if layered on an OpenShell sandbox image
# or directly:
docker run --rm --entrypoint /bin/bash <image> -lc 'dsh --profile headless "run the tests"'
```

Not tested yet: an arm64 build, and a full agent run against a live model (no
credentials are configured in the image itself by design).
