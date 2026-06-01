<!--
SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# VIOS UI

The VIOS web UI. This directory contains two packages:

- `streaming-lib/` — `vst-streaming-lib`, the WebRTC streaming library.
- `vios-ui/` — `vst-ui-ts`, the React + Vite frontend (consumes `vst-streaming-lib`).

## Prerequisites

- Node.js 18+ (developed against Node 22)
- npm

## Build

`vios-ui` depends on the local `streaming-lib` package. Link and build it first:

```bash
cd vios-ui
npm install
npm run install:link   # builds streaming-lib and links it as vst-streaming-lib
npm run build          # outputs to vios-ui/dist
```

`install:link` builds `../streaming-lib` and symlinks it into `vios-ui/node_modules`,
so it only needs to be re-run when the streaming library changes.

## Configuration

Backend endpoints and ports are defined in `vios-ui/src/config.tsx`. By default the UI
talks to the host it is served from; edit this file if the backend ports differ:

- `mdatWebApiEndpoint` — MDAT web API port (default `8081`)
- `analyticsUIServerEndpoint` — analytics UI server port (default `8003`)
- `enableLogs` — toggle console logging

## Develop

```bash
cd vios-ui
npm run dev            # Vite dev server (default: http://localhost:5173)
```

## Build streaming-lib on its own

```bash
cd streaming-lib
npm install
npm run build          # outputs to streaming-lib/dist
```

## Deploy

The ingress container serves the UI as static files. After building, copy the
contents of `vios-ui/dist` into `services/vios/deployment/scaling/ingress/vst-ui`,
then rebuild the ingress container so it picks up the new assets.

```bash
# from services/vios
rm -rf deployment/scaling/ingress/vst-ui/*
cp -r ui/vios-ui/dist/* deployment/scaling/ingress/vst-ui/
# then rebuild the ingress container (see deployment/scaling/ingress/Dockerfile)
```
