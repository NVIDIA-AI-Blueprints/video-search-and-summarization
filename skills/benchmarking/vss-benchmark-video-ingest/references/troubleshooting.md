# Troubleshooting

| Symptom | Action |
|---|---|
| CLI missing or wrong installation | Install the checkout's `libs/vss/cli` per AGENTS.md; select `cli.executable` if needed. A project fallback must already be prepared. |
| Version API 404/unreachable | Check the configured public origin or `compatibility.version_url`; stop and ask the operator to expose the deployed version route. |
| Incompatible or invalid deployed version | Stop before upload; use a supported deployment/skill and review `metadata.yml`. Do not bypass the gate or use local `vss --version`. |
| configure show fails | Configure the intended public origin in the selected CLI config home |
| Agent ingestion/LVS routes absent | Not upload dependencies; public VIOS, ES and the deployed version API are required here |
| ES is discovered, but `/_cluster/health` returns 403 | Discovery does not prove the ingress permits health checks. Use an operator-provided public ES route to the same cluster that allows health and search reads, or ask the operator for one. Do not disable the ingress guard or skip readiness. |
| Legacy `--set` rejected | Use the dedicated flag or YAML key, such as `--concurrency 5` or `sweep.concurrencies: [5]`. The old generic option did not apply its values. |
| CLI nonzero | Keep the actual exit and diagnostic; no HTTP-status inference or automatic retry |
| CLI exit 7 | CLI bounded wait expired; cleanup resolves a missing ID from the persisted UUID name and public listing; use ledger recovery if still unresolved |
| CLI exit 0, ES unconfirmed | Check raw/embed identities and counts, configured indices/chunk duration, then deployed webhooks |
| No sensor_id | Cannot correlate Embed; report unconfirmed rather than guessing an ID |
| Raw coverage low | Could be frame drops or sparse detections; not proof of any one bottleneck |
| CLI delete succeeds, ES remains | VIOS removal is confirmed, asynchronous cleanup is not; operator checks camera_remove webhooks |
| Missing/invalid artifacts | Run `scripts/validate_artifacts.py --results-dir DIR`; regenerate summaries/charts from the completed CSVs before reporting. |
| Throughput plateaus | Compare latency, failures, payload rate, and client resources; no internal attribution |

The runner never uploads through the Agent API, never wraps the CLI in a retry or
extra timeout loop, and never deletes ES documents directly. ES timeouts are separate
from the CLI's own upload/timeline budgets.
