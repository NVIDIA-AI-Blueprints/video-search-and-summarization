---
name: vss-search-archive
description: Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. Do not use it for visual Q&A, live captioning, or video summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

## Shell contract

Run every fenced `bash` recipe with Bash. If a command-string exec tool may
default to POSIX `sh`, invoke the recipe through `bash -c` or as a Bash script.
Do not use a login shell that resets the provisioned `PATH`, and never submit
Bash syntax directly to that default shell.

## Purpose

Operate archive search from the caller's host. Compose and Kubernetes use the
same `vss configure` and `vss search run` commands; only the deployment origin
differs. Source ingestion and deletion are Agent-backed **when the deployment has
an agent `/api` route**; on a build without one, they belong to
`vss-manage-video-io-storage` `references/provision-vios-source.md`.

## Hard boundaries

- Run the project-local CLI on the host. Never use `docker exec`, `kubectl
  exec`, a pod shell, or a globally installed `vss` as a substitute.
- Never improvise a mutation against Elasticsearch, RTVI-CV, RTVI-Embed,
  storage-ms, or VST. Two paths are sanctioned, and the deployment picks which:
  the Agent upload/delete lifecycle where an agent `/api` route answers, and
  `vss-manage-video-io-storage` `references/provision-vios-source.md` where none
  does. That recipe owns the direct calls this rule otherwise forbids.
- Never remove, broaden, or silently substitute a requested source constraint.
- Similarity is retrieval evidence, not proof of visual presence.
- When the capability receipt enables VSS UI artifacts, publishing the exact
  validated search result is part of a successful search. Do not finish with
  prose alone.
- The CLI attempts critic verification by default. Do not separately inspect
  screenshots or call another verifier during the initial search turn.
- Offer delegated verification only when every displayed result is
  `unverified`, and only after displaying them and receiving explicit user
  confirmation. If any result is `confirmed` or `rejected`, do not hand off
  any result to another verifier.

## Prerequisites

- A running VSS `search` profile and its host-reachable Compose or Ingress
  origin.
- A checkout containing `services/agent`, host `uv`, `curl`, and `jq`.
- The bundled `scripts/run_search.sh` runner for both source listing and the
  validated search. Use the exact base directory announced by the skill
  loader; do not assume it is an environment variable and do not copy the
  runner's Bash body into the exec command. An OpenClaw workspace attached by
  VSS exposes it at `./skills/vss-search-archive/scripts/run_search.sh`.

`--extra cli` is mandatory because the base distribution contains the core
libraries, while `nvidia-vss-cli` declares the `vss` executable. The runner
owns that exact project-local invocation and pre-warms it before use.

The runner resolves the deployment through its one public/host origin, in
order, from `VSS_ORIGIN`, the capability receipt, the recorded CLI config, or
`HOST_IP`. It fails when none exists and runs `vss configure` before listing or
searching. Do not duplicate that setup in separate tool calls.

In a persisted multi-step workflow, reuse the origin recorded by the prepared
deployment as above. Do not repeat public-origin selection, edit routing, or
redeploy merely because the next agent turn did not inherit shell variables.

See [deployment resolution](../../vss-build-vision-ai/references/deployment_resolution.md)
for the deployment-owned `VSS_PUBLIC_URL` contract. On Kubernetes, never use
port-forwarding, Service DNS, NodePorts, or a guessed Helm release. Routes not
exposed through the Ingress are recorded as absent and a search path needing
one exits 4.

For deployment readiness, ingestion, fixture cleanup, index checks, RTSP, or
deletion, read [source lifecycle](references/source_lifecycle.md) completely
before acting. Re-run `vss configure` after the first ingestion: the recorded
raw family is what enables frame-level lookups (it gates `frames_index`, which
attribute and fusion need for frame enrichment). Only source-type selection is
independent of the index inventory.

## Mandatory search workflow

