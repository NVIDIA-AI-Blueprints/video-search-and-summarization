# Troubleshooting

Ingestion failures, CLI exit codes, and the gaps this benchmark does not cover.

## Ingestion

`--ingest-flow` defaults to **`vst-direct`**.

### `vst-direct` (default)

The runner uploads to VIOS, which fans the video out to perception services
through webhooks. There is no `/complete` call or `chunks_processed` count.
After upload, the runner waits for VST registration, then runs the **ingest
gate**: it polls Elasticsearch and RT-CV's stream list through the ingress
(`:7777`, `--ingress-url`) until every video is fully indexed, and aborts
instead of querying if one is not.

| Index | Done when |
|---|---|
| embeddings `mdx-embed-filtered-2025-01-01` | count ≥ ⌈duration / `--chunk-s`⌉ |
| tags `default_<sensor uuid>` | count ≥ the same chunk count |
| raw `mdx-raw-2025-01-01` | RT-CV listed the stream and then dropped it, and the raw count has not changed for `--ingest-quiet-s` since the drop (`raw_check: rtcv_released`) |
| behavior `mdx-behavior-2025-01-01` | raw is done and no count changed for `--ingest-quiet-s` (a heuristic, recorded as `behavior_check: quiet_window`) |

RT-CV writes a raw document only for a frame with at least one detection. On
sparse footage the raw count is far below the frame count (18 of 381 frames on
`Normal_Videos779`), and the last document is the last detection, not the end
of the video. So the frame count and the video's end are reported as coverage,
for example `raw=18/381 frames (5%)  last detection 3.77s of 12.7s`, and never
required. A low coverage figure is not a stall.

Polling starts before the first upload and each video joins as its upload
returns, so a short clip that RT-CV lists and drops while a later file is still
uploading is still counted as seen. The deadline and the "querying held back"
time (`waited_s`) both start when the last upload returns.

The other ways raw can finish:

- RT-CV never listed a video, but it has raw documents and the count stays
  unchanged for the quiet window: raw passes as `rtcv_missing`, with a warning.
  With no raw documents at all it does not pass. That looks the same as a
  webhook that never fired, so failing is the safe default.
- A `--skip-existing` source passes as `existing` once it has raw documents and
  the count stays unchanged for the quiet window after the first poll.
- RT-CV released the stream but wrote no raw documents: raw passes, with a
  warning that nothing was detected.

A video that has raw documents but no behavior documents also passes with a
warning: zero tracked objects is allowed, but on the stock datasets it usually
means RT-CV's behavior output is misconfigured.

The abort message lists every unfinished video with each index's count against
its target and the reason it is not done:

| Reason in the abort | Fix |
|---|---|
| `RT-CV never listed the stream` (no raw frames either) | check `webhooks.enabled` in the VIOS notification config and that `RTVI_EMBED_MODEL` matches the webhook's model string |
| `RT-CV is still processing` / embed or tag counts short | perception is slow; raise `--ingest-deadline-s` |
| `N frame docs (frames with detections), still changing` | RT-CV or Logstash is still writing; raise `--ingest-deadline-s` if it never settles |
| `no frame docs, and the source was not uploaded this run` | a `--skip-existing` source with nothing in `mdx-raw`; re-upload it |
| `no tag documents` | the profile has no RT-VLM; pass `--ingest-require embed,raw,behavior` |
| `stale documents for X` (before upload) | an earlier ingest of the same name left documents behind; delete the source and let the cleanup webhooks empty the indexes, or use `--clear` / `--only-dataset` |
| `duration unknown` | install ffprobe on the runner host; the VST timeline fallback also failed |

Results record the gate under `flow.ingest_readiness` and the batch figures
under `summary.ingest`. `--legacy-index-probe` restores the old one-hit
coverage probe for one release; it passes ~20 s after upload while RT-CV is
still writing, so do not quote numbers from it.

### `agent-3step` (explicit alternative)

```
POST {agent}/api/v1/videos                     → {"url": ...}
POST {url}          (bytes, nvstreamer-* hdrs) → {"sensorId": ...}
POST {agent}/api/v1/videos/{sensor}/complete   → {"chunks_processed": N}
```

Step 2 goes **browser → VST directly**, bypassing the agent. Step 3 is where
indexing happens (RTVI-CV stream add and RTVI-Embed embedding generation, run
in parallel). Each phase is timed separately, so you can see whether time went
to transfer or to processing.

`/complete` is intermittently flaky (502 on the first call, 200 on a retry), so
it retries with backoff — `--complete-retries`, default 3.

### `legacy-put`

