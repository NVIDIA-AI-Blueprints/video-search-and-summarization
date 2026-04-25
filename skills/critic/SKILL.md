---
name: critic
description: Verify video clips against a search query using VLM critique — score clips as confirmed/rejected/unverified, get per-criterion breakdowns. Use when asked to verify search results, score clips against a query, check if a video matches a description, validate a search finding, or run critic verification on clips. Can be triggered directly via a dedicated endpoint or inline during search. Requires the search profile to be deployed.
---

# Critic Agent Workflows

> **Alpha Feature** — not recommended for production use.

The critic agent uses a VLM to evaluate whether a video clip actually matches the original query. It decomposes the query into a **subject** and up to 3 criteria, then uses visual analysis to confirm or reject each criterion — one clip at a time.

## When to Use

- "Verify these search results to remove false positives"
- "Score these clips against the query and tell me which ones actually match"
- "Check if this video really shows a person carrying boxes"
- "Re-run critic on the top 5 search results"
- Any time search results need VLM validation before acting on them

---

## How the Critic Works

1. **Decompose** — The critic breaks the query into a subject (e.g. `subject:person`) and up to 3 criteria (e.g. `carrying boxes`, `wearing red jacket`).
2. **Analyze** — For each video clip, the VLM watches the clip and evaluates whether each criterion is met by the identified subject.
3. **Verdict** — A result is `confirmed` only when ALL criteria pass. Any failing criterion yields `rejected`. If the VLM call errors, the result is `unverified`.
4. **Output** — Each video gets a `result` (`confirmed` / `rejected` / `unverified`) and a `criteria_met` dictionary (`{criterion: bool}`).

The critic is **subject-anchored**: it only confirms a criterion if the *same specific subject* satisfies it. If a different entity satisfies the action (e.g., green team player makes the basket, but the query asked for red team player), it is a **relational failure** and the criterion is `false`.

---

## Gotchas

- **Profile requirement**: the `/api/v1/critic` endpoint is only registered in deployments whose `config.yml` includes it (e.g., `dev-profile-search`). Base, LVS, and alerts profiles do NOT expose it by default. If 404 is received, see [troubleshooting.md](references/troubleshooting.md).
- **VLM dependency**: `unverified` verdicts almost always mean the VLM is unreachable or overloaded, not that the clip is bad. Check VLM health before concluding results are poor.
- **Timestamps must be ISO 8601 UTC** — e.g. `"2025-08-25T03:05:55Z"`. The critic rejects or silently fails on other formats.
- **`sensor_id` must be a VST UUID** — not a friendly camera name. Use the `vios` skill to list sensors and map names to IDs.
- **Subject anchoring means relational failures are strict** — if a different person (not the described subject) carries boxes, the criterion is `false`. This is by design.
- **`criteria_met` may be empty for `unverified` results** — always check `result` first, then inspect `criteria_met`.
- ALWAYS step into [troubleshooting.md](references/troubleshooting.md) if all results are `unverified` or `critique_result` is null across all items.

---

## Mandatory Workflow

When using this skill, ALWAYS follow this high-level workflow:

1. Resolve inputs from the conversation or user query.
2. Call the critic endpoint (direct or inline — choose per situation below).
3. Present results as a professional verification report but name it `Critic Verification`:
   - Section per clip: sensor ID, time window, verdict, criteria breakdown
   - Summary table: total confirmed / rejected / unverified counts
   - Write like a technical audit report, not a chat message
4. CRITICAL: Verify the outcomes and explain them to the user concisely.
   If ALL results come back `unverified`, or the endpoint returns an error, STOP.
   Do not proceed without reading [troubleshooting.md](references/troubleshooting.md) to diagnose and iterate.

---

## Input Resolution

Infer these inputs from the conversation or user query. If any cannot be inferred, ask immediately:

- **`$HOST_IP`** *(always required)*: hostname or IP where the VSS agent backend runs (port 8000).
- **`query`** *(always required)*: the original search query used to find the clips — this becomes the critic's decomposition target.
- **`videos`** *(always required)*: list of clips to evaluate, each needing:
  - `sensor_id`: the VST sensor UUID. ALWAYS use the `vios` skill to list sensors if only a name is known.
  - `start_timestamp`: ISO 8601 UTC (e.g., `"2025-08-25T03:05:55Z"`)
  - `end_timestamp`: ISO 8601 UTC
  These are typically taken directly from search result hits (the `sensor_id`, `start_timestamp`, `end_timestamp` fields).
- **`evaluation_count`** *(optional)*: max number of clips to evaluate in this call (defaults to server config, usually 5). Increase when evaluating a larger batch.

---

## Approach

```bash
curl -s -X POST http://${HOST_IP}:8000/api/v1/critic \
  -H "Content-Type: application/json" \
  -d '{
    "query": "person carrying boxes",
    "videos": [
      {
        "sensor_id": "<sensor-uuid>",
        "start_timestamp": "2025-08-25T03:05:55Z",
        "end_timestamp": "2025-08-25T03:06:15Z"
      },
      {
        "sensor_id": "<sensor-uuid>",
        "start_timestamp": "2025-08-25T04:10:00Z",
        "end_timestamp": "2025-08-25T04:10:20Z"
      }
    ],
    "evaluation_count": 5
  }' | jq .
```

### Response shape

```json
{
  "video_results": [
    {
      "video_info": {
        "sensor_id": "<sensor-uuid>",
        "start_timestamp": "2025-08-25T03:05:55Z",
        "end_timestamp": "2025-08-25T03:06:15Z"
      },
      "result": "confirmed",
      "criteria_met": {
        "subject:person": true,
        "carrying boxes": true
      }
    },
    {
      "video_info": {
        "sensor_id": "<sensor-uuid>",
        "start_timestamp": "2025-08-25T04:10:00Z",
        "end_timestamp": "2025-08-25T04:10:20Z"
      },
      "result": "rejected",
      "criteria_met": {
        "subject:person": true,
        "carrying boxes": false
      }
    }
  ]
}
```

### Verdict meanings

| `result`     | Meaning                                                                 |
|--------------|-------------------------------------------------------------------------|
| `confirmed`  | VLM verified all criteria are met by the identified subject             |
| `rejected`   | At least one criterion failed (check `criteria_met` for the breakdown)  |
| `unverified` | VLM call errored out — treat as inconclusive, not as a pass             |
