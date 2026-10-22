---
name: deft-aoi-deployment-api
description: >
  Configure, deploy, operate, and call the deft-aoi Blueprint Builder deployment.
  Use this skill for deft-aoi, deployment configuration and status, OpenAPI discovery, calling deployment APIs, docker compose up/down/logs/status, services: deft-service. For API questions, fetch the target service's live /openapi.json and derive the exact call from the schema.
license: NVIDIA Proprietary
allowed-tools: Bash Read Write Glob
metadata:
  author: Blueprint Builder
  team: deployment-platform
  tags:
    - blueprint-builder
    - deployment
    - docker
    - openapi
    - generated
  languages:
    - bash
    - python
  domain: infrastructure
---

# deft-aoi Deployment and API

Complete single-host DEFT AOI stack assembled from AOI Active Learning, AOI Inference, Data Flywheel, and real-mode DEFT projects.

## Source

- Blueprint: `services/data-flywheel/deployment/blueprint/src/data_flywheel_deployment/blueprints/deft_aoi.py`
- Recorded compose path: `deployment-artifacts/deft-aoi-rtx600-1gpu/docker-compose.yml`
- Services: seaweedfs-master1, seaweedfs-master2, seaweedfs-master3, seaweedfs-volume1, seaweedfs-volume2, seaweedfs-volume3, seaweedfs-filer, seaweedfs-s3, seaweedfs-bucket-bootstrap, kafka, kafka-aoi-topic-bootstrap, elasticsearch, elasticsearch-aoi-template-bootstrap, kibana, logstash, backend-api, aoi-client, al-archiver, snapshot-slice-consumer, snapshot-finalizer, eval-slice-consumer, eval-summary-consumer, kpi-slice-consumer, kpi-finalizer, aoi-inference-triton, aoi-inference-api, aoi-inference, data-flywheel-api, data-flywheel-worker, data-flywheel-parent-worker, data-flywheel-beat, data-flywheel-model-manager, redis, mongodb, mlflow, deft-service
- OpenAPI services: deft-service

## Resolve Deployment Artifact

This skill may be copied to a different machine or repo path than the generated artifacts. Treat recorded paths as hints only.

Resolve the artifact for the backend the user wants to operate. Use an explicit user path first, then the matching shell variable, then the recorded path if it exists. Ask for a path if none is readable.

```bash
RECORDED_COMPOSE_PATH=deployment-artifacts/deft-aoi-rtx600-1gpu/docker-compose.yml
COMPOSE_PATH="${COMPOSE_PATH:-}"
COMPOSE_ENV_PATH="${COMPOSE_ENV_PATH:-}"
COMPOSE_SECRET_ENV_PATH="${COMPOSE_SECRET_ENV_PATH:-}"
COMPOSE_SECRET_ENV_TEMPLATE_PATH="${COMPOSE_SECRET_ENV_TEMPLATE_PATH:-}"
COMPOSE_EFFECTIVE_SECRET_ENV_PATH="${COMPOSE_EFFECTIVE_SECRET_ENV_PATH:-}"
if [ -z "$COMPOSE_PATH" ] && [ -f "$RECORDED_COMPOSE_PATH" ]; then
  COMPOSE_PATH="$RECORDED_COMPOSE_PATH"
fi
if [ -n "$COMPOSE_PATH" ] && [ -z "$COMPOSE_ENV_PATH" ]; then
  COMPOSE_ENV_PATH="$(dirname "$COMPOSE_PATH")/.env"
fi
if [ -n "$COMPOSE_PATH" ] && [ -z "$COMPOSE_SECRET_ENV_PATH" ]; then
  COMPOSE_SECRET_ENV_PATH="$(dirname "$COMPOSE_PATH")/.env.secrets"
fi
if [ -n "$COMPOSE_PATH" ] && [ -z "$COMPOSE_SECRET_ENV_TEMPLATE_PATH" ]; then
  COMPOSE_SECRET_ENV_TEMPLATE_PATH="$(dirname "$COMPOSE_PATH")/.env.secrets.example"
fi
if [ -n "$COMPOSE_PATH" ] && [ -z "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" ]; then
  if [ -f "$COMPOSE_SECRET_ENV_PATH" ] || [ -f "$COMPOSE_SECRET_ENV_TEMPLATE_PATH" ]; then
    COMPOSE_EFFECTIVE_SECRET_ENV_PATH="$COMPOSE_SECRET_ENV_PATH"
  else
    COMPOSE_EFFECTIVE_SECRET_ENV_PATH="$COMPOSE_ENV_PATH"
  fi
fi
test -f "$COMPOSE_PATH"
test -f "$COMPOSE_ENV_PATH"
```

## Sandbox and Host Access

Agents may run with filesystem, Docker, or network sandboxing. A failed `localhost`, `127.0.0.1`, or host-IP probe from the agent runtime is not enough to conclude the deployment is down.

