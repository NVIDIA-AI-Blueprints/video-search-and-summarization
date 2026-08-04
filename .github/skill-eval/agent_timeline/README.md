# Agent Hardware Timeline

This directory will contain the stacked prototype that correlates skill-eval
agent and tool activity with the GPU telemetry introduced by PR #1516.

The draft compares three representations generated from one canonical,
ephemeral correlation model:

- a Perfetto-compatible trace;
- a self-contained HTML timeline;
- OpenTelemetry trace and metric records.

Raw agent trajectories remain ephemeral. Only allowlisted, sanitized timeline
fields are eligible for workflow artifacts.
