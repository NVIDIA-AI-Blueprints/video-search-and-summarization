# Incident range report (Mode B)

Numbered steps for Mode B, loaded on demand from [`SKILL.md`](../../SKILL.md) (`$SKILL_DIR/SKILL.md`); routing, gates and the shared setup live there and the skeleton is defined in `SKILL.md` § Report types.

- **Trigger phrases:** `SKILL.md` § Examples — the Mode B rows.
- **Prerequisite gate:** `SKILL.md` § Runtime prerequisites — the *Mode-by-mode checklist* row(s) for Mode B; no HITL step.
- **Inputs to resolve:** Step 1 below.
- **Template:** Step 3 below.
- **Output contract:** `SKILL.md` § Instructions → *Output contract for evaluators*, the Mode B lines.
- **Failure modes:** `SKILL.md` § Error Handling, plus the rules stated in the steps below.

---

## Mode B — Report on incidents in a time range

### Step 1 — Resolve the time range and (optionally) sensor

- `start_time` / `end_time` must be ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SS.sssZ`). Resolve relative phrases ("last hour", "today") against the current host clock.
- If the user names a sensor, capture it as `source` + `source_type=sensor`. Otherwise leave both unset for an all-sensors query.

### Step 2 — Fetch incidents via `/vss-query-analytics`

Hand off to `/vss-query-analytics` (initialize → `tools/call`) with:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "video_analytics__get_incidents",
    "arguments": {
      "source": "<sensor-id-or-omit>",
      "source_type": "sensor",
      "start_time": "<ISO>",
      "end_time": "<ISO>",
      "max_count": 100,
      "includes": ["objectIds", "info"]
    }
  },
  "id": 1
}
```

Read-only boundary (mandatory):
- Mode B is strictly read-only analytics retrieval. Never write, seed, backfill, or mutate Elasticsearch/VA data.
- Forbidden examples: indexing synthetic incidents, replaying fixture payloads into ES, calling write/update/delete APIs to "make data available" for the report.
- If no incidents exist for the requested range/scope, handle as empty results (see below); do not fabricate data.

For each incident keep: `id`, `sensorId`, `timestamp`, `end`, `category`, `place.name`, `info.verdict`, `info.reasoning`, `objectIds`, and the clip URL (commonly `info.clip_url`, `clip_url`, or whichever clip-pointer field the response carries). **Apply the browser-playable rewrite (see *Clip URLs: VLM input vs browser report link* in SKILL.md — `VSS_PUBLIC_URL` on Kubernetes, or `$VSS_PUBLIC_HOST:$VSS_PUBLIC_PORT` on Docker) to every clip URL before pasting it into the report** — the raw value is often a private `HOST_IP:30888` URL the user's browser cannot reach.

### Step 3 — Fill the Incident Range Report template

Load the matching template from [`$SKILL_DIR/references/report-templates/incident-range-report.md`](../report-templates/incident-range-report.md). Treat the template as read-only — copy its structure, then group by sensor (or by category if no sensor scope), tally verdicts, and list each incident with timestamp / category / verdict / reasoning. Fill all placeholders before returning markdown. Never leave template instructions, placeholder tokens, or internal-only URLs in user output. Every incident clip value must be a rewritten browser-playable URL; omit the clip line when the incident carries no clip URL.

For non-empty results, rendered output MUST start exactly with:
- `# Incident Range Report`
- `## Basic Information`
- a pipe table containing rows: `Report Identifier`, `Range`, `Scope`, `Total Incidents`, `Confirmed / Rejected / Unverified`

If `get_incidents` returns zero results, STOP and return exactly this one-line sentence shape (single line only):
`No incidents found for scope <scope> in range <start_time> to <end_time>.`

When zero results:
- Do not render `# Incident Range Report`.
- Do not render `## Basic Information`.
- Do not render any markdown table, bullets, or summary section.
- Do not invent incidents, do not seed test data, and do not fall back to Mode A.
