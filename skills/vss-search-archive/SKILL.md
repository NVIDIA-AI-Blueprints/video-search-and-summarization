---
name: vss-search-archive
description: Use this skill when a user wants to search archived VSS video, ingest a file or RTSP source for search, or remove a search source. Do not use it for visual Q&A, live captioning, or video summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.4.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

## Purpose

Run NAT-free archive search from the host machine. The `nvidia-vss-cli`
distribution exports `lib.*` for Python callers and provides the `vss-cli`
console executable. Search never calls the VSS agent `/generate` route and never
performs NAT query decomposition.

## Prerequisites

- A running VSS search deployment and a checkout containing `libs/vss-cli`.
- Host `uv`, plus Docker access for Docker deployments or `kubectl` access to
  Deployments, ConfigMaps, Services, Endpoints, Ingresses, and port-forwards for Kubernetes.
- The `vss-manage-video-io-storage` skill for source listing and inspection.
  Search ingestion and deletion use the agent-backed recipes in this skill.

Do not execute `vss-cli` inside a distroless VSS container or a pod. Do not
wrap it with `docker exec`, `kubectl exec`, or `sh -lc`.

## Mandatory workflow

1. Confirm this is the **search** profile. If it is unavailable, ask whether
   the user wants it deployed; do not target an unrelated profile.
2. If the user names a file, camera, or sensor, list registered sources using
   `vss-manage-video-io-storage` before searching. Accept an exact source,
   stream ID, or one unambiguous normalized substring match only.

   - If there is no match, stop. Report the registered names and ask whether
     the user meant one of them, wants to ingest the named source, or wants an
     unrestricted search.
   - If several sources match, stop and ask the user to choose.
   - Never remove a requested source constraint, substitute a different video,
     or run a broad search as a probe.

3. Decompose the request into explicit fields using
   [Query decomposition](references/query_decomposition.md). The CLI does not
   decompose natural language. Preserve the requested object/action and use
   `--query`, `--attribute`, `--has-action`, `--video-source`, time bounds, and
   `--use-critic` as appropriate.
4. Run the host command for the selected deployment. It validates named
   sources again against that deployment's VST listing before querying ES.
5. Present an inspection report titled `Video Search Results`, not raw JSON.
   For every hit, copy the selected source, start/end timestamps, similarity,
   and screenshot/clip URL verbatim from CLI output. Never invent or normalize
   evidence. Show critic status and every returned criterion (`✓`/`✗`); mark a
   null critic result as skipped.
6. End with a `Verification Step`. Offer to download and visually inspect the
   returned screenshots, but do not download them without user opt-in or prior
   authorization. When authorized, inspect the pixels and report a grounded
   confirmed/rejected/uncertain verdict for each inspected hit.
7. If the result set is empty, say that no matches were found. Keep all source
   constraints, explain that the object may be absent or the query too narrow,
   and offer a specific query or similarity-threshold refinement. Never broaden
   the search silently or fabricate a result.

When the user explicitly asks to ingest or delete a search source, use the
agent-backed procedures below. Do not delegate those mutations back to
`vss-manage-video-io-storage`; its bare VIOS operations do not maintain the
search indexes.

## Host CLI

Always invoke the checked-out `libs/vss-cli` project with `uv run`:

```bash
uv run --project libs/vss-cli vss-cli search run [deployment options] [query options]
```

Direct low-level invocation remains environment-free. Use explicit runtime
flags or `--config` with explicit `--config-env KEY=VALUE` values only when a
deployment selector is not appropriate. The CLI never reads endpoint variables
from the host process.

### Docker

Docker requires a deployed profile's generated file, not its checked-in `.env`.
The command reads `deploy/docker/developer-profiles/dev-profile-<profile>/generated.env`
and that profile's checked-out agent config. It translates Compose-only service
DNS to the loopback ports published for Elasticsearch, RTVI-Embed, RTVI-CV,
and VST.

```bash
uv run --project libs/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "find all instances of forklifts" \
  --source-type video_file --top-k 10 --no-use-critic
```

Before running this, start the profile with `dev-profile.sh` so `generated.env`
exists. Do not use a checked-in `.env` as a substitute. Private service ports
are loopback-only; do not expose them to a LAN simply to run a search.

### Kubernetes / Helm

Kubernetes has no `generated.env`. The command obtains non-secret state from:

1. the live vss-agent Deployment, to find its config mount and literal or
   ConfigMap-backed environment values;
2. the mounted ConfigMap's `config.yml`;
3. referenced ConfigMaps for the runtime-key allowlist only.

