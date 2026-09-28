# Troubleshooting

Ingestion failures, CLI exit codes, and the gaps this benchmark does not cover.

## Ingestion

`--ingest-flow` defaults to **`vst-direct`**.

### `vst-direct` (default)

The runner uploads to VIOS, which fans the video out to perception services
through webhooks. There is no `/complete` call or `chunks_processed` count.
After upload, the runner waits for VST registration and probes the search index;
registration alone does not prove that embedding or indexing finished. Check
`webhooks.enabled` and the RT-Embed model mapping if the index probe fails.

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
registration; for `vst-direct` it also probes index coverage before scoring.
Querying early returns empty results that look like a retrieval regression.

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
| Everything scores 0.0 | possibly empty or unscoped search indices | check Step 3's Elasticsearch index counts and the index-probe result; a VST listing alone is insufficient |
| Header says `fallback_path` | routing is active; that is the path for unrouted queries | look at `planned_paths` |

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

Verification is best-effort by design — `_verify_results` never fails a search —
so a broken clip URL and "no critic configured" are indistinguishable in the
output. Check the causes in order.

**1. The clip URL is not routable from inside the containers.** This is the
common one when the benchmark runs *on* the deployment host.

The CLI builds the critic with `media_mode="video_url"` and
`video_url_scope="external"`, so RT-VLM is handed a VST clip URL and fetches it
itself. That URL is built from whatever `vss configure --base-url` recorded. Set
it to `http://localhost:7777` and RT-VLM — a container — resolves `localhost` to
itself, the fetch fails, and every hit stays `unverified`.

```bash
vss configure show | grep -i base_url
docker exec vss-rtvi-vlm curl -sf --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}\n' \
    http://<HOST_LAN_IP>:7777/vst/api/v1/sensor/version
```

Fix by pointing the CLI at an address the containers can reach, even though you
are on the host:

```bash
vss configure --base-url http://<HOST_LAN_IP>:7777
```

`http://172.17.0.1:7777` (the docker bridge gateway) works as a fallback when
the host IP is not reachable from the container network.

Running the benchmark from another machine hides this entirely: nothing but a
routable address reaches the deployment in the first place.

**2. RT-VLM is not in the CLI's config.** The critic stack is built only when
the deployment exposes one — `vss configure show` must list `rt_vlm` with both a
URL and a non-empty model list, and it must answer an availability probe. Any of
those missing returns no critic, silently.

**3. The critic ran but returned no verdict.** Distinguish this from the above:
here the response carries a `search_message` such as *"Visual verification ran
but produced no verdict for any hit"*, rather than no verification block at all.
That is a different failure — the critic was reached and produced nothing — and
is not explained by the clip-URL routing above.
