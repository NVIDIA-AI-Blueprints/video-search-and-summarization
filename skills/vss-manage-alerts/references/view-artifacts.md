# Render an incident view (Workflow C, optional)

Parent: [`../SKILL.md`](../SKILL.md). Runs **after** Workflow C's
`GET $AB/api/v1/realtime/incidents` call has returned. This never replaces the
answer — you still report the incidents in the reply. It adds a browsable page
on top.

Renderer: [`tools/vss-view`](../../../tools/vss-view/README.md) in the repo
checkout. No service, no deployment, no `vss-ui` dependency — the output is a
plain HTML file the user opens.

## When to render

Offer or produce a view when the user asks to **see / show / watch / monitor**
incidents, asks for something to keep open, or when the result set is large
enough that a table beats prose (≳10 incidents).

Do **not** render for a one-line factual answer ("any alerts today?" → 3) or
when `incidents` came back empty — an empty result is a valid answer; report it
and stop.

**Never render instead of answering.** The reply states what the incidents were;
the file is an extra.

## Live or static

| Ask | `source.mode` |
|---|---|
| "watch", "monitor", "keep it updating", "real-time" | `poll` |
| "show me what happened", a closed time range, something to share or attach | `inline` |

A `poll` page only works while the deployment is reachable **from the viewer's
browser** — say so when you hand over the link. A static page keeps working
afterwards, which is what a ticket attachment needs.

## Build the spec

Resolve `$AB` exactly as Workflow C does. Use the **same** URL, params, and
`items_path` for every block so they share one poll loop.

```bash
cat > /tmp/vss-alerts-spec.json <<JSON
{
  "title": "Real-time alerts",
  "subtitle": "Alert Bridge realtime incident store — <scope>, <window>",
  "slug": "vss-alerts",
  "theme": "dark",
  "source_note": "alerts profile @ <host>",
  "blocks": [
    {
      "type": "summary",
      "source": { "mode": "poll", "url": "${AB}/api/v1/realtime/incidents",
                  "params": { "limit": 200 }, "items_path": "incidents",
                  "id_path": "id", "interval_ms": 5000 },
      "items": [
        { "label": "Total matches", "value_path": "total" },
        { "label": "On this page", "count_of": "items" }
      ]
    },
    {
      "type": "timeline", "title": "Incidents per 5 minutes",
      "bucket_minutes": 5, "fields": { "time": "timestamp" },
      "source": { "mode": "poll", "url": "${AB}/api/v1/realtime/incidents",
                  "params": { "limit": 200 }, "items_path": "incidents",
                  "id_path": "id", "interval_ms": 5000 }
    },
    {
      "type": "incident_list", "title": "Incidents",
      "columns": ["time", "sensor", "category", "description", "verdict", "media"],
      "max_rows": 50,
      "empty_text": "No incidents in this window.",
      "fields": {
        "time": "timestamp", "sensor": "sensor_id", "category": "category",
        "description": "description", "reasoning": "info.reasoning",
        "verdict": "info.verdict", "thumbnail": "info.imagePath",
        "clip": "info.videoPath"
      },
      "sensor_names": { "<uuid>": "<name>" },
      "source": { "mode": "poll", "url": "${AB}/api/v1/realtime/incidents",
                  "params": { "limit": 200 }, "items_path": "incidents",
                  "id_path": "id", "interval_ms": 5000 }
    }
  ]
}
JSON

python3 "${VSS_REPO_ROOT}/tools/vss-view/render_view.py" \
  --spec /tmp/vss-alerts-spec.json --out /tmp/vss-alerts.html
```

For a **static** view, swap each `source` for the rows you already fetched:

```bash
curl -sf "${AB}/api/v1/realtime/incidents?limit=200" > /tmp/incidents.json
# then build blocks with:
#   "source": { "mode": "inline", "items": <.incidents>, "raw": <whole response> }
```

Add `--inline-media` on a static render to embed thumbnails as data URIs, so
the file still shows them after the deployment is gone.

## Rules

- **Never invent rows.** `inline` items are verbatim API output. Never
  hand-write an incident, a count, a timestamp, or a sensor name into a spec.
- **`params` must match what you actually queried.** The page re-runs that exact
  request; a spec that widens the filter shows the user something different from
  what you reported.
- **Reverse-resolve sensors properly.** `sensor_names` maps `sensor_id` → name
  from VIOS `GET /vst/api/v1/sensor/list`, same as the reply. Unmapped ids
  render as the raw UUID — acceptable; a guessed name is not.
- **`interval_ms` ≥ 5000** unless the user asked for faster. Every block on a
  shared URL is one request per tick, but a 1 s board still means 60 requests a
  minute against Alert Bridge.
- **View only.** The page never writes to VSS. Rule create/stop stays in
  Workflow D; do not present the page as a way to manage alerts.
- **Absent `verdict` is normal.** On the default `use_verdict: false` deploy it
  renders "no verdict". Never describe that as an error.

## Report it

Give the absolute path and say what it is:

> Rendered a live view: `/tmp/vss-alerts.html` — open it in a browser. It
> refreshes every 5 s from Alert Bridge and is view-only. **Pause** stops
> polling; **Save snapshot** downloads a standalone copy with the current rows
> baked in, which keeps working after the deployment goes away.

Drop the refresh sentence for a static render.

## Troubleshooting

- **"Disconnected — Failed to fetch"** — the browser cannot reach `$AB`. The
  page uses the URL in its spec verbatim: a container-internal host or
  `localhost` minted on the deploy host is unreachable from a laptop. Rebuild
  the spec with the origin the *viewer* can reach.
- **Rows render but every field is `—`** — the `fields` dot-paths don't match
  these documents. Incident documents are free-form nvschema; check one with
  `jq '.incidents[0]'` and fix the paths.
- **Timeline says "Nothing to plot yet"** — no item had a parseable
  `fields.time`. Confirm the path and that the value is ISO-8601 or epoch.
- **`error: invalid spec — …`** — `render_view.py` rejected it before writing.
  The message names the exact path; fix that key rather than removing the block.