It never reads `secretKeyRef` values, Secrets, checked-in `values.yaml`, or the
agent runtime endpoint. It rewrites private backend Service URLs to managed
localhost `kubectl port-forward` connections and closes every managed forward
on success, failure, or interruption. `VST_EXTERNAL_URL` is the exception: it
is returned in screenshots and media links that must outlive the CLI process,
so it must already be host-reachable or use an operator-managed localhost
forward whose lifetime extends through result consumption. The CLI rejects an
in-cluster Service URL in that external field instead of returning dead links.

```bash
uv run --project libs/vss-cli vss-cli search run \
  --deployment kubernetes --namespace <namespace> --release <release> \
  --kube-context <optional-context> \
  --query "person in a white jacket climbing a ladder" \
  --attribute "white jacket" --has-action true \
  --video-source <resolved-source> --top-k 10 --no-use-critic
```

If a required runtime value is Secret-backed or absent from the Deployment and
its non-secret ConfigMaps, stop. Do not print or pass a secret. Use an explicit
non-secret endpoint override when valid, or route authenticated VLM work
through the operator-managed workflow.

## Search behavior and safeguards

- `ELASTIC_SEARCH_INDEX` wins whenever the deployment provides it. The only
  fallback is `mdx-embed-filtered-2025-01-01`, never `video_embeddings`.
  Missing indexes fail with nearby MDX index diagnostics; ingest video before
  retrying.
- The configured Cosmos/RTVI Embed model is verified through `/v1/models`.
  The CLI never guesses a replacement model ID. Choose one explicitly from the
  reported deployed IDs if the configured model is unavailable.
- Attribute/fusion search performs a short RTVI-CV text-embedding capability
  preflight. It fails by default rather than hanging or silently changing the
  search. `--allow-embed-only-fallback` is the only opt-in way to remove
  attributes and continue as embed-only search.
- Result object IDs that are missing or `unknown` are not merged together.
- Always make critic intent explicit. Use `--no-use-critic` for ordinary host
  searches. Use `--use-critic` only when the deployment exposes an
  operator-reachable, unauthenticated VLM or an approved operator-managed
  authenticated workflow has already been established. This skill never reads
  or copies an API key. Requested critic configuration errors are fatal; only
  transient backend failures degrade to unverified results.
- Deployment-discovered critic calls default to frame-base64 media so remote or
  containerized VLMs never receive a host-loopback VST URL. Use video-url only
  when the selected VLM can demonstrably reach the chosen VST URL.

## Query examples

```bash
# Embed-only search across all ingested files
uv run --project libs/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "red forklift near a loading bay" --source-type video_file \
  --no-use-critic

# Attribute-only search; source must have been resolved first
uv run --project libs/vss-cli vss-cli search run \
  --deployment kubernetes --namespace vss --release search \
  --query "person wearing a white jacket" \
  --attribute "white jacket" --has-action false \
  --video-source warehouse-camera-3 --no-use-critic

# Deliberate fallback when a deployment has no RTVI-CV text endpoint
uv run --project libs/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "forklift near a loading bay" --attribute "yellow forklift" \
  --has-action true --allow-embed-only-fallback --no-use-critic
```

## Ingestion and deletion

Only ingest through the VSS agent backend: that transaction creates the VIOS
source and both RTVI-CV/RTVI-Embed records. A bare VIOS upload is not
searchable. Use `vss-manage-video-io-storage` only to list and inspect sources;
the recipes in this section are authoritative for search mutations.

Use the guarded runner below for exactly one mutation. It deliberately has no
default action. Set `ACTION` to one of `file-ingest`, `rtsp-ingest`,
`file-delete`, or `rtsp-delete`, then set only that action's inputs:

- `file-ingest`: `FILE_PATH` and a safe, whitespace-free `FILENAME`.
- `rtsp-ingest`: `RTSP_URL`, a unique unregistered `SOURCE_NAME`, and optional `RTSP_USERNAME`.
  Leave `RTSP_PASSWORD` unset to receive a hidden interactive prompt, or point
  `RTSP_PASSWORD_FILE` at an operator-managed, mode-0600 file for automation.
- `file-delete` or `rtsp-delete`: the exact `VIDEO_ID` and `SOURCE_NAME`
  resolved before deletion. The two delete operations are mutually exclusive.

