# Troubleshooting

| Symptom | Action |
|---|---|
| CLI missing or wrong installation | Install the checkout's `libs/vss/cli` per AGENTS.md; select `cli.executable` if needed. There is no automatic project-environment fallback. |
| Version API 404/unreachable | Check the configured public origin or `compatibility.version_url`; stop and ask the operator to expose the deployed version route. |
| Incompatible or invalid deployed version | Stop before upload; use a supported deployment/skill and review `SKILL.md` compatibility metadata. Do not bypass the gate or use local `vss --version`. |
| configure show fails | Configure the intended public origin in the selected CLI config home |
| Agent ingestion/LVS routes absent | Not upload dependencies; public VIOS, ES and the deployed version API are required here |
| ES is discovered, but `/_cluster/health` returns 403 | Discovery does not prove the ingress permits health checks. Use an operator-provided public ES route to the same cluster that allows health and search reads, or ask the operator for one. Do not disable the ingress guard or skip readiness. |
| Legacy `--set` rejected | Use the dedicated flag or YAML key, such as `--concurrency 5` or `sweep.concurrencies: [5]`. The old generic option did not apply its values. |
| CLI nonzero | Keep the actual exit and diagnostic; no HTTP-status inference or automatic retry |
| CLI exit 7 | CLI bounded wait expired; cleanup resolves a missing ID from the persisted UUID name and public listing; use ledger recovery if still unresolved |
| CLI exit 0, ES unconfirmed | Check raw/embed identities and counts, configured indices/chunk duration, then deployed webhooks |
| Warmup stops before the measured sweep (exit 1) | Console errors distinguish ingestion outcomes, cleanup failures and unresolved cleanup identities, with per-upload diagnostics. Review `raw/warmup_details.jsonl`; for incomplete cleanup, retain the displayed `raw/upload_ledger.jsonl` for recovery. |
| No sensor_id | Cannot correlate Embed; report unconfirmed rather than guessing an ID |
| Raw coverage stalls below the expected count | Remains unconfirmed, including a 95% plateau. Could be delayed indexing, frame drops or sparse detections; the stall guard is not proof of any one cause. |
| Legacy raw completion ratio rejected | Remove `es_readiness.raw_completion_ratio` / `--raw-completion-ratio`; both expected counts are now mandatory. |
| CLI delete exits 0 without stored-recording confirmation | Treat deletion as incomplete; preserve diagnostics and stop before the next point. |
| CLI deletion confirmed, ES remains or reappears | Runner waits for continuous absence for `cleanup.settle_sec`, within `cleanup.timeout_sec`. If it cannot settle, stop and ask the operator to inspect camera_remove processing. No direct ES deletion. |
| Cleanup reports missing indices or no shards | Verify both configured Raw and Embed index targets on the selected ES endpoint. Cleanup is unverified and later sweep points stop; do not treat missing indices as zero documents. |
| Missing/invalid artifacts | Run `scripts/validate_artifacts.py --results-dir DIR`; regenerate summaries/charts from the completed CSVs before reporting. |
| Throughput plateaus | Compare latency, failures, payload rate, and client resources; no internal attribution |

The runner never uploads through the Agent API, never wraps the CLI in a retry or
extra timeout loop, and never deletes ES documents directly. ES timeouts are separate
from the CLI's own upload/timeline budgets.
