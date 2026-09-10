# SOP compliance report (Mode C)

Numbered steps for Mode C, loaded on demand from [`SKILL.md`](../../SKILL.md) (`$SKILL_DIR/SKILL.md`); routing, gates and the shared setup live there and the skeleton is defined in `SKILL.md` § Report types.

- **Trigger phrases:** `SKILL.md` § Examples — the Mode C rows.
- **Prerequisite gate:** `SKILL.md` § Runtime prerequisites — the *Mode-by-mode checklist* row(s) for Mode C and the `tools/list` gate in its probe block — Step 1 below enforces it; no HITL step, no report-time VLM.
- **Inputs to resolve:** Step 1 below.
- **Template:** Step 3 below.
- **Output contract:** `SKILL.md` § Instructions → *Output contract for evaluators*, the Mode C lines.
- **Failure modes:** `SKILL.md` § Error Handling, plus the rules stated in the steps below.

---

## Mode C — SOP compliance report

Use for "generate an SOP compliance report" over a sensor + time range. Data comes from the SOP tools on VA-MCP (added by the SOP profile); this skill aggregates and renders the template itself.

### Step 1 — Resolve the sensor + time range

- Capture the named sensor as `sensor_id`. `start_time` / `end_time` are ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SS.sssZ`); resolve relative phrases ("last hour", "today") against the host clock.
- Confirm the SOP tools are present (once). The four `get_sop_*` tools are added by the SOP patch and are **not** in the base `/vss-query-analytics` tool set, so call the VA-MCP endpoint directly (two-step MCP JSON-RPC: `initialize` → `tools/list`):

```bash
# Each fenced block is its own shell — re-derive VA-MCP here (do not rely on
# SKILL.md § Endpoint resolution). Force public path when VSS_PUBLIC_URL is set.
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  VA_MCP_URL="${VSS_PUBLIC_URL%/}/va-mcp"
else
  VA_MCP_URL="http://${HOST_IP:-localhost}:9901"
