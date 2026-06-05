# Testing

## Unit Tests

Unit tests use mocha, chai, sinon, and proxyquire. They are located in `test/unit-test/` and mirror the source structure.

### Running Unit Tests

```bash
cd src/web-api-core
npm install

cd ../app
npm install --save ../web-api-core
npm install

cd ../../test
npm install --save ../src/web-api-core && npm install --save ../src/app
npm install
npm test
```

### Running with Coverage

```bash
cd test
npm run coverage
```

Coverage reports are generated in `test/coverage/` using nyc (Istanbul).

### Coverage Setup

`test/coverage-setup.js` pre-loads all source modules so they appear in the coverage report even if not directly exercised by tests. Controllers are loaded with proxyquire to mock runtime dependencies (Elasticsearch, Kafka).

## Integration Tests

Integration tests use Docker Compose to stand up Elasticsearch and the video-analytics-api container, then run HTTP assertions against the live API.

### Running Integration Tests

```bash
cd test/integration-test
./test.sh dev
```

Modes:

- `dev` - Local development (default)
- `prod` - CI environment

### What Integration Tests Cover

1. Elasticsearch ingest pipeline setup
2. Data dump loading
3. REST API endpoint validation (CRUD operations, query parameters, error handling)
4. Schema validation of request fixtures against AJV schemas
5. Warehouse 2D and 3D application-specific tests

### Docker Compose Structure

- `docker_compose/infra/` - Elasticsearch infrastructure
- `docker_compose/apps/` - video-analytics-api service definition
- `docker_compose/apps_data/` - Persistent data directories

## CI Pipelines

### GitLab CI (`.gitlab-ci.yml`)

- **unit-test** - Runs unit tests with coverage
- **integration-test** - Docker-in-Docker integration tests
- **pages** - JSDoc generation for `web-api-core`
- **api-governance-linter** - OpenAPI specification linting

### Jenkins (`Jenkinsfile.develop.multiarch`)

- npm packaging and test coverage
- Integration tests
- npm publish to URM
- Multi-arch Docker image builds
