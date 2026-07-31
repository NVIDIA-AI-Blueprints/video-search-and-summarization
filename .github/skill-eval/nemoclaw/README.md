# NemoClaw Skill Eval Runner

This directory contains the GitHub CI integration for opt-in NemoClaw/OpenClaw skill evaluation.

- `notebook_cells.json` selects setup-only cells from `deploy/docker/scripts/deploy_nemoclaw.ipynb` and `deploy/docker/scripts/deploy_vss_orchestrator.ipynb` by stable cell id.
- `notebook_setup_adapter.py` combines and executes those cells as a temporary setup notebook, then writes `/tmp/skill-eval/nemoclaw/nemoclaw.env` on the Brev worker.
- `readiness.py` checks host tools, in-sandbox OpenClaw gateway health, and VSS Orchestrator MCP health before Harbor runs the scenario.
- `headless_runner.py` is called from the Harbor trial. It posts the real task prompt to the OpenClaw hooks endpoint so the scenario runs through NemoClaw/OpenClaw.

Harbor remains the CI entrypoint and result owner. The default runner is unchanged unless a spec declares `runner: "nemoclaw"` / `requires_nemoclaw: true`, or a manual workflow dispatch selects `runner=nemoclaw`.

When manual dispatch uses `runner=nemoclaw`, `skills=vss-deploy-profile` keeps the lightweight base-profile smoke behavior. `skills=*` discovers adapter-backed `skills/*/evals/*.json` specs, wraps their generated Harbor tasks as NemoClaw/OpenClaw launcher tasks, and reports unsupported eval specs as blocked coverage gaps.

Dense-captioning scenarios additionally require
`vss_orchestrator__rtsp_sample_probe`. The orchestrator notebook installs host
`ffmpeg`, and the no-argument tool probes only the orchestrator server's
configured runtime sample. The exact `RTSP_SAMPLE_URL` is never passed through
the model or MCP arguments and is not persisted in the composed notebook
environment.
