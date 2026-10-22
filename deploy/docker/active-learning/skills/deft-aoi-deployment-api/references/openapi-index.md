# OpenAPI Index

Services are included here only when the Blueprint Builder microservice metadata marks them with `has_openapi_endpoint=True`, or when a backward-compatible known-service fallback was needed.

| Service | Base URL | OpenAPI fetched | Reference | Detection |
|---|---|---|---|---|
| `deft-service` | `http://127.0.0.1:${DEFT_SERVICE_PORT:-9150}` | no | not generated | has_openapi_endpoint |

For API questions, fetch the live OpenAPI spec from the target service's `BASE_URL/openapi.json` and derive endpoint paths, methods, required parameters, and request bodies from that spec. If no base URL is resolved here, resolve it from the live deployment: use Kubernetes Service `nodePort`/LoadBalancer details when a namespace is known, use a provided `COMPOSE_PATH` with `docker compose port` for Compose deployments, or ask the user for the service base URL.
