# CV Verifier Prompts & Verdicts (CV mode only)

Operational reference for Workflow A / Workflow C on a **CV (verification)** deployment: how to read VLM verification verdicts on alerts and how to customize the VLM-verifier prompts. Not applicable to VLM real-time mode (there is no separate verdict field — see below).

## Verdict interpretation

Verified CV alerts carry an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `not-confirmed` | VLM response could not be parsed into a confirmed/rejected verdict (parse failure) |
| `verification-failed` | Verification could not complete — API/VLM error |
| `""` (empty) | A pluggable response parser was used instead of the verdict path |

- Check `verificationResponseCode` (`200` = success) and `reasoning` for the VLM's explanation. These live in the incident `info` block and appear camelCase (`verificationResponseCode`, `verificationResponseStatus`).
- VLM real-time mode incidents are always "confirmed" at source (the trigger itself is a Yes/No VLM answer), so there is **no** separate verdict field in VLM mode.

## Customize CV verifier prompts

CV-path verifier prompts live in:

```
deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json
```

Each entry maps a CV `alert_type` (the `category` field emitted by Behavior Analytics) to the VLM `system` / `user` / optional `enrichment` prompts.

Key rules:

- `alert_type` must match the `category` emitted by Behavior Analytics.
- `output_category` is the display name in Elasticsearch / UI.
- `enrichment` triggers a second VLM call for a richer description; requires `alert_agent.enrichment.enabled: true`.
- Edits require an `alert-bridge` container restart to take effect.

VLM real-time prompts are **not** configured in a file — they are per-request, shaped by `rtvi_prompt_gen` from the user's natural-language detection description.
