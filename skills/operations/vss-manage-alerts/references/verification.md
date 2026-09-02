# Verification Results & Verdicts (Workflow B — CV mode)

Operational reference for **Workflow B** on a **CV (verification)** deployment: how a CV alert becomes a verdict, how to inspect verification results, and how to customize the VLM-verifier prompts. Execution requires CV mode; *explain-only* asks ("how does verification work?") are answerable in any mode from this background. VLM real-time mode has no separate verdict field (see the verdict table's last note).

## The verification pipeline

```
camera (VIOS/VST)
  → vss-rtvi-cv            RT-CV perception (Grounding DINO object detections)
  → vss-behavior-analytics rule engine → candidate alert (PPE / ladder / proximity / restricted-area …)
  → vss-alert-bridge       VLM verifier: fetches the clip from VIOS, prompts per alert_type
  → vss-rtvi-vlm           vision-language inference (runs in both modes)
  → Elasticsearch          verified document in mdx-vlm-alerts-* (alert-kind store)
```

- Candidate alerts reach Alert Bridge over Kafka (or `POST /api/v1/alerts` for `nv.Behavior` submissions); Alert Bridge looks up the verifier prompts by the alert's `category` (= `alert_type`), calls the VLM on the clip, and stamps the result into the document's `info` block before publishing.
- This whole path exists only when the alerts profile is deployed with `-m verification` (`MODE=2d_cv`). Deploy/mode-switch belongs to `vss-deploy-profile` — never duplicate it here.
- The **real-time** path (VLM mode, Workflow D rules) writes *incident-kind* results to `mdx-vlm-incidents-*`, queryable via Workflow C's `GET /api/v1/realtime/incidents`. The two stores do not mix.

## Verdict interpretation

Verified CV alerts carry an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `not-confirmed` | VLM response could not be parsed into a confirmed/rejected verdict (parse failure) |
| `verification-failed` | Verification could not complete — API/VLM error |
| `""` (empty) | No verdict parsing ran. Either the **default `use_verdict: false` freestyle deploy** — the common case: the raw VLM text is stored in `reasoning` / `vlm_response` and no verdict is stamped — or a pluggable response parser replaced the verdict path. An empty verdict is a **valid success, not a failure**; report the VLM text, never invent a `confirmed`/`rejected`. |

- Companion fields (camelCase, inside `info`): `verificationResponseCode` (HTTP-like; `200` = success), `verificationResponseStatus` (`OK` or an error description), `reasoning` (the VLM's explanation), and `vlm_response` (pluggable-parser output only).
- VLM real-time mode incidents are always "confirmed" at source (the trigger itself is a Yes/No VLM answer), so there is **no** separate verdict field in VLM mode.

## Inspecting verification results — interim ES probe

> **Interim path (Docker only).** The `mdx-vlm-alerts-*` store has **no REST query endpoint yet** — Workflow C's `/incidents` reads only the real-time incident store and will NOT surface these documents. A dedicated Alert Bridge query endpoint is planned; until it lands, query Elasticsearch (`:9200`, host-published) **on Docker only**. Elasticsearch is **not** on the public Ingress — on Kubernetes do not probe `:9200`, do not `kubectl port-forward`, and report that CV verdict inspection via ES is unavailable over `VSS_PUBLIC_URL`.

```bash
# Docker only — ES is not published on Kubernetes Ingress.
ES="http://${HOST_IP}:9200"

# latest verification results
curl -sf "$ES/mdx-vlm-alerts-*/_search?size=10&sort=@timestamp:desc" | jq '.hits.hits[]._source'

# by alert category (alert_type / output_category as shown in ES)
curl -sf "$ES/mdx-vlm-alerts-*/_search" -H 'Content-Type: application/json' -d '{
  "size": 10, "sort": [{"@timestamp": "desc"}],
  "query": {"match": {"category": "<alert_type>"}}
}' | jq '.hits.hits[]._source'
```

**An absent `mdx-vlm-alerts-*` index IS the answer: no CV verdicts recorded
yet.** The index is created by the first verification write, so "index not
found" and "zero hits" mean the same thing — report it. Do **not** fall back to
`mdx-vlm-incidents-*`. That is the real-time incident store answering a
different question, and sourcing a CV-verdict answer from it reports someone
else's data as this one's.

```bash

# by verdict
curl -sf "$ES/mdx-vlm-alerts-*/_search" -H 'Content-Type: application/json' -d '{
  "size": 10,
  "query": {"match": {"info.verdict": "confirmed"}}
}' | jq '.hits.hits[]._source'

# by time range
curl -sf "$ES/mdx-vlm-alerts-*/_search" -H 'Content-Type: application/json' -d '{
  "size": 50,
  "query": {"range": {"@timestamp": {"gte": "now-24h"}}}
}' | jq '.hits.hits[]._source | {category, timestamp, verdict: .info.verdict}'

# on-demand (Workflow F) results land here ONLY when submitted with notification_type:"alert";
# the default minimal submission is incident-kind -> use GET $AB/api/v1/realtime/incidents instead
curl -sf "$ES/mdx-vlm-alerts-*/_search?q=<correlationId>" | jq '.hits.hits[]._source'
```

Reading the results:

- Summarize each hit's `category`, timestamp, sensor, `info.verdict`, and `info.reasoning`. Accept camelCase or snake_case on the response-code field (`verificationResponseCode` / `verification_response_code`) — index mappings have varied.
- **Zero hits is a valid answer.** CV detection has latency (stream must be online, detections must trip a Behavior Analytics rule, VLM round-trip). Report "no verification results yet", optionally note the latency reasons, and STOP — never pad the answer with the rules list, `/incidents`, the `mdx-vlm-incidents-*` ES index (incident-kind docs carry no verdicts — a populated incidents index next to an empty alerts index is normal, not a substitute), or invented documents.
  - **One exception — on-demand follow-up:** when the question follows an *on-demand* verification (Workflow F), its default result is incident-kind and lands in `/incidents`, not this store. Poll `GET $AB/api/v1/realtime/incidents` by the request's `correlationId` and report the VLM `reasoning` / `vlm_response` (still no `verdict` field on a freestyle deploy) instead of answering "no results".
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

Config fields: `alert_type`, `prompt`, `system_prompt`, `enrichment_prompt`, `vlm_params`, `output_category` (+ server-stamped `created_at`/`updated_at`). `PUT` is a partial update — only the fields you send change.

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

- *Was it confirmed / show verdicts / verification results* → **this workflow (B)**: ES probe on `mdx-vlm-alerts-*`, never the rules list. *Exception:* a follow-up to an on-demand verification just run → **Workflow F**, poll `/realtime/incidents` by `correlationId` (that result is incident-kind, not in this store).
- *What happened / any alerts today* → **Workflow C** (`GET /api/v1/realtime/incidents`), even on a CV deployment.
- *Verify this specific clip/image URL right now* → **Workflow F** (on-demand verification) — its default (incident-kind) result surfaces via `GET /api/v1/realtime/incidents`, NOT this store; it lands here only when the submission carried `notification_type: "alert"`.
- Verdict-keyword asks on a **VLM** deployment: explain-only → answer from this reference; execution → the VLM-mode refusal text in SKILL.md (redeploy hint `-m verification`); no auto-redeploy.
