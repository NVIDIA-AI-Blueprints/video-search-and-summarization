# Tests

This directory contains unit tests for the `web-api-core` module and the `app` REST API controllers.

## Prerequisites

- Node.js (v18 or higher recommended)
- npm

## Installation

Navigate to the test directory and install dependencies:

```bash
cd test
npm install
```

## Running Tests

### Run All Tests

```bash
npm test
```

This will execute all test files in the `unit-test/` directory.

### Run Tests with Coverage

```bash
npm run coverage
```

This will:
1. Run all unit tests
2. Generate coverage reports in multiple formats (JSON, HTML, LCOV, Text)
3. Display a coverage summary showing total percentages

### View Coverage Summary Only

```bash
npm run coverage:summary
```

This displays only the total coverage percentages without re-running tests.

## Coverage Reports

After running coverage, reports are generated in the `coverage/` directory:

| Format | Location | Description |
|--------|----------|-------------|
| HTML | `coverage/index.html` | Interactive coverage report viewable in browser |
| JSON | `coverage/coverage-final.json` | Machine-readable coverage data |
| LCOV | `coverage/lcov.info` | Format compatible with CI/CD tools |
| Text | Console output | Summary displayed in terminal |

## Test Structure

```text
test/
├── package.json
├── README.md
├── coverage-setup.js           # Imports all modules for coverage tracking
├── unit-test/
│   ├── fixtures/               # Test data files
│   │   ├── behavior.json
│   │   ├── calibration.json
│   │   ├── road-network.json
│   │   └── ...
│   ├── app/                    # App unit tests
│   │   ├── controllers/rest-apis/
│   │   │   ├── alerts.test.js
│   │   │   ├── behavior.test.js
│   │   │   ├── clustering.test.js
│   │   │   ├── config.test.js
│   │   │   ├── events.test.js
│   │   │   ├── frames.test.js
│   │   │   ├── incidents.test.js
│   │   │   ├── livez.test.js
│   │   │   ├── metrics.test.js
│   │   │   ├── sensor.test.js
│   │   │   └── tracker.test.js
│   │   └── initializers/
│   │       └── cache.test.js
│   └── web-api-core/           # Core library tests
│       ├── Errors/
│       │   ├── IndexNotFoundError.test.js
│       │   ├── ResourceNotFoundError.test.js
│       │   └── ServiceUnavailableError.test.js
│       ├── Metrics/
│       │   ├── Behavior.test.js
│       │   ├── LastProcessedTimestamp.test.js
│       │   ├── Occupancy.test.js
│       │   ├── SpaceUtilization.test.js
│       │   └── TripwireEvent.test.js
│       ├── Services/
│       │   ├── Alerts.test.js
│       │   ├── Behavior.test.js
│       │   ├── Calibration.test.js
│       │   ├── Clustering.test.js
│       │   ├── ConfigManager.test.js
│       │   ├── Events.test.js
│       │   ├── Frames.test.js
│       │   ├── Incidents.test.js
│       │   ├── MTMC.test.js
│       │   ├── NotificationManager.test.js
│       │   ├── Place.test.js
│       │   ├── RoadNetwork.test.js
│       │   ├── Sensor.test.js
│       │   └── UsdAssets.test.js
│       └── Utils/
│           ├── Config.test.js
│           ├── Database.test.js
│           ├── Elasticsearch.test.js
│           ├── FileUploadHandler.test.js
│           ├── Histogram.test.js
│           ├── Kafka.test.js
│           ├── MessageBroker.test.js
│           ├── utils.test.js
│           └── Validator.test.js
```

## Test Approach

### Unit Tests

Unit tests focus on testing individual functions and classes in isolation using mocks and stubs.

- **web-api-core tests**: Test the core library services, metrics, utilities, and error classes
- **app controller tests**: Test controller logic using `proxyquire` to mock dependencies (elastic, kafka, cache)

### Controller Testing Pattern

Controller tests use `proxyquire` to inject mock dependencies and `supertest` to simulate HTTP requests:

```javascript
const sinon = require('sinon');
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

const mockElastic = {
    getName: () => 'Elasticsearch',
    getClient: () => ({}),
    getConfigs: () => new Map([['indexPrefix', 'mdx-']])
};

const mockMdx = {
    Services: {
        MyService: sinon.stub().returns({
            myMethod: sinon.stub().resolves({ data: 'result' })
        })
    },
    Utils: {
        Utils: {
            expressAsyncWrapper: (fn) => async (req, res, next) => {
                try { await fn(req, res, next); }
                catch (error) { next(error); }
            }
        }
    }
};

const controller = proxyquire('../path/to/controller', {
    '@nvidia-mdx/web-api-core': mockMdx,
    '../../initializers/elastic': mockElastic
});
```

## Example Output

```text
  1100 passing (18s)

=============================== Coverage summary ===============================
Statements   : 92.50% ( 4350/4703 )
Branches     : 82.00% ( 1620/1976 )
Functions    : 93.00% ( 247/265 )
Lines        : 92.60% ( 4325/4670 )
================================================================================
```

## npm Scripts Reference

| Script | Description |
|--------|-------------|
| `npm test` | Run all unit tests |
| `npm run coverage` | Run tests with coverage report |
| `npm run coverage:summary` | Display coverage summary only |

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `mocha` | Test framework |
| `chai` | Assertion library |
| `sinon` | Mocking/stubbing library |
| `nyc` | Code coverage tool (Istanbul) |
| `proxyquire` | Module mocking for dependency injection |
| `supertest` | HTTP assertion library for testing Express routes |
| `express` | Web framework (used in controller tests) |
