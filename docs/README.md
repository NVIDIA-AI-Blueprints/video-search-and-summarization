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
cd fern
fern login   # NVIDIA org auth is required for global-theme: nvidia
fern docs dev
```

Open `http://localhost:3000/vss`.

## Stage

Staging uploads the checked-out tree to a persistent Fern instance. It does not update `docs.nvidia.com`.

Staging URL: https://nvidia-vss-staging.docs.buildwithfern.com/vss

### From the CLI

```bash
cd fern
fern login
fern generate --docs --instance nvidia-vss-staging.docs.buildwithfern.com/vss --force
```

### From GitHub Actions

1. Open the repository on GitHub.
2. Click **Actions**.
3. Select **Stage Fern Docs**.
4. Click **Run workflow**.
5. Choose the branch you want to stage.
6. Click **Run workflow** again.
7. After the job succeeds, open https://nvidia-vss-staging.docs.buildwithfern.com/vss

Do not run **Publish Fern Docs** to stage. That workflow publishes production.

## CI

GitHub Actions under `.github/workflows/` run `fern check`, MDX safety, PR previews, staging, and production publish. Preview comments, staging, and live publish need the `DOCS_FERN_TOKEN` repository or organization secret.
