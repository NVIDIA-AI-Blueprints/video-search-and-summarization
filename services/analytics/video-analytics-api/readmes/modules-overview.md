# Modules Overview

## Repository Structure

```
.
├── configs/                          # Application configuration files
│   ├── default-configs/
│   │   └── config.json               # Default bootstrap configuration
│   └── sample-configs/
│       └── calibration.json          # Sample calibration config
├── docker/
│   ├── Dockerfile                    # Multi-stage Docker build
│   └── Dockerfile.dockerignore       # Dockerfile-specific build-context exclusions
├── src/
│   ├── app/                          # Express application
│   │   ├── controllers/rest-apis/    # REST API route handlers
│   │   ├── initializers/             # Server bootstrap (server, elastic, kafka, cache, cron, routes)
│   │   ├── specification/            # OpenAPI specification files
│   │   ├── index.js                  # Application entry point
│   │   └── package.json
│   └── web-api-core/                 # Shared library (@nvidia-mdx/web-api-core)
│       ├── Errors/                   # Custom error types
│       ├── Metrics/                  # Metric computation classes
│       ├── Services/                 # Data access and business logic
│       ├── Utils/                    # Utilities (Config, Elasticsearch, Kafka, Validator)
│       ├── schemas/                  # AJV and protobuf schemas
│       ├── queryTemplates/           # Elasticsearch query templates
│       ├── index.js                  # Package entry point
│       └── package.json
├── test/
│   ├── unit-test/                    # Unit tests (mocha + chai + sinon)
│   ├── integration-test/             # Integration tests (Docker Compose + HTTP assertions)
│   ├── coverage-setup.js             # Pre-loads modules for coverage
│   └── package.json                  # Test dependencies and nyc config
├── readmes/                          # Additional documentation
├── .gitlab-ci.yml                    # GitLab CI pipeline
├── Jenkinsfile.develop.multiarch     # Jenkins multi-arch build pipeline
└── README.md
```

## web-api-core

The `@nvidia-mdx/web-api-core` package provides shared functionality used by the app:

- **Errors** - Custom error types (BadRequest, ResourceNotFound, InvalidInput, etc.)
- **Metrics** - Metric computation (Behavior, Occupancy, SpaceUtilization, TripwireEvent)
- **Services** - Data access layer (Alerts, Behavior, Calibration, ConfigManager, Events, Frames, etc.)
- **Utils** - Infrastructure utilities (Config, Database, Elasticsearch, Kafka, Validator)

## app

The Express application that exposes REST API endpoints. Key initializers:

- **server.js** - Main bootstrap: loads config, configures Express, starts HTTP server
- **elastic.js** - Elasticsearch client initialization
- **kafka.js** - Kafka client initialization
- **routes.js** - REST API route registration
- **cache.js** - In-memory cache (node-cache)
- **cron.js** - Scheduled tasks

## Docker

The `docker/Dockerfile` uses a multi-stage build:

1. **Builder stage** - Installs dependencies for `web-api-core` and `app`
2. **Runtime stage** - Copies built artifacts and `configs/` into a distroless Node.js image

The `docker/Dockerfile.dockerignore` file excludes git metadata, Jenkins files, runtime upload files, and the governance OpenAPI spec from the build context used by `docker/Dockerfile`.
