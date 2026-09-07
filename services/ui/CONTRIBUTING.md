<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: MIT AND Apache-2.0
-->
# Contributing to the VSS UI

Follow the repository-wide [contributor guide](../../CONTRIBUTING.md) for the
license, SPDX-header, DCO, and pull-request requirements. In particular, new
contributions under `services/ui/` are accepted under Apache-2.0 even though
retained upstream-derived files remain under MIT.

Run UI commands from this directory:

```bash
npm ci
npm test
npm run typecheck
npx turbo run build bundle --filter=./apps/nv-metropolis-bp-vss-ui
```

Do not add or update dependencies without the required license review. The
workspace-level development and build commands are documented in
[README.md](README.md).
