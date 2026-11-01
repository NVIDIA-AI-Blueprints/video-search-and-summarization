# `incident-console-v2`: Hexagonal Layout

How `incident-console-v2` separates its UI and HTTP routes from Supabase, R2, the VLM gateway and the agent, and the import rules `npm test` enforces (`tests/import-boundaries.test.mjs`). Why it is built this way: [decision](../decisions.md#2026-10-23-incident-console-v2-uses-a-feature-module-layout-under-src).

## 1. Layout

```
incident-console-v2/src/
  app/                    # Next App Router: pages and api/**/route.ts (HTTP parsing, validation, status codes)
  sections/<feature>/     # client UI: components/<Name>/ and hooks/ (analysis, dashboard, evidence, library, reports, shell, videos)
  modules/<feature>/
    domain/               # types and port interfaces
    application/          # use cases, one folder each (for example reports/application/save-human-edit/)
    infrastructure/       # adapters: supabase/ (with mappers/), memory/ (test fakes), agent/, r2/, gateway/
  lib/                    # shared technical code: container.ts, env.ts, http.ts, postgrest/, r2/, video/, evidence/, ...
```

Modules: `analysis`, `chat`, `contract`, `dashboard`, `health`, `jobs` (the background analysis queue), `reports`, `search`, `shared` (the kernel: `RunKey`, `Page`, `StoreRejection`, `DbAdapters`) and `videos`. Tests live in `tests/` and beside their module (`src/**/*.test.mjs`); both globs run in `npm test`.

## 2. Ports and adapters

- **Composition root:** `src/lib/container.ts` is the only file that picks adapters. `getDbAdapters()` returns `{dashboard, incidents, videos}` (`DashboardRepository`, `IncidentRepository`, `VideoRepository`); `getDbHealth()` returns `HealthRepository` and never throws; `getAnalysisPorts()` returns `{runs, videos, gateway, agent}` (`AnalysisRunRepository`, `VideoObjectStore`, `ModelGateway`, `IncidentAgent`; a model port is null when its URL is not configured); `getChartInterpreter()` and `getChartRenderer()` return `ChartInterpreter` and `ChartRenderer`, or null without a gateway or agent URL; `getUploadAnalysis()` bundles them with the crop and embedding ports for `analyseUpload`, which both `POST /api/analysis` and the job loop run; `getAnalysisJobs()` returns `AnalysisJobRepository`. Adapters are built per call, so configuration is read at request time.
- **Supabase:** only `src/modules/*/infrastructure/supabase/` and `src/lib/postgrest/` know table names, filters, RPC argument names (`p_*`) and snake_case columns. The adapters and their `mappers/` map rows and RPC JSON field by field into camelCase domain types, and turn database refusals into `StoreRejection`, which routes return as 422.
- **Agent:** evidence search (`EvidenceSearchRepository`, `src/lib/adapters/agent/search.ts`), revision search (`RevisionSearchRepository`, `src/lib/adapters/agent/revision-search.ts`), chat (`src/modules/chat/infrastructure/agent/agent-chat.ts`) and evidence embeddings (`src/modules/reports/infrastructure/agent/evidence-embeddings.ts`). The console embeds nothing itself; the HTTP contracts are in [`../vm-api-contracts.md`](../vm-api-contracts.md).
- **R2:** use cases receive R2 work as functions or ports (`VideoObjectStore`, `EvidenceObjectStore`); routes that only sign or put an object call `src/lib/r2/` directly.
- **Fakes:** the analysis-run, analysis-job, dashboard, incident and video repositories have in-memory fakes under `infrastructure/memory/`, and contract suites run the same cases against the fake and the Supabase adapter (the Supabase side over `tests/support/fake-postgrest.mjs`).
- **Contract shapes:** `incident-contract-v4` types (`src/modules/contract/domain/types.ts`, `StructuredEdit`) keep their snake_case keys, because they mirror the JSON Schema shared with eval and the agent. Translation applies to database shapes only.

## 3. Dependency rules

Enforced by `tests/import-boundaries.test.mjs` (paths relative to `src/`):

1. Domain and application code (`modules/*/domain`, `modules/*/application`, `lib/application`, `lib/ports`) imports no infrastructure (`modules/*/infrastructure`, `lib/adapters`, `lib/postgrest`, `lib/r2/config`, `lib/r2/frame-source`, `lib/container`), no UI (`app`, `sections`), and not `next`, `react` or `node:*`.
2. Sections import no infrastructure and not `node:*`.
3. Each exception is listed in the test's `ALLOWED` with its reason, and an entry that matches no import fails the test. Today there are none (the upload hook's VST chunked-upload exception went with VST on 2026-10-31). Strict Step 6 (PR 236) eliminated the earlier contract loader and `node:crypto` exceptions by injecting a `ContractCatalog` port and moving `randomUUID` behind a port in `AnalysisPorts`.

Not checked by the test, upheld in review: routes reach the database only through the container, and only `lib/container.ts` constructs adapters.

## 4. Open work

- **Keys built in SQL:** report library items keep snake_case keys their RPC builds (for example `incident_type`), because the sections read them; renaming them means changing the mapper and the sections together.