- For API calls, first try the resolved `BASE_URL` normally. If `curl` returns connection refused, timeout, HTTP code `000`, DNS/network errors, or cannot reach a service that the user says is running, ask for host/escalated network access and retry the same non-destructive probe before reporting failure.
- For Docker Compose commands, Docker socket permission errors, missing compose network visibility, or sandbox write restrictions may require host/escalated execution. Ask the user for that access before rerunning Docker commands.
- If the tool runtime has an explicit escalation mechanism, use it with a concise justification that names the endpoint or Docker operation. If escalation is unavailable or denied, ask the user for a host-reachable service base URL or a host-shell result.
- Do not print secrets while retrying with elevated access. Treat POST API calls as workload/data-creating operations; make the target URL and payload source clear before running them when escalation is required.

## Configure

Do not hand-edit generated artifacts for lasting changes; update the blueprint or microservice definition, rebuild, then refresh this skill.

Populate these required non-secret runtime settings before validation:

| Runtime input | Services | Notes |
|---|---|---|
| `AOI_SELECTION_TAG` | data-flywheel-api, data-flywheel-beat, data-flywheel-parent-worker, data-flywheel-worker | Required AOI inspection tag used to scope the training snapshot and prevent KPI evaluation records from entering the training set. |

For Compose, set each value in `$COMPOSE_ENV_PATH`. Generated `${VAR:?VAR must be set}` interpolation makes `docker compose config --quiet` fail when a required value is missing or empty.

When Blueprint Builder supplies `.env.secrets.example`, initialize the protected runtime file first. The template contains names and descriptions only. This step does not generate secret values and does not replace the approval requirement for generated secrets.

Resolve `<skill-directory>` to the directory containing this `SKILL.md`, then run:

```bash
  SECRET_PROVISIONER="${SECRET_PROVISIONER:-<skill-directory>/scripts/provision_generated_secret.py}"
test -f "$SECRET_PROVISIONER"
if [ -f "$COMPOSE_SECRET_ENV_TEMPLATE_PATH" ]; then
  python3 "$SECRET_PROVISIONER" compose-init --template "$COMPOSE_SECRET_ENV_TEMPLATE_PATH" --env-file "$COMPOSE_SECRET_ENV_PATH"
fi
```

Populate all required entries in `$COMPOSE_SECRET_ENV_PATH` using user-supplied values or the explicitly approved generation workflow below.

Only after required inputs are populated, validate the selected backend:

```bash
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" config --quiet
```

Required or likely secret values detected from the blueprint metadata:

| Runtime input | Services | Secret ref | Required | Approved generation | Notes |
|---|---|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | backend-api, eval-slice-consumer, eval-summary-consumer, kpi-finalizer, kpi-slice-consumer, seaweedfs-bucket-bootstrap, seaweedfs-s3, snapshot-finalizer, snapshot-slice-consumer | seaweedfs-s3-credentials/access-key-id | yes | - | S3 access key. Compose defaults to a local development credential; Kubernetes requires the referenced Secret. |
| `AWS_SECRET_ACCESS_KEY` | backend-api, eval-slice-consumer, eval-summary-consumer, kpi-finalizer, kpi-slice-consumer, seaweedfs-bucket-bootstrap, seaweedfs-s3, snapshot-finalizer, snapshot-slice-consumer | seaweedfs-s3-credentials/secret-access-key | yes | - | S3 secret key. Compose defaults to a local development credential; Kubernetes requires the referenced Secret. |
| `DEFT_API_KEY` | data-flywheel-api, data-flywheel-beat, data-flywheel-parent-worker, data-flywheel-worker | data-flywheel-credentials/deft-api-key | yes | - | Credential issued by the DEFT training service. Docker reads DEFT_API_KEY; Kubernetes requires the referenced Secret. |
| `DEFT_AUTH_MODE` | deft-service | - | no/unknown | - | Require bearer tokens configured through DEFT_API_KEYS. The trusted demo mode is intentionally not exposed by this real-execution deployment. |
| `HF_TOKEN` | deft-service | hf-token/HF_TOKEN | no/unknown | - | Optional Hugging Face token forwarded to TAO stage containers when gated model assets are required. |
| `STORAGE_BACKEND_API_KEY` | data-flywheel-api, data-flywheel-beat, data-flywheel-parent-worker, data-flywheel-worker | data-flywheel-credentials/storage-backend-api-key | yes | - | Credential issued by the AOI Active Learning storage backend. Docker reads STORAGE_BACKEND_API_KEY; Kubernetes requires the referenced Secret. |

For Compose, the configuration preflight above checks required non-secret settings in `.env` and required `SecretKeyRef` settings in the protected secret env file. It fails on empty required values without printing secrets.

