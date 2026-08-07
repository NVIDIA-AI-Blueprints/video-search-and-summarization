---
name: ngc
description: Registry access for the vss-behavior-analytics image. The default GHCR image is public and needs no login; use this only when overriding the registry to NGC, or when a pull returns 401/403.
---

# Registry Access — GHCR default, NGC fallback

**You probably do not need this file.** The default image is public:

```
ghcr.io/nvidia-ai-blueprints/vss/vss-behavior-analytics:develop-latest
```

`docker pull` and `docker compose up` fetch it **unauthenticated**. There is no `docker login`, no API key,
and no `NGC_CLI_API_KEY` in the default path. If a pull is failing, check the tag and network before assuming
it is a credentials problem.

The coordinate is assembled in `deploy/docker/services/analytics/behavior-analytics/compose.yml`:

```yaml
image: ${VSS_BEHAVIOR_ANALYTICS_IMAGE:-${VSS_CONTAINER_REGISTRY:-ghcr.io/nvidia-ai-blueprints/vss}/vss-behavior-analytics}:${VSS_BEHAVIOR_ANALYTICS_TAG:-develop-latest}
```

So `VSS_CONTAINER_REGISTRY` (or `VSS_BEHAVIOR_ANALYTICS_IMAGE`) is what moves you off GHCR.

> **Registry and tag move together.** `develop-latest` does not exist on `nvcr.io`, and NGC release tags such as
> `3.2.1` do not exist in GHCR. Changing one without the other resolves to a coordinate that 404s — which reads
> like an auth failure but is not one. See `.github/github-first.md`.

## Only if you override the registry to NGC

An NGC-hosted image is not public, so it needs a key and a login.

### Get an API key

1. Go to https://ngc.nvidia.com → sign in.
2. Top-right → **Setup** → **API Keys** → **Generate Personal Key**.
3. Permissions: **NGC Catalog**.
4. Copy the key immediately (it is shown only once).

### Export it and log in

```bash
read -rsp "NGC API key: " NGC_CLI_API_KEY
echo
export NGC_CLI_API_KEY
printf '%s' "$NGC_CLI_API_KEY" | docker login --username '$oauthtoken' --password-stdin nvcr.io
```

`$oauthtoken` is the literal username for NGC registry auth — use it verbatim, do not substitute your own.

> Security note: Prefer a current-session handoff: enter the key with `read -rs`, inject it from a secrets
> manager, and pass it to `docker login` with `--password-stdin`. Do not pass the raw key as a CLI argument,
> write it to any workspace file or shell profile such as `~/.bashrc`, or commit it to version control. If an
> env file is unavoidable, keep it outside the repo and restrict it with `chmod 600`.

This skill does not use the `ngc` CLI to download NGC resources — that flow lives in the `vss-deploy-profile`
skill's `references/ngc.md`.

## Troubleshooting a failing pull

- **`401 Unauthorized` / `403` from `ghcr.io`** — unexpected, since the image is public. Usually a stale
  `docker login ghcr.io` with expired credentials: `docker logout ghcr.io` and retry anonymously.
- **`manifest unknown` / 404** — the tag does not exist at that registry. Almost always a registry/tag mismatch;
  see the note above.
- **`401` / `403` from `nvcr.io`** — the key is missing, expired, or not scoped to **NGC Catalog**. Regenerate,
  re-export `NGC_CLI_API_KEY`, and re-run `docker login`.