fi
MCP="${VA_MCP_URL%/}/mcp"
CT='Content-Type: application/json'; AC='Accept: application/json, text/event-stream'
SID=$(curl -si --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
[ -n "$SID" ] || { echo "VA-MCP initialize failed (no session id) — is VA-MCP up at ${VA_MCP_URL}?" >&2; exit 1; }
# Same envelope selection as Step 2: the JSON-RPC response to OUR request (id 1) from SSE `data:` events or a
# plain-JSON body (exactly one), and tell "the call failed" apart from "the tool is missing" before pointing at a rebuild.
LIST=$(curl -sS --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}') || { echo "VA-MCP tools/list failed (transport error above)" >&2; exit 1; }
ENVELOPE=$(printf '%s\n' "$LIST" | grep '^data: *{' | sed 's/^data: *//' | jq -c 'select(type=="object" and .id==1)' 2>/dev/null)
[ -n "$ENVELOPE" ] || ENVELOPE=$(printf '%s' "$LIST" | jq -c 'select(type=="object" and .id==1)' 2>/dev/null)
[ "$(printf '%s\n' "$ENVELOPE" | grep -c '^{')" = 1 ] && printf '%s' "$ENVELOPE" | jq -e '(.result.tools | type) == "array"' >/dev/null \
  || { echo "VA-MCP tools/list did not return exactly one id-1 response holding a tool list (empty, non-JSON-RPC, error envelope, or duplicate responses) — VA-MCP problem, not a missing SOP patch:" >&2; printf '%s\n' "$LIST" >&2; exit 1; }
printf '%s' "$ENVELOPE" | jq -r '.result.tools[].name' | grep -qx video_analytics__get_sop_report \
  || { echo "SOP tools absent — deployment lacks the SOP patch; hand off to /vss-build-vision-ai to compose the SOP profile" >&2; exit 1; }
```

(No bash arrays — POSIX-`sh` safe; the session id is guarded, a failed or malformed `tools/list` exits non-zero as a VA-MCP problem, and the tool check exits non-zero with its own message when `get_sop_report` is missing.)

### Step 2 — Fetch the aggregated SOP report from VA-MCP

Call `video_analytics__get_sop_report` on the same endpoint. Each fenced block runs as its own shell, so `$MCP` / `$SID` / `$CT` / `$AC` from Step 1 do NOT carry over — re-establish them and re-`initialize` for a fresh session id here:

```bash
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  VA_MCP_URL="${VSS_PUBLIC_URL%/}/va-mcp"
else
  VA_MCP_URL="http://${HOST_IP:-localhost}:9901"
fi
MCP="${VA_MCP_URL%/}/mcp"
CT='Content-Type: application/json'; AC='Accept: application/json, text/event-stream'
SID=$(curl -si --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
[ -n "$SID" ] || { echo "VA-MCP initialize failed (no session id) — is VA-MCP up at ${VA_MCP_URL}?" >&2; exit 1; }
# Keep the raw response: classify transport / JSON-RPC / tool failures BEFORE extracting the text.
BODY_FILE=$(mktemp) || exit 1
trap 'rm -f "$BODY_FILE"' EXIT
CODE=$(curl -sS --max-time 30 -o "$BODY_FILE" -w '%{http_code}' -X POST "$MCP" -H "$CT" -H "$AC" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_sop_report","arguments":{"sensor_id":"<sensor>","start_time":"<ISO>","end_time":"<ISO>"}},"id":2}') \
  || { echo "get_sop_report: curl failed (transport error above)" >&2; cat "$BODY_FILE" >&2; exit 1; }
[ "$CODE" = "200" ] || { echo "get_sop_report tools/call failed: HTTP $CODE" >&2; cat "$BODY_FILE" >&2; exit 1; }
# Pick the JSON-RPC response to OUR request (id 2) — exactly one, from SSE `data:` events or a plain-JSON body.
ENVELOPE=$(grep '^data: *{' "$BODY_FILE" | sed 's/^data: *//' | jq -c 'select(type=="object" and .id==2)' 2>/dev/null)
[ -n "$ENVELOPE" ] || ENVELOPE=$(jq -c 'select(type=="object" and .id==2)' "$BODY_FILE" 2>/dev/null)
[ "$(printf '%s\n' "$ENVELOPE" | grep -c '^{')" = 1 ] || { echo "get_sop_report: expected exactly one JSON-RPC response with id 2 (body empty, non-JSON-RPC, unparseable, or duplicate responses)" >&2; cat "$BODY_FILE" >&2; exit 1; }
printf '%s' "$ENVELOPE" | jq -e 'has("error") | not' >/dev/null || { echo "get_sop_report: JSON-RPC error" >&2; printf '%s\n' "$ENVELOPE" >&2; exit 1; }
printf '%s' "$ENVELOPE" | jq -e '(.result | type) == "object" and .result.isError != true' >/dev/null || { echo "get_sop_report: missing result or tool returned isError" >&2; printf '%s\n' "$ENVELOPE" >&2; exit 1; }
# The text may be the empty-range sentinel {"error": "No VisionLLM messages found for the given filters."} — Step 3 handles it.
printf '%s' "$ENVELOPE" | jq -er '.result.content[0].text | select(type=="string" and length>0)' \
  || { echo "get_sop_report: result carries no text content" >&2; printf '%s\n' "$ENVELOPE" >&2; exit 1; }
```

Returns `report_summary` (total messages, current / completed cycle, compliance status), `sop_violations` (missing / mis-ordered steps per cycle with timestamps), `actions_observed` (`total_action_entries`, `unique_actions`, `latest_action` — `total_action_entries` counts the action entries actually recorded, NOT the message count: a chunk whose VLM response is null/empty yields no action entry, so it can be far below total messages), and a `formatted_report` markdown string.

Read-only boundary (mandatory): Mode C is strictly read-only. Never write, seed, backfill, or mutate Elasticsearch/VA data. **Reproduce the tool's numbers and action names verbatim** — DS-SOP actions are numbered classifications (e.g. "(1) first fan", "(10) not belong"); never paraphrase, rename, or invent them.

### Step 3 — Fill the SOP Compliance Report template

Copy [`$SKILL_DIR/references/report-templates/sop-compliance-report.md`](../report-templates/sop-compliance-report.md), fill every placeholder from the Step 2 result (message count, compliance status, cycle counts, the missing / mis-ordered step tables, actions observed — set `{total_actions}` to `actions_observed.total_action_entries`, the field upstream `get_sop_report` itself maps to Total Actions Recorded; it is NOT `report_summary.total_messages_analyzed`: chunks with a null/empty VLM response record no action entry, so never substitute the message count for it, and treat a missing `total_action_entries` key as a schema mismatch to report, not a reason to fall back), and return the rendered markdown. For placeholders `get_sop_report` does not carry: generate `{report_id}` + `{report_date}`, set `{agent_version}` to `vss-generate-video-report (Mode C)`, and set `{video_analysis_details}` / `{snapshot_image}` to `N/A` (Mode C runs no report-time VLM and fetches no media). Fill `{notes}` with the data provenance and snapshot caveats (source/scope, the bounded `end_time` used, the doc count vs the 1000-doc `get_sop_report` cap, and that a live stream never reaches EOS so `final_*` counts stay 0 and every violation is per-chunk); fill `{recommendations}` with the compliance interpretation (recurring missing / mis-ordered steps and whether they reflect the source clip rather than an operator fault). Keep the source asset unchanged; never leave a placeholder, and never include template instructions in a filled cell.

`get_sop_report` reports an empty range as the tool result `{"error": "No VisionLLM messages found for the given filters."}`: on that result, STOP and return exactly one plain-text line: `No SOP messages found for sensor <sensor_id> in range <start_time> to <end_time>.` Inspect the raw `tools/call` response before trusting `.result.content[0].text`: a failed call, an empty body or one with no JSON-RPC response for the request (neither SSE `data:` events nor plain JSON), a JSON-RPC `error` envelope, `result.isError: true`, or any other error text is a failure — do NOT render that line; surface it per `SKILL.md` § Error Handling. In either case do not render the full template, invent data, or fall back to another mode.
