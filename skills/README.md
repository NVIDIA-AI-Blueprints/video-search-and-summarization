# VSS Skills

Skills for working with NVIDIA Video Search & Summarization (VSS). Each subdirectory under `skills/` is a self-contained skill following the [agentskills.io](https://agentskills.io/specification) specification, with `name`, `description`, `version`, and `license` declared in its `SKILL.md` frontmatter.

## Catalog

| Skill | Description |
|---|---|
| [alerts](alerts/SKILL.md) | Manage and monitor VSS alerts after the alerts profile is deployed. |
| [deploy](deploy/SKILL.md) | Deploy, debug, or tear down any VSS profile using a compose-centric workflow. |
| [report](report/SKILL.md) | Produce video analysis reports by querying the VSS agent's `/generate` endpoint. |
| [rt-vlm](rt-vlm/SKILL.md) | Work with the RTVI VLM microservice — captions, alerts, streams, OpenAI-compatible completions. |
| [video-analytics](video-analytics/SKILL.md) | Query video analytics data and metrics from Elasticsearch via the VA-MCP server. |
| [video-search](video-search/SKILL.md) | Search video archives using natural language across recorded video. |
| [video-summarization](video-summarization/SKILL.md) | Summarize a video by calling the VLM NIM directly or the Long Video Summarization (LVS) service. |
| [video-understanding](video-understanding/SKILL.md) | Run video understanding to answer text questions about video content. |
| [vios](vios/SKILL.md) | Query VIOS REST APIs — sensor list, recording timelines, clip extraction, snapshots. |

Skills with `eval/*.json` specs are exercised automatically by the Skills Eval CI workflow on every PR that touches `skills/**` — see [`.github/skill-eval/AGENTS.md`](../.github/skill-eval/AGENTS.md) for harness behavior.

## Install (recommended: ask your coding agent)

Open this repository in your coding agent (Claude Code, Codex, Cursor, or any other agentskills.io-compatible host) and paste the following prompt:

> Read `skills/README.md` and every `SKILL.md` file under `skills/`. For each skill in the catalog, install it for this host so I can invoke it from a shell or chat session. Use the host's standard skills directory:
>
> - Claude Code: `~/.claude/skills/<name>/`
> - Codex: `~/.codex/skills/<name>/`
> - Hosts that follow the agentskills.io universal path: `~/.agents/skills/<name>/`
>
> Symlink each skill folder rather than copying it so a `git pull` here keeps every install up to date. Skip skills that are already installed and pointing at this checkout. When you're done, list the skills you registered and which directory you used.

The agent will read the frontmatter of each `SKILL.md`, create the symlinks, and confirm what's installed. The skills become invokable in the next agent session.

### Single-skill install

> Install only `skills/<name>/` for this host the same way.

### Update

After `git pull`, the symlinks already point at the updated content — nothing to do unless skills were added or renamed. To pick up new skills:

> Re-read `skills/README.md` and add any new skills missing from this host's skills directory.

### Uninstall

> Remove every VSS skill symlink you previously created under this host's skills directory.

## Source of truth

This `skills/` directory is the canonical source. Skills published to the public catalog at `github.com/nvidia/skills` are mirrored from here at sync time per [`components.yml`](https://github.com/NVIDIA/skills/blob/main/components.yml).
