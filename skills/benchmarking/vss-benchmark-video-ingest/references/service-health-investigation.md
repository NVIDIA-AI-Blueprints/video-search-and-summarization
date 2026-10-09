# Health investigation

Before attributing an all-zero run to capacity, check the configured deployment:

1. `vss configure show`: correct public origin and VIOS/ES routes?
2. `vss vios list --type video`: exit 0 and valid JSON? An empty list is healthy.
3. `python scripts/check_compatibility.py`: deployed version API reachable and compatible?
   A failed lookup stops the run; a local CLI version cannot replace this evidence.
4. Runner ES preflight: reachable non-red cluster? Missing indices on a new cluster
   need ingestion; they do not by themselves mean the route is missing.
5. Inspect one run-owned upload with `vss vios list --type video --sensor NAME`
   and `vss vios timeline --sensor NAME`.
6. Inspect upload_details.jsonl counts: raw only, embed only, neither, or partial?

CLI exit 0 with missing ES work can mean absent/rejected webhooks, inference failure,
indexing failure, an incorrect identity/index/chunk-duration setting, or sparse raw
output. These observations do not identify an internal root cause. Report evidence
and ask the operator to inspect the webhook/consumer deployment. Do not modify the
cluster, retry uploads blindly, or fall back to Agent REST.
