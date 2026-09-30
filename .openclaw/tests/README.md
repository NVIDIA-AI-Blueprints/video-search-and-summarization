# NemoClaw runtime instruction tests

Run deterministic instruction, notebook refresh, and workspace migration checks:

```bash
python3 -m unittest discover -s .openclaw/tests -p test_runtime_instructions.py
```

## Live UI scenarios (opt-in)

With a running NemoClaw/LVS deployment, install Playwright and Chromium in your
test environment (`npm install playwright`, `npx playwright install chromium`).
Set `NODE_PATH` if that environment's node_modules is outside this directory.

Set these environment variables locally; never commit credentials:

- `VSS_TEST_UI_URL`: deployed UI origin.
- `VSS_TEST_GATEWAY_TOKEN`: gateway token from your sandbox's `gateway-token --quiet`.
- `VSS_TEST_VIDEO`: name of an existing uploaded warehouse video (a short clip is sufficient).
- `VSS_TEST_STREAM`: name of an existing registered stream.
- Optional `VSS_TEST_RESULTS_FILE`: private, ignored output path for run IDs and answers.

Refresh the sandbox policy by rerunning notebook section 3.2, then start the test
in a new chat session. The notebook replaces only the managed stream-preflight
section of AGENTS.md, preserving other local instructions even when reusing a
sandbox. No shared operational skills are changed.

```bash
node .openclaw/tests/stream_preflight_ui.cjs
```

This test uses the actual UI and agent, incurs model calls, and creates one
uploaded-video summary and one video report. It never registers/deletes sources.
It checks raw RTSP/RTSPS rejection without tools, named-stream rejection with at
most one classification tool, time-window rejection, and a stale-file-context
challenge. That challenge does not mutate a real source registration.

For stronger validation, inspect the sandbox session transcript for the returned
run IDs: stream turns may only call `vss vios list --sensor <name>`, never ingestion,
clip extraction, or inference. UI SSE exposes tool names but not CLI arguments,
so UI assertions alone cannot prove which `vss_cli` subcommand ran. Verify the
uploaded workflows' job IDs using the configured VSS CLI.
