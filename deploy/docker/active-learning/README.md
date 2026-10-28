# AOI active-learning deployment

Docker Compose deployment of the DEFT AOI active-learning stack. All team images
are pulled from `nvcr.io/0691049440816435/mdx`; nothing is built from source.

## Prerequisites

- Docker with the Compose plugin
- `docker login nvcr.io` with access to the `0691049440816435/mdx` registry
- [uv](https://docs.astral.sh/uv/) (or `python3 -m pip install pytest`) to run the checks
- `.env.secrets` filled in:

  ```bash
  install -m 600 env.secrets.example .env.secrets   # then set the Required values
  ```

## Deploy and verify

```bash
./deploy-and-verify.sh
```

This runs the config checks, pulls the images, runs `docker compose up -d`, waits
for health checks and setup jobs to settle, then runs the deployment checks. It
exits 0 only when every check passes.

| Option | Use it to |
|---|---|
| `--verify-only` | Check a stack that is already running, without redeploying |
| `--no-pull` | Deploy with the images already on the host |
| `--timeout SECONDS` | Allow more than the default 900s for the stack to settle |

Set `AL_VERIFY_HOST` when the published ports are reachable on a host other than `localhost`.

## What is checked

**Config checks** (`tests/test_config.py`, no running stack needed):
`.env.secrets` has every required value, the Compose config resolves, no service
builds from source, team images are pinned `mdx` references, image overrides agree
between `.env` and the per-stack env files, and every `mdx` image exists in the registry.

**Deployment checks** (`tests/test_live_deployment.py`, read-only):
every service has all its replicas running on the configured image, health checks
pass, no container has restarted, every setup and wait-for job exited 0, each HTTP
API answers, the Elasticsearch cluster is not red, the Kafka topics exist with the
right partition counts, the S3 bucket exists, the Elasticsearch index template is
installed and the Logstash pipelines are running.

## Running the checks directly

```bash
uvx --from pytest==9.1.1 pytest tests                 # everything
uvx --from pytest==9.1.1 pytest tests -m "not live"   # config only, before deploying
uvx --from pytest==9.1.1 pytest tests -m "not registry"   # offline
```

A failure names the container and the command to inspect it, usually
`docker logs <container>`.
