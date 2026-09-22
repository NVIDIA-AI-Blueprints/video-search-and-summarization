# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# VSS documentation (Fern)

Author MDX in `docs/`. Fern configuration lives in `fern/` only.

| Path | Role |
|------|------|
| `docs/` | MDX pages (landing page is `index.mdx`) |
| `docs/assets/images/` | Images referenced as `/assets/images/...` |
| `fern/docs.yml` | Site config and sidebar navigation (`path: ../docs/...`) |
| `fern/fern.config.json` | Fern organization and CLI version |

`fern/assets` is a symlink to `docs/assets` so existing `/assets/...` links resolve.

## Local preview

```bash
fern login   # NVIDIA org auth is required for global-theme: nvidia
FERN_INPUT_PARENT="$(mktemp -d)"
FERN_INPUT_ROOT="${FERN_INPUT_PARENT}/fern-input"
python3 .github/scripts/prepare_fern_inputs.py --output "${FERN_INPUT_ROOT}"
(cd "${FERN_INPUT_ROOT}" && fern docs dev)
```

Open `http://localhost:3000/vss`.

### Environment-variable syntax

Fern performs build-time substitution for <code>&#36;{NAME}</code> expressions. Most such
expressions in these docs are literal shell, Compose, or configuration examples,
so keep them in their runnable source form:

```mdx
${VSS_DATA_DIR}
```

`.github/scripts/prepare_fern_inputs.py` copies Fern's inputs to a temporary tree
and escapes literals there immediately before validation or publication. Never
commit Fern's backslash-escaped literal syntax to `docs/`; commands copied from authored
MDX must remain runnable.

Build-time documentation variables use a shell default in authored MDX:

```mdx
git checkout "${VSS_DOCS_GIT_REF:-develop}"
```

When the variable is unset, preparation writes the default into the temporary
tree. When it is set, preparation converts the expression to Fern's
<code>&#36;{VSS_DOCS_GIT_REF}</code> form and Fern interpolates the supplied value. This keeps
raw commands executable and makes local, staging, and release builds
configurable. The allowlisted variables and defaults are:

| Variable | Default |
|----------|---------|
| `VSS_DOCS_GIT_REF` | `develop` |
| `VSS_DOCS_IMAGE_TAG` | `develop-latest` |
| `VSS_DOCS_SBSA_IMAGE_TAG` | `develop-latest-sbsa` |

All build-time variables must use the `VSS_DOCS_` prefix and be explicitly
added to `.github/scripts/check_fern_substitutions.py`. Export overrides before
running `prepare_fern_inputs.py` locally. The Fern GitHub Actions workflows set
these values statically in their top-level `env` blocks. Release branches must
update those workflow values to the checkout reference and image tags published
for that branch.

## Stage

Staging uploads the checked-out tree to a persistent Fern instance. It does not update `docs.nvidia.com`.

Staging URL: https://nvidia-vss-staging.docs.buildwithfern.com/vss

### From the CLI

```bash
fern login
FERN_INPUT_PARENT="$(mktemp -d)"
FERN_INPUT_ROOT="${FERN_INPUT_PARENT}/fern-input"
python3 .github/scripts/prepare_fern_inputs.py --output "${FERN_INPUT_ROOT}"
(cd "${FERN_INPUT_ROOT}" && fern generate --docs --instance nvidia-vss-staging.docs.buildwithfern.com/vss --force)
```

### From GitHub Actions

1. Open the repository on GitHub.
2. Click **Actions**.
3. Select **Stage Fern Docs**.
4. Click **Run workflow**.
5. Choose the branch you want to stage.
6. Click **Run workflow** again.
7. After the job succeeds, open https://nvidia-vss-staging.docs.buildwithfern.com/vss

The same workflow also runs when a pull request that touches `docs/` or `fern/` is merged into `develop`. It does not run when a pull request is closed without merging, and it does not publish production.

Do not run **Publish Fern Docs** to stage. That workflow publishes production.

## CI

GitHub Actions under `.github/workflows/` run `fern check`, MDX safety, PR previews, staging, and production publish. Preview comments, staging, and live publish need the `DOCS_FERN_TOKEN` repository or organization secret.
