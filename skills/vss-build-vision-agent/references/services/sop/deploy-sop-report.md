# Deployment Reference: SOP Report Tools (VA-MCP)

Deploys **`vss-va-mcp`** (with the SOP `video_analytics` patch) on top of a running
DS-SOP + ELK stack, in the SAME compose project (`mdx`). This is the only service
the SOP report path needs — reports are rendered by the `vss-generate-video-report`
skill (Mode C), not by any deployed agent. Run as part of build-vision-agent's
deploy flow; also runnable standalone.

## Image / GPU

- `vss-va-mcp` — `ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest` (from the Foundation),
  `mcp serve`. No local build. **No GPU, no LLM, no API key.**

## Prerequisites

1. DS-SOP stack up in project `mdx` with data:
   `curl -s 'http://localhost:9200/_cat/indices/mdx-vlm-captions*?v'` → docs.count > 0.
2. `docker login ghcr.io` if needed. Port `9901` free.

## Deploy Steps (build-vision-agent's deploy flow / the operator performs these)

**1. Resolve the site-packages path** (the patch mount target):
```bash
docker run --rm --entrypoint /vss-agent/.venv/bin/python3 ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest \
  -c "import agent,os;print(os.path.dirname(os.path.dirname(agent.__file__)))"
```
Set `VSS_AGENT_SITE_PACKAGES` in `generated.env` if it differs from the default.

**2. Download + adapt the SOP release patch, then stage it** at
`${VSS_APPS_DIR}/services/agent/sop-report/{video_analytics,configs}/` (mounted read-only).
The patch is **not shipped in this repo** — download it from NVIDIA's public repo and adapt
it to this image per `../sop.md § Patch specifics` (reuse the `build-ds-sop.md § Step 0`
clone). Stage the adapted `tools.py`, `sop_tools.py`, and VA-MCP config there;
`VSS_VA_MCP_CONFIG_FILE` already points at the staged config.

**3. `override.env`** — write the SOP delta per `../sop.md` § Delta composition
(`FOUNDATION=alerts` + the effective `COMPOSE_PROFILES` with `ds-sop` added and
RT-VLM/agent/UI removed + the DS-SOP / report knobs). No credentials needed.

**4. Dry-run + normalize** (apply the patch overlay as an extra `-f`):
```bash
docker compose --env-file <generated.env> -f <BUILD_DIR>/compose.yml \
  -f $DST/sop-report-override.yml config > resolved.sop-report.yml
grep -c 'sop-report/video_analytics' resolved.sop-report.yml   # expect 2 (patch mounts on vss-va-mcp)
uv run skills/vss-deploy-profile/scripts/normalize_resolved_yml.py resolved.sop-report.yml
```

**5. Bring up just the MCP** (leaves DS-SOP/ELK untouched):
```bash
docker compose --env-file <generated.env> -f resolved.sop-report.yml up -d vss-va-mcp
```

## Known Deployment Issues

- **`get_sop_*` missing from VA-MCP** → patch didn't mount. `docker inspect vss-va-mcp`
  shows the two `sop-report/video_analytics` binds; `VSS_AGENT_SITE_PACKAGES` matches
  the image python minor (Step 1). Verify:
  `docker exec vss-va-mcp /vss-agent/.venv/bin/python3 -c "import agent.video_analytics.sop_tools"`.
- **`get_sop_report` empty/error** → `mdx-vlm-captions-*` empty or the SOP JSON
  Logstash pipeline not registered (`deploy-ds-sop.md`).

## Testing

Report generation is exercised by the `vss-generate-video-report` skill (Mode C).
This layer's own pass criteria:

1. **Health** — `curl -sf http://<host>:9901/health` → 200.
2. **SOP tools present** — MCP `tools/list` on `:9901` includes the four
   `video_analytics__get_sop_*` (two-step JSON-RPC: initialize → tools/list).
3. **Real data** — `video_analytics__get_sop_report` for the sensor returns
   `report_summary` / `sop_violations` / `actions_observed` / `formatted_report`
   (NOT an error), consistent with the raw `mdx-vlm-captions-*` docs.
4. **End-to-end via the skill** — run `vss-generate-video-report` Mode C ("Generate
   an SOP compliance report for sensor `<id>` from `<t1>` to `<t2>`"); it returns
   markdown titled `# SOP Compliance Report` with real numbered steps / counts.

MCP two-step probe:
```bash
SID=$(curl -si -X POST http://<host>:9901/mcp -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')
curl -s -X POST http://<host>:9901/mcp -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_sop_report","arguments":{"sensor_id":"<sensor>"}},"id":2}' \
  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text' | head -40
```
