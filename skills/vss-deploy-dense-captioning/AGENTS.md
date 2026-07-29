# AGENTS.md

## Scope

Applies to `vss-deploy-dense-captioning`, the RT-VLM dense captioning and
stream caption service skill.

## Rules

- Use this skill for standalone RT-VLM dense captions, anomaly descriptions,
  and caption API calls. Use `vss-manage-alerts` for Alert Bridge operations.
- Verify whether the request is standalone service bring-up or profile-bound
  API use before deploying.
- Preserve user-provided stream/video source and model endpoint choices.
- Do not route one-off VSS Agent Q&A through this skill unless the task
  explicitly requires RT-VLM captions.

## Eval Behavior

- Deploy prerequisites only when the prompt pre-authorizes setup.
- Report concrete caption/API evidence and the endpoint used.
