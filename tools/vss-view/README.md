# vss-view — read-only view artifacts for VSS skills

Turns a small JSON **view spec** into one self-contained HTML file that shows
VSS alerts or search results. Operate skills emit the spec; this tool emits the
markup.

```bash
python3 tools/vss-view/render_view.py --spec spec.json --out /tmp/vss-alerts.html
# then open the printed path in a browser
```

No service, no container, no deployment. The output is a plain file — `file://`
works. It does not depend on `vss-ui` and works on profiles that drop the
Agent/UI layer entirely.

## Why a spec instead of generated markup

The agent picks a **block type and its parameters**; it never writes HTML or JS.
That keeps the output assertable in a skill's `evals/*.json`, keeps arbitrary
generated code off an origin that can reach `/alert-bridge` and `/vst`, and lets
the tested renderer be reused instead of re-derived per query.

Unknown block types, non-`http(s)` URLs, unknown columns, and sub-second poll
intervals are rejected by `render_view.py` before anything is written. In the
page itself every value is written with `textContent`, and only `http`,
`https`, and `data:image/` URLs are allowed to reach an `href` or `src`.

## Blocks

| Block | For | Notes |
|---|---|---|
| `summary` | KPI row | literal `value`, `value_path` into the raw response, or `count_of: items` |
| `incident_list` | alerts | columns: `time`, `sensor`, `category`, `description`, `verdict`, `media` |
| `media_grid` | search results | thumbnail + score + snippet cards |
| `timeline` | either | counts per time bucket, single-series column chart |

## Sources — the live/static switch

Every block takes a `source`. This is the only difference between a search
report and a live alerts board:

```jsonc
// static: rows baked in at generation time. Portable, survives the deployment.
{ "mode": "inline", "items": [ /* … */ ] }

// live: the page re-fetches in the browser.
{ "mode": "poll",
  "url": "http://<host>:7777/alert-bridge/api/v1/realtime/incidents",
  "params": { "limit": 200 },
  "items_path": "incidents",
  "id_path": "id",
  "interval_ms": 5000 }
```

Polling works from a `file://` page because Alert Bridge ships
`cors.allow_origins: ['*']` (`services/alert/config.yaml`). Blocks that share a
URL share **one** poll loop, so a three-block board is still one request per
tick. The first response is the baseline; only rows that arrive later flash as
new.

A live page carries **Pause** and **Save snapshot**. Save snapshot rewrites the
page's own spec to `inline` with the current rows and downloads a standalone
copy — the renderer travels with it, so the snapshot still opens after the
deployment is gone. That is the artifact to attach to a ticket; a live page is
only meaningful while the deployment is reachable from the viewer's machine.

## Field mapping

Incident documents are free-form nvschema (`additionalProperties: true` in
`services/alert/openapi.json`), so no field path is assumed. `fields` maps
logical names to dot-paths:

```json
"fields": {
  "time": "timestamp", "sensor": "sensor_id", "category": "category",
  "description": "description", "reasoning": "info.reasoning",
  "verdict": "info.verdict", "thumbnail": "info.imagePath", "clip": "info.videoPath"
}
```

A missing path renders `—` rather than breaking the row. `sensor_names` maps
`sensor_id` → the name the agent reverse-resolved from VIOS.

Verdicts render as icon + text + color, never color alone: `confirmed`,
`rejected`, `not-confirmed`, `verification-failed`. An **absent or empty
verdict is a valid state** on the default `use_verdict: false` deploy and shows
as "no verdict" — never as a failure.

## Options

| Flag | Effect |
|---|---|
| `--spec PATH` | spec file, or `-` for stdin (default) |
| `--out PATH` | output `.html` (required) |
| `--inline-media` | fetch `inline`-source thumbnails and embed as data URIs (≤2 MB each) so the file survives the deployment; clips stay links |
| `--theme dark\|light` | override the spec's theme; the page also has a toggle |

`--inline-media` deliberately skips `poll` sources — the browser overwrites
those rows on the first tick.

## Files

| File | |
|---|---|
| `render_view.py` | spec → HTML. Stdlib only. |
| `template.html` | the renderer (CSS + JS). Placeholders: `__VSS_THEME__`, `__VSS_TITLE__`, `__VSS_SPEC__`. |
| `view_spec.schema.json` | full spec schema |
| `examples/alerts-live.json` | live alerts board, 3 blocks on one feed |
| `examples/search-results.json` | static search results grid |

Colors follow the shared data-viz palette: fixed status colors for verdicts
(paired with icon + label), a single sequential blue for the timeline, thin
marks with rounded data-ends, and recessive hairline grid — light and dark both
stepped for their own surface rather than flipped.
