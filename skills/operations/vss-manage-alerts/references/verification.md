# Verification Results & Verdicts (Workflow B — CV mode)

Operational reference for **Workflow B** on a **CV (verification)** deployment: how a CV alert becomes a verdict, how to inspect verification results, and how to customize the VLM-verifier prompts. Execution requires CV mode; *explain-only* asks ("how does verification work?") are answerable in any mode from this background. VLM real-time chunks carry only the trigger's `info.verdict: "confirmed"`, not a verifier verdict (see the last note under the verdict table).

## The verification pipeline

```
camera (VIOS/VST)
  → vss-rtvi-cv            RT-CV perception (Grounding DINO object detections)
  → vss-behavior-analytics rule engine → candidate alert (PPE / ladder / proximity / restricted-area …)
  → vss-alert-bridge       VLM verifier: fetches the clip from VIOS, prompts per alert_type
  → vss-rtvi-vlm           vision-language inference (runs in both modes)
  → Elasticsearch          verified document in mdx-vlm-incidents-*
                           (read via GET $AB/api/v1/realtime/incidents)
```

- Candidate alerts reach Alert Bridge over Kafka (or `POST /api/v1/alerts` for `nv.Behavior` submissions); Alert Bridge looks up the verifier prompts by the alert's `category` (= `alert_type`), calls the VLM on the clip, and stamps the result into the document's `info` block before publishing.
- **Which store the result lands in follows the input's kind.** Stock Behavior Analytics publishes its candidates on `mdx-incidents`; Alert Bridge treats them as incident-kind and writes the verified document to `mdx-vlm-incidents-*`. Only alert-kind input — `nv.Behavior` events on `mdx-alerts` (including `POST /api/v1/alerts`), or any payload carrying `notification_type: "alert"` — goes to `mdx-vlm-alerts-*`, which no Alert Bridge endpoint reads. The alerts profile never writes it, so there an empty or missing `mdx-vlm-alerts-*` says nothing about verdicts.
- **Alert-kind verdicts (Smart Cities).** Where Behavior Analytics also publishes anomaly `nv.Behavior` events on `mdx-alerts`, those verdicts are in `mdx-vlm-alerts-*` and never in `/incidents`. Tell such a deployment by its verifier config: `GET $AB/api/v1/verification/config` lists `stop anomaly module` / `movement anomaly module` (Smart Cities; the endpoint returns alert types lower-cased, so compare case-insensitively). There, read this store too and report its results beside the `/incidents` ones — Alert Bridge does not serve them, Elasticsearch does, through the same public origin (`/elasticsearch`) or the Docker host port. The read below is store-wide (every sensor; `total` capped at 10000) — say so when you quote it. Never report them as "no verification results":
  ```bash
  ES=${VSS_PUBLIC_URL:+${VSS_PUBLIC_URL%/}/elasticsearch}; ES=${ES:-http://${HOST_IP:-localhost}:9200}
  curl -sf --max-time 15 "$ES/mdx-vlm-alerts-*/_search?size=10&sort=timestamp:desc" \
    | jq -en 'input | {total: .hits.total.value, newest: [.hits.hits[]._source | {category, sensorId, timestamp, verdict: .info.verdict, code: .info.verificationResponseCode}]}' \
    || { echo "Elasticsearch mdx-vlm-alerts-* query failed" >&2; exit 1; }
  ```
- This whole path exists only when the alerts profile is deployed in verification mode (`MODE=2d_cv`). Deploy/mode-switch belongs to `vss-build-vision-ai` — never duplicate it here.
- The **real-time** path (VLM mode, Workflow D rules) writes RT-VLM chunks to the same `mdx-vlm-incidents-*` store. Tell the two apart per document: a verifier result carries `info.verificationResponseCode`; an RT-VLM chunk carries `info.chunkIdx` and no response code.

## Verdict interpretation