Set `DEPLOYMENT=docker` for the loopback-published Docker services. For
Kubernetes set `DEPLOYMENT=kubernetes`, `NAMESPACE`, `RELEASE`, and optionally
`KUBE_CONTEXT`. The runner selects the live-release Services for the agent and
VST, follows each live runtime ES/RTVI endpoint, creates loopback-only
port-forwards for in-cluster endpoints, waits for readiness, and closes them on
success, failure, or a signal. It preserves an RTVI-CV HAProxy path prefix; a
direct RTVI-CV Service must have exactly one ready backend, while a prefixed
route must have a live x-stream-id/consistent-hash Ingress contract. Explicit
host-reachable `VSS_AGENT_URL`, `VST_URL`, `ES_URL`, or `RTVI_CV_URL` values take precedence.
Management URLs must be HTTP(S) endpoints with a hostname and no embedded
userinfo, query credentials, or fragments.
The runner uses `VST_URL` as the upload authority while preserving the path
returned by the agent; an explicit `VST_FORWARD_URL` overrides that authority.
It also loads the exact video, behavior, raw, and wildcard index expressions
from the selected Docker `generated.env` plus profile config, or from the live
Kubernetes Deployment and ConfigMap. Explicit index variables may override
those values; unrelated hard-coded defaults are never used to certify cleanup.

```bash
ACTION=file-ingest \
FILE_PATH=/data/clip.mp4 \
FILENAME=clip.mp4 \
DEPLOYMENT=docker \
PROFILE=search \
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

Supported actions are `file-ingest`, `rtsp-ingest`, `file-delete`, and
`rtsp-delete`. Set the action-specific values shown in
[Source lifecycle CLI](references/source_lifecycle.md). The script performs
deployment discovery, loopback-only Kubernetes forwarding, source identity/type
validation, transactional cleanup, and exact Elasticsearch verification.
For split-cluster or operator-managed cleanup transports, explicit
host-reachable `ES_URL`, `BEHAVIOR_ES_URL`, and `RAW_ES_URL` override the live
embed, behavior, and raw-frame endpoints independently.

The upload action asks the agent for the VST URL, preserves the returned path,
and rewrites only its scheme/authority when a Kubernetes VST forward is active.
It uses 10 MiB nvstreamer chunks with bounded retries. If VST has returned a
sensor ID but `/complete` or a later check fails, the exit trap requests
agent-backed cleanup before closing port-forwards. A successful completion
response must echo the same sensor ID. A disconnected or timed-out completion
request is never reported as durably cleaned because its server-side embedding
work may still be active. If no sensor ID was returned,
report the failed upload identifier and inspect VST rather than guessing an ID.

`rtsp-ingest` does not treat the route's `success` response as search readiness:
it resolves the exact VST sensor and waits a bounded time for an exact source
record in the RTSP embedding index expression. Both delete actions reject
`failure`; RTSP deletion also rejects `partial` so history is not purged while a
producer may still run. File deletion can reconcile `partial` only after direct
VST storage/sensor repair, an ID-routed RTVI-CV removal, tightly scoped history
cleanup, and independent proof that the sensor, timeline, physical media list,
and exact ES counts are all empty. RTSP rollback similarly waits for one exact,
unambiguous post-mutation VST identity before sending its name-addressed delete,
and does so only after this invocation received an agent-confirmed add. Failed
or transport-unknown adds are never deleted by name because ownership is not provable.
RTVI-CV 404 is idempotent only when the verified route returns a structured
`NotFound` body naming that exact camera ID; a generic route 404 is a failure.
Never use the
deprecated `PUT /api/v1/videos-for-search/{filename}` route, a bare VIOS delete,
or an unconstrained index query as a substitute.

## Troubleshooting

- **`generated.env` missing**: start the selected Docker profile with
  `deploy/docker/scripts/dev-profile.sh`; do not fall back to `.env`.
- **Kubernetes ConfigMap/port-forward error**: verify read and port-forward
  RBAC in the selected namespace. Do not use a pod shell as a workaround.
- **Kubernetes `VST_EXTERNAL_URL` is Service-backed**: configure a durable
  external ingress URL or an operator-managed localhost forward. The CLI does
  not create a short-lived managed forward for result URLs.
- **Source unavailable or ambiguous**: stop and clarify; do not substitute.
- **Zero results**: report the empty outcome, retain the selected source, and
  offer an explicit query or similarity-threshold refinement. Run a broader
  search only after the user accepts it.
- **Missing index**: verify ingestion completion and the deployed
  `ELASTIC_SEARCH_INDEX` value.
- **Model preflight failure**: pass an explicit deployed model ID after
  reviewing the reported list.
- **RTVI-CV preflight failure**: repair the service or use the explicit
  `--allow-embed-only-fallback` option only when an embed-only result is
  acceptable.
- **Critic needs authentication**: stop and use the secret-managed operator
  path. Never copy API keys into CLI flags, generated files, logs, or skill
  output.
