# Prompt registry

This registry holds every model prompt the profile sends besides the contract's P1 and RP1 prompts (`../incident_extraction_prompt.txt`, `../report_generation_prompt.txt`):
- Each file is the exact text.
- [`registry.json`](registry.json) pins its SHA-256 and lists who uses it.
- The request goldens in [`../golden/`](../golden/README.md) pin what each prompt renders to.

| Id | Stage | Used by | Placeholders | Purpose |
|---|---|---|---|---|
| [`caption/system`](caption/system.txt) | caption | agent | - | The caption pass's system message (the base profile's video_report_gen system prompt). |
| [`caption/window`](caption/window.txt) | caption | agent | - | The caption request's text (the base profile's video_report_gen.vlm_prompt), before the time range. |
| [`caption/time-range`](caption/time-range.txt) | caption | agent | `end_time`, `start_time` | The time range appended to the caption text (video_report_gen's CHUNK_TIMESTAMP_PROMPT). |
| [`caption/context-preamble`](caption/context-preamble.txt) | p1 | agent | - | Heads the caption context message P1 receives (clips over 30 s). |
| [`p1/reviewer-preamble`](p1/reviewer-preamble.txt) | p1 | agent, console | - | Heads a reviewer's instruction in P1, in both analysis modes (console and agent). |
| [`p1/marks-preamble`](p1/marks-preamble.txt) | p1 | agent | - | Heads the set-of-marks message P1 receives (flag, off by default). |
| [`grounding/best-match`](grounding/best-match.txt) | grounding | agent | `query` | One box for a report item's label (grounding; object tracking's re-anchoring). |
| [`zoom/look-entity-frame`](zoom/look-entity-frame.txt) | zoom | agent | - | Blind look at a person: the frame with the box drawn. |
| [`zoom/look-entity-close-up`](zoom/look-entity-close-up.txt) | zoom | agent | - | Blind look at a person: the frame, then the close-up. |
| [`zoom/look-object-frame`](zoom/look-object-frame.txt) | zoom | agent | - | Blind look at an instrument or asset: the frame with the box drawn. |
| [`zoom/look-object-close-up`](zoom/look-object-close-up.txt) | zoom | agent | - | Blind look at an instrument or asset: the frame, then the close-up. |
| [`zoom/merge-entity`](zoom/merge-entity.txt) | zoom | agent | `description`, `look_description`, `look_name`, `name` | Text-only merge of the agreeing looks with a person's report text. |
| [`zoom/merge-object`](zoom/merge-object.txt) | zoom | agent | `description`, `item_type`, `look_description`, `look_name`, `name` | Text-only merge of the agreeing looks with an instrument's or asset's report text. |
| [`chart/system`](chart/system.txt) | chart | console | - | The dashboard chart interpreter's system prompt, built from the allowlist (source: chart-interpreter.ts). |
| [`chart/allowlist`](chart/allowlist.json) | chart | console | - | The chart allowlist (metrics, dimensions, periods, severities, incident types; source: chart-spec.ts). |
| [`chat/incident-answer`](chat/incident-answer.txt) | chat | agent | - | The grounded chat's system prompt: answer one report version from its JSON, cite item ids, flag insufficient evidence. |
| [`chat/library-plan`](chat/library-plan.txt) | chat | agent | - | The library chat's planner: filters, a breakdown, a count flag and the search phrase the filters leave. |
| [`chat/library-answer`](chat/library-answer.txt) | chat | agent | - | The library chat's answer to a question that is not a count, from the matching reports and the code counts. |

## Rules

- **Placeholders** are `{{name}}`. They are replaced in one pass by the value's text, and a value is never scanned again. A missing or extra field is refused. Every other brace is literal, such as the JSON examples inside the prompts.
- **Agent:** reads its prompts from the vendored copy that `python ../sync.py` writes (`--check` reports drift), through `vss_agents.incident_contract`:
  - `prompt(id)` and `render_prompt(id, **fields)` check the file's hash;
  - `prompt_sha256(id)` is the version a run records.
- **Console:** reads `p1/reviewer-preamble` at runtime through its contract catalog, with the hash checked. The agent uses the same file, so a reviewer instruction reads the same in both analysis modes.
- **Chart files are exports:** `chart/system.txt` and `chart/allowlist.json` are written from the console's TypeScript, which builds the chart prompt from its allowlist. `npm run export:prompts` in `incident-console-v2/` rewrites them, and `tests/prompt-registry.test.mjs` fails until it is re-run.

## Changing a prompt

1. Edit the file. For a chart prompt, edit the TypeScript and run the export.
2. Update the entry's `sha256`, then run `python ../sync.py` for an agent prompt.
3. Update the request goldens it changes (`../golden/`) in the same PR.
4. Say why in [`.docs/decisions.md`](../../.docs/decisions.md). Evaluation records each prompt's sha256, so the old and new versions stay distinguishable.
