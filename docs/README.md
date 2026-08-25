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

## CI

GitHub Actions under `.github/workflows/` run `fern check`, MDX safety, previews, and publish. Preview comments and live publish need the `DOCS_FERN_TOKEN` repository or organization secret.