1. Confirm the selected deployment is the `search` profile. If required routes
   are unavailable, ask whether to reconnect or deploy it with
   `vss-deploy-profile -p search`; do not target another profile.

2. When the user names a file, camera, or sensor, list registered sources with
   one runner call before searching:

   ```bash
   bash ./skills/vss-search-archive/scripts/run_search.sh --list-sources
   ```

   If the loader announced another base directory, substitute that exact path;
   do not search for or guess a bundled harness path. The runner reads the same
   resolved origin as search and takes no endpoint. Accept only an exact
   source, stream ID, or one unambiguous normalized substring match.

   - No match: report the missing source, list available names, and ask the
     user to clarify or explicitly request ingestion. Stop without probing the
     search CLI, deploying, or ingesting. **Never continue with a different
     source.** Answering about `warehouse_sample` when the request named
     `warehouse-ladder` returns a confident answer about the wrong video, and
     nothing downstream can tell it was substituted.
   - Several matches: ask the user to choose and stop.
   - Never substitute another video or run an unrestricted search as a probe.

   Preserve both the matched source's `.sensorId` and `.name`. The required
   `--video-source` value depends on the search path, not the source type:
   `embed` and `fusion` use the sensor ID; `attribute` and `object` use the
   name. The CLI matches this value literally and does no name↔ID conversion.
   Set `--source-type video_file` for uploads or `--source-type rtsp` for live
   streams. This selects the index partition for that media kind from a fixed
   uploads anchor (not a discovered index), independently of the identifier, so
   it is correct regardless of ingestion order.

3. Decompose the request before choosing a path; do not pick by surface form.
   `run embed` accepts any sentence, so being one sentence is not evidence for
   embed. Separate each specific detectable property (`white jacket`, `red hard
   hat`) from the actions/relations only embeddings capture, then choose:

   - a detectable property plus an action or relation is present → `run fusion` (even within one sentence)
   - free-text intent with no detectable property → `run embed`
   - detectable properties only, no action or relation → `run attribute`
   - explicit tracked object IDs → `run object`

   `--attribute` is for specific detectable properties, not generic nouns or
   actions. A property counts only when RT-CV detects it on the subject (attire,
   PPE, color-on-person), not object identity or an object's own color; keep
   `red forklift` wholly in `--query`. `worker in a hard hat carrying a cone` has
   a property (`hard hat`) and an action (`carrying a cone`): `run fusion --query
   "worker in a hard hat carrying a cone" --attribute "hard hat"`. Reserve embed
   for genuinely attribute-free intent.

4. Read [CLI usage](references/cli_usage.md) for every supported flag, then
   invoke the bundled runner once. Set `--source-scoped true` whenever the user
   named a source; the runner refuses to broaden that request when no
   `--video-source` follows it. Use `false` only for an explicitly unrestricted
   request. Pass the selected search mode and its exact CLI flags next. A `--`
   separator is accepted but not required:

```bash
bash ./skills/vss-search-archive/scripts/run_search.sh \
  --source-scoped true embed \
  --source-type video_file \
  --video-source "<resolved-sensor-id>" \
  --query "<complete user query>" \
  --top-k 3 \
  --raw
```

Repeat `--video-source` for each resolved value. `embed` and `fusion` take the
sensor ID; `attribute` and `object` take the source name. Append repeatable
`--attribute`, `--object-id`, and time bounds only as requested. Invoke the
runner with `bash`, not by copying its body into the exec command. It resolves
the pinned VSS checkout and origin, validates the exact two-document CLI
result, and emits the versioned UI envelope from that same result.

Do not pass endpoint, index, model, deployment, profile, or base-URL flags to
`search run`; `vss configure` owns those values. Do not replace a failed CLI
call with `/api/v1/search` or private backend access.

