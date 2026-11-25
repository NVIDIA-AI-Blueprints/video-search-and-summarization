# Decision-model alerts UI

This is an independent Next.js application for the decision-model alerts example.
It does not copy, patch, or build the core VSS UI. The preserved monitoring
components, server-side API proxy, and regression tests live here.

## Develop and test

Use Node.js 22.23.3 and run from this directory:

```bash
npm ci
npm test
npm run typecheck
npm run build
```

All direct dependency versions are pinned. The app uses React, Next.js, and
Tabler icons already used in VSS; Next.js is pinned to 16.4.6 to include the
November security fixes. Its package manifest and lockfile are independent of
the core UI.

To run locally, set `D1_AGENT_ENABLED=true` and configure the server-only
`D1_INGEST_URL`, `D1_ANALYTICS_URL`, and `D1_ELASTICSEARCH_URL` for your example
services, then run `npm run dev` (development) or `npm start` (after building).
Without the enable flag, the proxy returns 404. Upstream URLs are never provided
by browser requests. This example does not implement a user login; use the
loopback-only deployment and SSH tunnel documented in the parent README.

Docker Compose builds this directory directly. `bash build.sh` is also available
for an explicit image build. The image runs as the non-root `node` user on port
3000, and its build runs the tests and type checks before compiling the app.

## Optional embedding

`/?embed=1` hides the duplicate VSS header while preserving the monitoring UI.
`&theme=light` selects the light theme; the default is dark. The response allows
only same-origin framing through its `frame-ancestors` policy.

For a future same-origin extension tab, build with
`--build-arg NEXT_PUBLIC_BASE_PATH=/examples/decision-model-alerts` and route
that entire prefix, including assets and `/api`, to this service without
stripping it. Open `/examples/decision-model-alerts?embed=1`. The prefix is a
build-time setting and must match the deployment route. A generic VSS extension
tab is a separate integration; this app remains usable at `/` without it.
