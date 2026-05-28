# CLAUDE.md

Agent guide for the `video-analytics-api` repo — a Node.js/Express REST API server that retrieves video analytics data (behaviors, events, incidents, tracking, alerts, and metrics) from Elasticsearch, with optional Kafka integration for notifications and real-time stream processing.

Read this before editing. For human docs, start at `README.md` -> `readmes/`.

## Orientation (1-minute read)

- **Application**: `src/app/` — Express server. Entry point is `index.js`, bootstrap in `initializers/` (server, elastic, kafka, cache, routes, cron).
- **Controllers**: `src/app/controllers/rest-apis/` — one file per endpoint, auto-discovered by `initializers/routes.js` via `require-dir`. Filename determines URL path (camelCase -> kebab-case).
- **Core library**: `src/web-api-core/` — the `@nvidia-mdx/web-api-core` package. Organized by concern: `Services/` (business logic + ES queries), `Metrics/` (aggregation/KPI computation), `Utils/` (Elasticsearch client, Validator, Kafka, Cache), `Errors/` (custom HTTP error types), `schemas/ajv/` (JSON schemas for validation).
- **Configs**: `configs/default-configs/config.json` — loaded by `server.js` via `--config` CLI arg. Sample configs in `configs/sample-configs/`.
- **Tests**: `test/unit-test/` (mirrors `src/` layout), `test/integration-test/` (Docker Compose driven with Elasticsearch + API container).
- **OpenAPI spec**: `src/app/specification/openapi.json` — update when adding or modifying endpoints.

## Rules to follow when editing

### Always
- **SPDX header** on every new `.js` file (copy from any existing module). Copyright year = current year. NVIDIA Apache-2.0.
- **`'use strict';`** at the top of every new `.js` module, after the SPDX header.
- **Logging**: use winston (configured in `initializers/server.js`) or structured JSON (`console.log(JSON.stringify({...}))`). Never bare `console.log('debug message')` in production code.
- **`const` by default**, `let` only when reassignment is needed. Never `var`.
- **Error handling**: use `mdx.Utils.Utils.expressAsyncWrapper` in controllers to catch async rejections. Throw custom error types from services (`BadRequestError`, `InvalidInputError`, `ResourceNotFoundError`, `InternalServerError`).
- **AJV validation**: validate all inputs via `Validator.validateJsonSchema` before querying Elasticsearch.
- **Imports**: explicit `require()` only. No dynamic requires outside of `routes.js` auto-discovery.

### Never
- **Don't edit `src/web-api-core/schemas/ajv/*.json`** unless you understand the downstream validation impact — these schemas are used by both the API and integration tests.
- **Don't add bare `console.log()`** for debugging that ships — use structured JSON or winston.
- **Don't catch bare `catch(e) {}`** — catch specific errors, log them, and re-throw or return an appropriate HTTP error.
- **Don't mutate query templates** — always `deepcopy(template)` before modifying (see Services pattern).

### House style
- Module-level constants in `UPPER_SNAKE_CASE`. Classes `PascalCase`. Functions/vars `camelCase`.
- One file per controller, one file per service, one file per metric class.
- Private methods use ES2022 `#` prefix (e.g., `#getDataFromEs`).
- Keep controllers thin — validation and business logic belong in Services.
- `expressAsyncWrapper` + `return next()` in every controller handler.
- Service public methods accept `(documentDb, input)` and switch on `documentDb.getName()` for database abstraction.

## Common commands

```bash
# Install (dev) — two-step: core library first, then app
cd src/web-api-core && npm install
cd ../app && npm install --save ../web-api-core && npm install

# Start the server
cd src/app && npm start                                           # default config
cd src/app && npm start -- --config /absolute/path/to/config.json  # custom config

# Unit tests
cd test && npm install --save ../src/web-api-core && npm install --save ../src/app && npm install
cd test && npm test

# Unit tests with coverage
cd test && npm run coverage

# Integration test
cd test/integration-test && ./test.sh dev    # local
cd test/integration-test && ./test.sh prod   # CI

# Docker build
docker build -t video-analytics-api -f docker/Dockerfile .

# Docker run
docker run --network=host video-analytics-api

# Health check
curl -sf http://localhost:8081/livez && echo "OK" || echo "DOWN"
```

## Where to find things

For a full module-by-module map of `src/`, see **`readmes/modules-overview.md`** — the authoritative module map. Use this quick table for common tasks:

| Need to... | Read |
|---|---|
| Understand server startup lifecycle | `src/app/initializers/server.js`, `src/app/index.js` |
| Implement an open-ended feature end-to-end | Invoke the `implement-feature` skill |
| Add a new REST API endpoint | `src/app/controllers/rest-apis/` + `implement-feature` skill (has scaffold) |
| Add a new service class | `src/web-api-core/Services/` + export from `Services/index.js` |
| Add a new metric class | `src/web-api-core/Metrics/` + export from `Metrics/index.js` |
| Change a config field | `configs/default-configs/config.json` + `readmes/configuration.md` |
| Add an Elasticsearch index mapping | `src/web-api-core/Utils/Elasticsearch.js` (index registry) |
| Add input validation schema | Inline AJV schema in the service method (codebase convention) |
| Add a Kafka producer/consumer | `src/app/initializers/kafka.js` + `src/web-api-core/Utils/Kafka.js` |
| Understand caching | `src/app/initializers/cache.js` + `src/web-api-core/Utils/Cache.js` |
| Update the OpenAPI spec | `src/app/specification/openapi.json` |
| Debug deployment / Docker issues | Invoke the `deploy-debug` skill |
| Run the full test pipeline (unit + integration) | Invoke the `run-test` skill |

## Deeper docs

- Modules overview: `readmes/modules-overview.md`
- Configuration guide: `readmes/configuration.md`
- Docker build/deploy: `readmes/docker.md`
- Testing: `readmes/testing.md`
- Test README: `test/README.md`

## Pre-flight checklist for any change

Before reporting a task complete:

1. **Syntax-check** touched files: `node -c <path>`.
2. **Run unit tests**: `cd test && npm test` — all pass, no regressions.
3. **Coverage on new code** — any new or modified file must be exercised by a test. Add new files to `test/coverage-setup.js`. Measure with `cd test && npm run coverage`.
4. **Match the surrounding style** — don't reformat unrelated lines.
5. **Keep SPDX headers** and `'use strict';` on any new or rewritten `.js` file.
6. **No stray `console.log()`** or empty `catch {}` blocks.
7. **Export new modules** — services from `Services/index.js`, metrics from `Metrics/index.js`.
8. **Update OpenAPI spec** if you added or modified an endpoint.
9. **Update `readmes/modules-overview.md`** if you added or renamed files under `src/`.
