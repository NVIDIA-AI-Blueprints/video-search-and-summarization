<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

Introduction
============

This is the standalone VSS chat UI. It renders the shared `@nv-metropolis-bp-vss-ui/chat` panels (main and sidebar) and proxies turns through this app's same-origin `/api/chat` route so agent backend addresses never reach the browser.

Getting Started
===============

Pre-run installation:

At root of the repo, run:

```bash
npm install
```

Setup environment variables. Copy or create a `.env` file in this app directory. Server-only backends used by `/api/chat`:

```
VSS_CHAT_BACKEND_MAIN=http://127.0.0.1:9097/chat/stream
VSS_CHAT_BACKEND_SIDEBAR=http://127.0.0.1:9098/chat/stream
```

Those are the local defaults when the variables are unset. For Docker, see **[DOCKER-README.md](../../DOCKER-README.md)** at the UI workspace root (section *".env sample to use for docker run when running the Metropolis BP VSS UI app"*).

Run the application:

In dev mode:
```bash
npx turbo dev --filter=./apps/vss-chat
```

In production mode (full production build, then production server):

From repo root, build all packages, build this app, then start the production server:
```bash
npx turbo build --filter=./packages/** \
  && npx turbo run build --filter=./apps/vss-chat \
  && npx turbo start --filter=./apps/vss-chat
```

Unlike `nv-metropolis-bp-vss-ui`, this app has no Turbo `bundle` step; `next start` serves the build on the port below.

The application will be available at `http://localhost:3100`