Verified CV alerts carry an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `verification-failed` | Verification could not complete — API/VLM error, media fetch failure, or a VLM response that could not be parsed into a confirmed/rejected verdict (`verificationResponseCode: 500`, status names the schema error) |
| `""` (empty) | No verdict parsing ran. Either **freestyle** (`alert_agent.media_download.use_verdict: false`, the default, which applies only to on-demand and media-URL requests): the raw VLM text is in `reasoning` and no verdict is stamped — or a pluggable response parser replaced the verdict path: its fields are in `vlm_response`, a JSON string (`response-parser.md`). A `vlm_response` field means a parser. With the stock Cosmos Reason / Cosmos3 verifier, pipeline results always carry `confirmed`, `rejected` or `verification-failed`; another model under `vlm.response_format: auto` (or `other`) stores no verdict, with its answer in `description`. An empty verdict is a **valid success, not a failure**; report the VLM text, never invent a `confirmed`/`rejected`. |

- **`not-confirmed` is never a stored verdict.** Alert Bridge does not write it; it is only a `vlmVerdict` *filter* on the video-analytics API (`vss-query-analytics`) that matches `rejected` + `verification-failed`. Never search `info.verdict` for it or describe it as a parse failure.
- Companion fields (camelCase, inside `info`): `verificationResponseCode` (HTTP-like; `200` = success), `verificationResponseStatus` (`OK` or an error description), `reasoning` (the VLM's explanation), and `vlm_response` (pluggable-parser output only).
- RT-VLM real-time chunks carry `info.verdict: "confirmed"` set by the trigger itself (a Yes/No VLM answer) and no `verificationResponseCode`: that is the trigger's answer, not a verifier verdict.

## Inspecting verification results

Workflow B reads Alert Bridge's `GET $AB/api/v1/realtime/incidents` in its **raw view** — on Docker and Kubernetes alike, with `$AB` from the parent skill's *Deployment prerequisite*. Never pass `consolidate=true` here: the consolidated view keeps only RT-VLM chunks (`info.chunkIdx` + `info.verdict: "confirmed"`), so it never returns a verifier result. Scope only with the declared parameters — `sensor_id` (the stored sensor **name**, resolved as in Workflow C), `category` (only a value copied from a returned document — the stored `category` is the verifier's `output_category` when one is configured, so the stock alert type `FOV Count Violation` is stored as `Ladder PPE Violation`; never pass a `/verification/config` `alert_type`), `start_time` / `end_time` (ISO-8601 UTC), `limit` (≤ 1000) and `offset`. Results come newest first.

```bash
# $AB from the parent skill (K8s: ${VSS_PUBLIC_URL}/alert-bridge; Docker: :9080).
# Same program as SKILL.md Workflow B step 2: pipeline counts (by verdict, by category), on-demand count, newest 10.
# `jq -en 'input | …'` fails on an empty or non-JSON body (a failed curl) on every jq version, so it never
# reads back as "no results"; an empty incidents list still succeeds with `verified: 0`.
VERIFIED='def verdicts: group_by(.info.verdict // "") | map({key: ((.[0].info.verdict // "") | if . == "" then "empty" else . end), value: length}) | from_entries;
input | [.incidents[] | select(.info.verificationResponseCode != null)] as $all | [$all[] | select(has("id") | not)] as $v
| {total, complete: ((.incidents | length) >= (.total // 0)), capped: ((.total // 0) >= 10000), verified: ($v | length), oldest: ($v | last | .timestamp), by_verdict: ($v | verdicts), ondemand: (($all | length) - ($v | length)), by_category: ($v | group_by(.category // "") | map({key: ((.[0].category // "") | if . == "" then "(none)" else . end), value: verdicts}) | from_entries),
  newest: [$all[:10][] | {id: (.id // .Id // ._id), ondemand: has("id"), category, sensorId, timestamp, verdict: .info.verdict, code: .info.verificationResponseCode, status: .info.verificationResponseStatus, reasoning: .info.reasoning, vlm_response: .info.vlm_response, description: .info.description}]}'

# latest verification results, store-wide
curl -sfG --max-time 15 "$AB/api/v1/realtime/incidents" --data-urlencode "limit=1000" \
  | jq -en "$VERIFIED" || { echo "Alert Bridge incidents query failed" >&2; exit 1; }

# one sensor; a named alert kind is a `by_category` key
curl -sfG --max-time 15 "$AB/api/v1/realtime/incidents" --data-urlencode "limit=1000" \
     --data-urlencode "sensor_id=<stored name>" \
  | jq -en "$VERIFIED" || { echo "Alert Bridge incidents query failed" >&2; exit 1; }

# a time range
curl -sfG --max-time 15 "$AB/api/v1/realtime/incidents" --data-urlencode "limit=1000" \
     --data-urlencode "start_time=<ISO-8601 UTC>" --data-urlencode "end_time=<ISO-8601 UTC>" \
  | jq -en "$VERIFIED" || { echo "Alert Bridge incidents query failed" >&2; exit 1; }

# all pages, when `complete` is false — run it once. Keep the first query's scope: put its sensor_id / start_time /
# category in `set --` (nothing for a store-wide ask) and set END for this ask: its end_time, or now.
# Results keep landing under their event `timestamp`, which can fall inside the window, so pages can
# overlap; the merge keeps one copy per `_id`. Elasticsearch counts and serves at most 10000 raw documents:
# with `capped` true every count is a floor ("at least"); only if the user asks for a finer figure, split
# the window and name each sub-window.
set -- --data-urlencode "sensor_id=<stored name>"
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)   # or the ask's end_time — never one left in the shell by an earlier ask
PAGES=$(mktemp); OFF=0
while :; do
  P=$(curl -sfG --max-time 15 "$AB/api/v1/realtime/incidents" "$@" --data-urlencode "end_time=$END" \
        --data-urlencode "limit=1000" --data-urlencode "offset=$OFF") \
    || { echo "Alert Bridge incidents query failed" >&2; rm -f "$PAGES"; exit 1; }
  printf '%s\n' "$P" >>"$PAGES"
  T=$(printf '%s' "$P" | jq -en 'input | .total | numbers') || { echo "unexpected incidents response" >&2; rm -f "$PAGES"; exit 1; }
  OFF=$((OFF + 1000)); [ "$OFF" -lt "$T" ] && [ "$OFF" -lt 10000 ] || break
done
jq -s '([.[].incidents[]] | unique_by(._id) | sort_by(.timestamp) | reverse) as $all
  | {total: ([.[].total, ($all | length)] | max), incidents: $all}' "$PAGES" \
  | jq -en "$VERIFIED"; RC=$?; rm -f "$PAGES"
[ "$RC" -eq 0 ] || { echo "page merge failed" >&2; exit 1; }
```

The endpoint has no verdict parameter; "how many were confirmed" is the `by_verdict` count (the `by_category` entry for a named kind), and "which were confirmed" selects `.info.verdict == "confirmed"` from the same filtered list. When `complete` is false, every count comes from the all-pages recipe, run once, never from that page. Its output is final: with `capped` true, or `complete` still false because results landed during the read, quote its counts as "at least" and do not run it again.

Reading the results:

- Quote the counts from the jq output, never counted by hand: `verified` pipeline results since `oldest` (`total` counts documents of any kind in scope), `by_verdict` (`empty` = no verdict stamped), `by_category` for a named kind or when several kinds exist, and `ondemand` separately. Then detail the newest results with `category`, timestamp, sensor, verdict, response code (`code`, a string: `"200"` = success; `status` explains another), `reasoning`, and `vlm_response` (pluggable parser) or `description` (free-form answer) when set. An empty `reasoning` is normal: a bare Yes/No answer carries no reasoning text, so say it is empty rather than leaving it out.
- **The stock store keeps at most about 4 hours.** Elasticsearch deletes each whole daily `mdx-vlm-incidents-*` index 4 h after it is created (`ELASTICSEARCH_ILM_MIN_AGE`, default `4h`) and the next result starts a new one, so the window can be much shorter. A "today" or "so far" count covers results since `oldest`, not since midnight or deployment — say so; results before `oldest` may have expired or may never have existed, and you cannot tell which. An empty list can mean expiry as well as detection latency.
- Pipeline documents have no `id` field — their identifier is `Id` / `_id` (the projection shows `.id // .Id // ._id`); on-demand documents carry `id` = the `correlationId`.
- On-demand results (Workflow F) sit in the same store. With the stock Elasticsearch sink they always carry an `id` (the `correlationId`: `ondemand-<uuid>` unless the submitter set one, as it may set `sensorId`), and pipeline results never do, so the program counts every result with an `id` as `ondemand`, never in the pipeline counts, and marks it `ondemand: true` in `newest`. A sensor-scoped query leaves them out unless the submitter used that sensor's name.
- **An empty `verified` list is a valid answer** (with `complete` false, only after the all-pages recipe; on a deployment whose verifier config lists alert-kind types, only after the `mdx-vlm-alerts-*` read above came back empty too). CV detection has latency (stream must be online, detections must trip a Behavior Analytics rule, VLM round-trip), and older results expire (at most about 4 hours are kept). With `ondemand` above 0, report those on-demand results, labelled on-demand, rather than "none". Otherwise report "no verification results yet", optionally note the latency reasons, and STOP — never pad the answer with the rules list, RT-VLM chunks (`info.chunkIdx`, no `verificationResponseCode`; their verdict is the trigger's, not a verifier's), an `mdx-vlm-alerts-*` search on the alerts profile, or invented documents.
  - **On-demand follow-up:** when the question follows an *on-demand* verification (Workflow F), match that request's `correlationId` (= the document `id`) instead of listing the store, and report the VLM `reasoning` / `vlm_response` (still no `verdict` field on a freestyle deploy).
- Never report a verdict you did not read from a returned document.

## Customize CV verifier prompts

Two equivalent surfaces; prefer the REST API on a live deployment (no restart needed).

### REST API (live)

```bash
# Use $AB from parent skill (K8s: ${VSS_PUBLIC_URL}/alert-bridge; Docker: :9080).
: "${AB:?Resolve AB from vss-manage-alerts Deployment prerequisite}"

curl -sf "$AB/api/v1/verification/config" | jq .                 # list all alert-type configs
curl -sf "$AB/api/v1/verification/config/<alert_type>" | jq .    # read one
curl -sf -X PUT "$AB/api/v1/verification/config/<alert_type>" \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "<user prompt>", "system_prompt": "<system prompt>"}'
# POST "" creates a new alert_type (409 if it exists); DELETE removes one (404 if missing)
```

Config fields: `alert_type`, `prompt`, `system_prompt`, `enrichment_prompt`, `vlm_params`, `output_category` (+ server-stamped `created_at`/`updated_at`). `PUT` is a partial update — only the fields you send change. On a deployment with a custom response parser (`docker logs vss-alert-bridge` shows `Pluggable response parser active`), every alert type's prompt feeds that parser: a new or edited prompt must still ask for the parser's JSON fields, or each alert of that type lands as `verification-failed` (`response-parser.md`).

### Config file (deploy-time)

CV-path verifier prompts also live in:

```
deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json
```

Each entry maps a CV `alert_type` (the `category` field emitted by Behavior Analytics) to the VLM `system` / `user` / optional `enrichment` prompts.

Key rules:

- `alert_type` must match the `category` emitted by Behavior Analytics.
- `output_category` is the display name in Elasticsearch / UI.
- `enrichment` triggers a second VLM call for a richer description; requires `alert_agent.enrichment.enabled: true`.
- File edits require an `alert-bridge` container restart to take effect (REST edits do not).

VLM real-time prompts are **not** configured in a file — they are per-request, shaped by `rtvi_prompt_gen` from the user's natural-language detection description.

## Routing guards

- *Was it confirmed / show verdicts / verification results* → **this workflow (B)**: the verifier results in the `/realtime/incidents` raw view, never the rules list. *Exception:* a follow-up to an on-demand verification just run → **Workflow F**, poll `/realtime/incidents` by that request's `correlationId`.
- *What happened / any alerts today* → **Workflow C** (`GET /api/v1/realtime/incidents`), even on a CV deployment.
- *Verify this specific clip/image URL right now* → **Workflow F** (on-demand verification) — its default (incident-kind) result surfaces via `GET /api/v1/realtime/incidents` by `correlationId`; a submission carrying `notification_type: "alert"` lands in `mdx-vlm-alerts-*` instead, which no Alert Bridge endpoint reads.
- Verdict-keyword asks on a **VLM** deployment: explain-only → answer from this reference; execution → the VLM-mode refusal text in SKILL.md (redeploy hint `-m verification`); no auto-redeploy.
