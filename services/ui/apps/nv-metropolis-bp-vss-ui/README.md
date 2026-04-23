<!-- SPDX-License-Identifier: MIT -->
Introduction
============

This is the UI for the NV Metropolis BP VSS application.

Getting Started
===============

Pre-run installation:

At root of the repo, run:

```bash
npm install
```

Setup environment variables. Copy or create a `.env` file in this app directory. For a full list of supported environment variables and a sample `.env`, see **[DOCKER-README.md](../../DOCKER-README.md)** at the repository root (section *".env sample to use for docker run when running the Metropolis BP VSS UI app"*).

Run the application:

In dev mode:
```bash
npx turbo dev --filter=./apps/nv-metropolis-bp-vss-ui
```

In production mode (full production build, then production server):

From repo root, build all packages, build this app (including the standalone `bundle` step), then start the production server:
```bash
npx turbo build --filter=./packages/** \
  && npx turbo run build bundle --filter=./apps/nv-metropolis-bp-vss-ui \
  && npx turbo start --filter=./apps/nv-metropolis-bp-vss-ui
```

The Turbo task `bundle` (repo root `turbo.json`, plus this app’s `turbo.json`) runs the `bundle` npm script here and copies `.next/static` and `public/` into the standalone output expected by `npm run start`.

The application will be available at `http://localhost:3000`


