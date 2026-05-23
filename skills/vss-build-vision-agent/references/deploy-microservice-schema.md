# `deploy-<microservice>.md` — Canonical Schema

This is the schema that every per-service `deploy-<microservice>.md` file in the VSS repo must follow. The `build-vision-agent` skill reads these files to determine deployment-time concerns: which image and tag, GPU and VRAM requirements, volumes, health checks, startup behavior, and known deployment failure modes.

**Filename convention:** `deploy-<microservice>.md` where `<microservice>` is the service name in lowercase-kebab-case, matching the corresponding `integrate-<microservice>.md`.

**Location:** `skills/<skill-folder>/references/deploy-<microservice>.md`

---

## Required Sections (in order)

### `# Deployment Reference: <Service Name>`

H1 title.

### `## Container Image`

- **Image name** — e.g., `nvcr.io/nvidia/vss-core/vss-rt-vlm`
- **Tag pattern** — versioning scheme. Document multiarch tag suffixes if applicable (e.g., `3.1.0`, `3.1.0-sbsa`).
- **Registry** — `nvcr.io`, `docker.elastic.co`, public Docker Hub, etc.
- **NGC pull requirements** — does pulling require `NGC_CLI_API_KEY` and `docker login nvcr.io`?
- **Architecture support** — x86_64, aarch64-tegra, aarch64-sbsa

### `## GPU Requirements`

- **GPU required?** — yes / no / conditional (specify the condition)
- **Minimum VRAM** — e.g., `16 GB for Cosmos Reason 8B at default batch size`
- **Supported GPU architectures** — Ampere, Hopper, Blackwell, Ada Lovelace
- **GPU count per instance** — typically 1; specify if more
- **Can share GPU with other services?** — yes/no, plus which services it shares well with (e.g., "shares cleanly with other RT-* services in `local_shared` mode")
- **Compose snippet for device reservation** — exact `deploy.resources.reservations.devices` block

### `## CPU & Memory`

- **Minimum CPU cores**
- **Minimum RAM**
- **`shm_size`** if non-default
- **`ulimits`** if non-default (memlock, stack, nofile)

### `## Storage`

Volume mounts the service requires. For each:

| Mount Path | Purpose | Type | Size estimate | Required permissions |
|---|---|---|---|---|

- **Type** = bind mount, named volume, tmpfs
- **Required permissions** — if a host bind mount needs `chmod 777` on a specific subdir, or `chown` to a specific UID, document it explicitly. If "no recursive chown" is a known footgun, flag it here.

Persistent volumes that survive `docker compose down` versus volumes that get destroyed must be called out — multi-GB cache volumes especially.

### `## Startup Behavior`

- **Expected startup time** — first-boot vs. warm-cache, with concrete numbers (e.g., "20 minutes on first boot for model download + vLLM warmup")
- **Startup ordering dependencies** — `depends_on` conditions and why
- **Health check endpoint** — exact URL and expected response
- **Health check tuning** — `interval`, `timeout`, `retries`, `start_period` values from compose
- **Log signatures of healthy startup** — phrases to grep for in logs to confirm readiness

### `## Known Deployment Issues`

Common failure modes and their fixes. Format as a table:

| Symptom | Root cause | Fix |
|---|---|---|

Each row should be specific enough that an operator can map a real error to the correct fix without guessing.

### `## Prerequisites`

External prerequisites the operator must satisfy before deploy:

- Driver version
- Docker / Compose version (note any compose-syntax features that require a minimum version, e.g., `${VAR:+:path}` conditional bind syntax)
- NVIDIA Container Toolkit
- API keys (`NGC_CLI_API_KEY`, `HF_TOKEN`, etc.)
- OS packages
- Disk space
- Network reachability (e.g., `nvcr.io`, `huggingface.co`)

---

## Optional Sections

- `## Dry Run` — non-destructive validation commands (`docker compose config`, etc.)
- `## Verify Deployment` — post-deploy probes
- `## Logs & Status` — how to tail and interpret logs
- `## Upgrade & Rollback` — image tag swap procedure
- `## Tear Down` — graceful shutdown steps including which volumes are wiped by `down -v`
- `## Gotchas & Known Issues` — quirks not big enough to be a "Known Deployment Issue" row but still worth documenting

---

## Validation Rules

The `validate-references.py` script enforces:

1. The file's H1 starts with `# Deployment Reference: `.
2. All required sections above exist as H2 headings in the listed order.
3. The Storage section is a Markdown table with at least the four required columns.
4. The Known Deployment Issues section is a Markdown table with `Symptom`, `Root cause`, `Fix` columns.
5. Required-section bodies are non-empty.

Reference files that fail validation block the PR via the CI workflow.
