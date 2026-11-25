# AGENTS.md - Your Workspace

This folder is home. Treat it that way.

## VSS Base prompt routing

For every named-video report, first resolve the exact timeline with the
standalone `vss` CLI. If it is 120 seconds or longer, stop before any VLM call
and report that LVS is required. Never bypass this gate through raw HTTP or
another tool.

For these UI requests, select and follow exactly one active VSS skill:

- List sensors, take a snapshot, or inspect a timeline: `vss-manage-video-io-storage`
- Ask what is visually present in a named video, including whether a worker is wearing PPE: `vss-ask-video`
- Generate a report for a named video: `vss-generate-video-report`

Read the selected skill from its exact location in the active Hermes skills.
Run the skill's command through the standalone `vss` executable on `PATH`.
Use the terminal only for the required `vss` commands; do not look for a
repository checkout or replace the CLI with raw HTTP.

Never route a named-video PPE question to analytics or VA-MCP. Resolve names
from the sensor listing; if one unambiguous result corrects a typo, state the
correction and use the listed identifier. Obtain the sensor's exact recorded
timeline before time-based requests; never substitute the current date.
A report for a named video shorter than 120 seconds uses
`vss-generate-video-report` Mode A, never `vss summarize`: resolve the sensor's
full recorded timeline and complete the skill's default/HITL prompt-selection
step. Use the HITL-selected prompt when one exists. Otherwise, use this exact
default prompt as one argument, preserving its line breaks:

```text
Describe in detail what happens in the video, with timestamps (start-end in seconds from clip start) for each segment or event.

Cover scenes, objects, people, vehicles, and notable actions.

Output requirements:
- Keep events in chronological order.
- Use concrete descriptions rather than generic placeholders.
- Include timestamps in each event line.
```

After selecting the prompt, the only VLM call in the report turn must have
this standalone CLI shape:

```bash
vss vlm run \
  --prompt "<selected-prompt>" \
  --sensor "<listed-name>" \
  --start-time "<timeline-start>" \
  --end-time "<timeline-end>" \
  --fps 2
```

Use the exact recorded ISO-8601 timeline values; do not omit them or replace
them with offsets. Then render the structured report. Never call
`vss summarize`, use raw HTTP, or reuse an earlier answer or snapshot.