Configure the listed runtime inputs through the deployment platform before starting services. Outside the explicitly approved generation workflow above, never read, print, copy, or persist secret values. Approved generated values may be persisted only to the disclosed Compose secrets file.

## Deploy

Docker Compose:

```bash
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" config --quiet
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" pull
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" up -d
```

## Status, Logs, and Tear Down

Docker Compose:

```bash
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" ps
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" logs --tail 200
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" logs -f <service>
docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" down
```

Use `down -v` only when the user explicitly wants to delete named volumes and model caches.


## API Usage

For API questions, do not ask for `COMPOSE_PATH` first. Use Docker Compose only for deploy/status/logs/down or when no API base URL can be resolved any other way.

Always start API work by resolving the target service and fetching its live OpenAPI spec:

1. Identify the service by matching the user's intent to the recorded service names and descriptions.
2. Read `references/openapi-index.md` and `references/blueprint-openapi.json` for candidate base URLs and service ports.
3. Resolve `BASE_URL` in this order: an explicit URL in the user's prompt, shell `BASE_URL`, a candidate base URL from the OpenAPI index, `docker compose port <service> <service-port>` when `COMPOSE_PATH` is known, otherwise ask the user for the service base URL.
4. Fetch `curl -fsSL "$BASE_URL/openapi.json"` and inspect the returned spec before giving endpoint-specific instructions.
5. If the OpenAPI fetch fails with a connection, timeout, DNS, HTTP `000`, or Docker/sandbox visibility error, follow **Sandbox and Host Access** above before concluding the service is unavailable.
6. Choose the endpoint by matching the user's intent against `operationId`, path, tags, summary, and description.
7. Build the request body from the OpenAPI schema: include every required field, use examples/defaults/enums from the spec when present, and do not invent fields that are not in the schema. If a required field's value is user-specific, show it as a placeholder and explain what it should be.

Live OpenAPI fetch and endpoint inventory:

```bash
export SERVICE="deft-service"
export SERVICE_PORT="8000"
export HOST="${HOST:-127.0.0.1}"
export BASE_URL="${BASE_URL:-}"

if [ -z "$BASE_URL" ] && [ -f "$COMPOSE_PATH" ]; then
  PUBLISHED="$(docker compose --env-file "$COMPOSE_ENV_PATH" --env-file "$COMPOSE_EFFECTIVE_SECRET_ENV_PATH" -f "$COMPOSE_PATH" port "$SERVICE" "$SERVICE_PORT" 2>/dev/null | awk -F: 'END {print $NF}')"
  if [ -n "$PUBLISHED" ]; then
    BASE_URL="http://${HOST}:${PUBLISHED}"
  fi
fi

if [ -z "$BASE_URL" ]; then
  echo 'Set BASE_URL explicitly, or pass COMPOSE_PATH for Docker Compose.' >&2
  exit 1
fi

SPEC="/tmp/${SERVICE}.openapi.json"
curl -fsSL "$BASE_URL/openapi.json" -o "$SPEC"
python3 - "$SPEC" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1], encoding='utf-8'))
methods = {'get','post','put','patch','delete','head','options'}
def resolve_schema(schema):
    if not isinstance(schema, dict):
        return {}
    ref = schema.get('$ref')
    if ref and ref.startswith('#/'):
        node = spec
        for part in ref[2:].split('/'):
            node = node.get(part.replace('~1', '/').replace('~0', '~'), {})
        return resolve_schema(node)
    return schema
for path, item in spec.get('paths', {}).items():
    if not isinstance(item, dict):
        continue
    for method, op in item.items():
        if method not in methods or not isinstance(op, dict):
            continue
        print(f"{method.upper():6} {path} :: {op.get('operationId') or ''} :: {op.get('summary') or ''}")
        params = op.get('parameters') or []
        required_params = [p for p in params if isinstance(p, dict) and p.get('required')]
        if required_params:
            print('  required params:', ', '.join(f"{p.get('name')}({p.get('in')})" for p in required_params))
        body = ((op.get('requestBody') or {}).get('content') or {}).get('application/json', {})
        schema = resolve_schema(body.get('schema') or {})
        if schema:
            print('  required body:', ', '.join(schema.get('required') or []) or '-')
            print('  body fields:', ', '.join((schema.get('properties') or {}).keys()) or '-')
PY
```

After selecting the endpoint, call it with the exact method/path from the spec:

```bash
curl -s -X <METHOD> "$BASE_URL/<path-from-openapi>" \
  -H "Content-Type: application/json" \
  -d @payload.json | jq .
```

## Changing Topology

For topology changes, update the Blueprint connections, rebuild the deployment artifacts, redeploy, and use the regenerated skill so service and port references match the deployment.

## References

- `references/blueprint-openapi.json`: structured blueprint/OpenAPI inspection data
- `references/openapi-index.md`: API-capable services and fetched specs
- `references/api/`: per-service endpoint summaries generated from fetched OpenAPI specs
