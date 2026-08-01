# Render a search-results view (optional)

Parent: [`../SKILL.md`](../SKILL.md). Runs **after** a search has returned and
you have the `SearchOutput.data` results in hand. It does not replace the
answer — you still report the matches in the reply.

Renderer: [`tools/vss-view`](../../../tools/vss-view/README.md). No service, no
deployment, no `vss-ui` dependency — the output is a plain HTML file the user
opens.

## When to render

Render when the user asks to **see / show / browse / look at** results, when
matches carry thumbnails or clips worth eyeballing, or when there are enough
results (≳6) that a grid beats a prose list.

Skip it for a single match, for a factual one-liner, and for **zero results** —
an empty outcome is a valid answer under the skill's zero-results rule: report
it, keep the selected source, and offer a refinement. Never render an empty
grid instead.

## Always static

Search results are a **snapshot of a query you already ran**, so every block
uses `source.mode: "inline"`. There is no live mode here — re-running a search
is a new search, with its own answer. (Live polling exists for the alerts
incident feed; see `vss-manage-alerts/references/view-artifacts.md`.)

## Build the spec

Map the search output into `media_grid` items, then render:

```bash
cat > /tmp/vss-search-spec.json <<'JSON'
{
  "title": "Search: <the user's query, verbatim>",
  "subtitle": "<N> matches across <M> sources · <range>",
  "slug": "vss-search",
  "theme": "dark",
  "source_note": "search profile @ <host>",
  "blocks": [
    {
      "type": "summary",
      "items": [
        { "label": "Matches", "value": <N> },
        { "label": "Sources", "value": <M> },
        { "label": "Top score", "value": "<score>" }
      ]
    },
    {
      "type": "media_grid",
      "title": "Results",
      "max_items": 24,
      "empty_text": "No matches for this query.",
      "fields": {
        "title": "title", "time": "timestamp", "sensor": "sensor_id",
        "score": "score", "snippet": "description",
        "thumbnail": "thumbnail", "clip": "clip"
      },
      "sensor_names": { "<uuid>": "<name>" },
      "source": { "mode": "inline", "items": [ /* one object per match */ ] }
    }
  ]
}
JSON

python3 "${VSS_REPO_ROOT}/tools/vss-view/render_view.py" \
  --spec /tmp/vss-search-spec.json --out /tmp/vss-search.html --inline-media
```

`--inline-media` embeds thumbnails as data URIs so the file still shows them
after the deployment is gone. Clips stay links either way.

## Rules

- **Media URLs go in verbatim.** The skill's existing rule stands: do not
  rewrite, reconstruct, or substitute a returned media URL — not into the reply
  and not into a spec. Copy `thumbnail` / `clip` exactly as search returned
  them.
- **Never invent results.** Items are verbatim search output. Never
  hand-author a match, a score, a caption, or a count.
- **Scores stay as returned.** Do not rescale, round for effect, or convert to
  a percentage.
- **Title is the user's query.** Verbatim, so the artifact is self-describing
  later.
- **Authenticated media routes** — if thumbnails need an operator-managed
  authenticated route, stop and use it. Never put an API key into a spec, a
  URL, a generated file, or skill output. Omit `--inline-media` rather than
  authenticating a fetch inside the renderer.
- **Sensor names** come from VIOS `GET /vst/api/v1/sensor/list`, same as the
  reply. Unmapped ids show as the raw UUID — acceptable; a guess is not.

## Report it

> Rendered the results: `/tmp/vss-search.html` — open it in a browser.
> Thumbnails are embedded, so it keeps working offline; clip links need the
> deployment to be up.

## Troubleshooting

- **Thumbnails blank / broken** — either the URL was rewritten (don't) or it
  needs an authenticated route. With `--inline-media`, `render_view.py` prints
  an `inline skipped (…)` line per image it could not fetch; those fall back to
  the plain link.
- **Tiles show `—` for everything** — the `fields` dot-paths don't match the
  result objects. Check one with `jq '.data[0]'` and fix the paths.
- **`error: invalid spec — …`** — rejected before writing; the message names
  the exact path.