`PUT /api/v1/videos-for-search/{name}` — one request. **Deprecated upstream**,
and its removal is gated on this repo migrating away from it. Kept so a
baseline can still be captured while it exists. It runs the same post-upload
pipeline internally, so it is not more reliable — just less observable.

### After ingest

Neither a successful `vst-direct` upload nor a 200 from the optional
`agent-3step` `/complete` call proves readiness. The runner polls VST for
registration; for `vst-direct` it also waits on the ingest gate before scoring.
Querying early scores a half-built index, which looks like a retrieval
regression.

### On a shared deployment

```bash
--skip-existing    # do not re-upload what VST already lists
--clear --confirm-delete  # deletes ALL sources; only on explicit request
```

---


## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Could not find a vss CLI` | none of the four sources resolved | follow the error's remedy: pass `--vss-repo-root` for a current checkout or install `vss` from that checkout |
| `vss exited 4 ... does not expose` | CLI pointed at the agent port | let the script configure it, or `--vss-base-url http://host:7777` |
| `vss exited 1 ... ModuleNotFoundError` | stale virtualenv | rebuild it, or use `--vss-repo-root` |
| `vss exited 5` | nothing ingested | drop `--skip-ingest` |
| `/complete` 502 | known flakiness | retried automatically; raise `--complete-retries` |
| `Duplicate Camera id` | RTVI-CV already has that stream | treated as done — embeddings still generate |
| Everything scores 0.0 | possibly empty or unscoped search indices | check Step 3's Elasticsearch index counts and `flow.ingest_readiness`; a VST listing alone is insufficient |
| Header says `fallback_path` | routing is active; that is the path for unrouted queries | look at `planned_paths` |
| `planned_paths: decided per query` | live decomposition picks each query's path at run time | read the actual split under `Search paths` in the final summary |

---

## Known gaps

- **`openclaw` query flow** — the full new UI flow end to end: chat → OpenClaw
  agent → skill → CLI. It would measure OpenClaw's skill selection and routing
  in addition to CLI retrieval. This runner calls the deployment LLM for query
  decomposition but bypasses OpenClaw. An end-to-end run needs a NemoClaw
  sandbox and a decision about whether CI takes that dependency.
  Not called "agent" because the NAT agent behind `POST /api/v1/search` runs
  its own decomposition — a different decision-maker. That path was removed
  from this script; the legacy `run_eval.py` in the separate `ci-vss-oss`
  repository still queries it and is not available in this checkout.
- **Fixed-route replay differs from live routing** — dataset-provided
  decompositions are used when live decomposition is off or unavailable, while
  `--decompositions` explicitly replaces the live decomposer. Report which
  mode ran.
- **Stage latency depends on the deployed VSS version** — this PR adds the
  `timings` block; a deployment without that code omits the section.
- **Path coverage depends on the queries and their decompositions** — a set of
  action-only queries can exercise only `embed`, however many queries it has.
  Check the `Search paths:` line before reading per-path results.

See `flows/routing.py` for the routing rule and `flows/ingest.py` for the ingest
contracts.

## Every hit is `unverified` and critic-filtered metrics read `NA`

The critic never ran, or ran and could not fetch the clip. Retrieval is
unaffected and looks healthy, which is what makes this hard to spot.

Verification is best-effort by design — `_verify_results` never fails a search.
A disabled critic adds a `Visual verification disabled: ...` search message;
failed clip verification instead leaves hits `unverified`. Check the causes in
order.

**1. The actual VST clip URL is not reachable from RT-VLM.**

The CLI builds the critic with `media_mode="video_url"` and
`video_url_scope="internal"`. VST supplies the `videoUrl` in the search result;
the CLI's configured base URL only controls how the CLI reaches the services.
`vss configure --base-url http://localhost:7777` is valid when the CLI runs on
the deployment host and does not itself make the clip URL loopback.

Inspect a returned hit's `videoUrl`, then test that **exact URL** from the
RT-VLM container (for example with `docker exec vss-rtvi-vlm curl ...`). If it
cannot be fetched, correct the VST ingress or clip-URL configuration used by
that deployment; changing the CLI base URL alone may not help. Do not assume
the Docker bridge gateway or the host LAN IP is the right address.

**2. RT-VLM is not in the CLI's config.** The critic stack is built only when
the deployment exposes one — `vss configure show` must list `rt_vlm` with both a
URL and a non-empty model list, and it must answer an availability probe. Any of
those missing returns no critic, silently.

**3. The critic ran but returned no verdict.** Distinguish this from the above:
here the response carries a `search_message` such as *"Visual verification ran
but produced no verdict for any hit"*, rather than no verification block at all.
That is a different failure — the critic was reached and produced nothing — and
is not explained by the clip-URL routing above.