5. Validate each nonempty hit's exact returned `screenshot_url` with a bounded
GET for availability only. Its normalized scheme, host, and effective port
always match the origin recorded by `vss configure`, because the CLI stamps
that origin into every hit — a localhost media URL means the deployment was
configured against a localhost origin, not that the URL is malformed. On Brev,
prefer the public HTTPS secure-link origin. If setup used the documented
host-reachable fallback after its one bounded public probe failed, accept only
that exact recorded origin and label its media URLs host-local; do not restart
routing diagnosis. Reject credentials in the URL and never rewrite the URL or
add a `streamId` routing header. Discard the response body; availability is not
visual evidence.

6. Read every hit's `verification` object:

   - `confirmed`: the critic found all requested visual criteria in that clip.
   - `rejected`: the critic found a visual criterion was not met.
   - `unverified`: no usable critic verdict was produced. This includes a
     missing VLM, inaccessible media, and malformed or inconclusive output.

The CLI is fail-open: verification failure must not discard or fail retrieval.
Never derive a verdict from similarity, filenames, object IDs, or screenshot
availability. Treat boolean `criteria_met` values as critic evidence only.

7. Format nonempty results without user-visible raw JSON:

```text
## Video Search Results
<each hit's exact source, start/end, similarity, complete media URL,
verification result, and criteria when present>

Similarity scores are retrieval evidence; the separate verification result
records whether the bounded clip satisfied the visual request.

## Verification Step
Would you like me to verify the unverified search results?
```

Include `## Verification Step` only when the nonempty displayed result set is
entirely `unverified`. If any displayed result is `confirmed` or `rejected`,
omit it even when other hits are unverified. Never deploy a VLM or call
`vss-ask-video` automatically during this results turn.

When the search command prints the machine-readable envelope, publish its
exact JSON object without reconstructing, summarizing, fencing, or modifying
the payload:

- If `vss_ui_publish_artifact` is an available tool, call it exactly once with
  that JSON object. After its success result, finish the human-facing answer
  without copying the XML envelope into prose.
- Otherwise, copy the command's single envelope line verbatim into the final
  response. The gateway strips it from prose when the upstream exposes only
  final text.

The gateway emits `artifact.created`, and the Search tab consumes the payload
to render result cards. Never publish an artifact when the search command or
JSON validation failed.

8. If the user explicitly confirms, read
[search-result verification](references/result_verification.md) completely and
delegate the displayed hits only after confirming again that every one is
still `unverified`. Preserve their exact bounded intervals and the complete
original visual intent. Keep at most three delegations in flight. Never hand
off a partially verified result set.

9. If `.data` is empty, report zero candidates faithfully — a fact about
   retrieval, not about the video — and emit the same `vss.search.results`
   artifact with its empty `data` array when UI artifacts are enabled. Do not
   claim the object is absent, describe what the footage contains, or argue it
   is not something you would expect there: a threshold or embedding gap yields
   the same empty result as a genuine absence. Offer a specific query or
   similarity-threshold refinement while preserving the source. Never broaden
   the search silently.

## Natural-language Agent responses

Use the host CLI for deterministic structured search. If a caller explicitly
requires the deployment Agent to decompose a natural-language request, its
`/api/v1/search` response is conversational text, not `SearchOutput`. Validate
the known text field and present it as prose; never run `.data[]`, screenshot,
or verification parsing against that response or invent structured hit rows.

## Troubleshooting

- CLI unavailable: retain `--extra cli`, verify `VSS_REPO_ROOT`, and stop.
- Exit 2: read the selected path's `--help`; do not guess flags.
- Exit 3: a recorded backend is unreachable; repair routing and reconfigure.
- Exit 4: run `vss configure --base-url <origin>` or choose a path whose
  required services are actually routed.
- Exit 5: ingest the source, wait for readiness, and re-run `vss configure`.
- Missing/ambiguous source: stop for clarification; never substitute.
- Missing RT-VLM: retrieval remains valid and results remain `unverified`.
- Authentication: use the operator-approved route. Never place secrets in
  prompts, flags, generated files, logs, or skill output.
