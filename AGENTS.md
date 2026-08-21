# AGENTS.md

Index of the agent-facing guides in this repository. Each area keeps its own,
next to the code it describes; this file exists so they can be found from the
root, which is where an agent harness starts.

Human contributor guidance — licensing, DCO sign-off, file headers, the code
contribution process — is in [CONTRIBUTING.md](CONTRIBUTING.md). What the
blueprint *is* is in [README.md](README.md). Neither is duplicated here.

## Guides

| Area | Read when you are… | Guide |
|------|--------------------|-------|
| **`vss` CLI** | driving a deployed VSS stack — search, summarize, sensors, clips | [`services/agent/packages/vss_cli/AGENTS.md`](services/agent/packages/vss_cli/AGENTS.md) |
| VSS Agent service | working on the agent itself: tools, workflows, the NAT stack | [`services/agent/AGENTS.md`](services/agent/AGENTS.md) |
| Video Analytics API | working on the analytics service | [`services/analytics/video-analytics-api/AGENTS.md`](services/analytics/video-analytics-api/AGENTS.md) |
| Skill evaluation | writing or debugging a skill eval | [`.github/skill-eval/AGENTS.md`](.github/skill-eval/AGENTS.md) |
| Helm sync | changing the Helm chart mirror | [`.github/helm-sync/AGENTS.md`](.github/helm-sync/AGENTS.md) |

## Skills

`skills/` holds the operational skills — deploy a profile, search the archive,
ask a question about a video, manage alerts. Each carries its own `SKILL.md`
stating when to use it and when not to; `skills/README.md` lists them.

A skill that needs to talk to a running deployment should drive the `vss` CLI
and link to its guide above, rather than restating the bootstrap. Instructions
that live in one skill are invisible to the next and drift the moment the CLI
moves.

## Two things that apply everywhere

- **Sign your commits.** `git commit -s`; DCO is enforced and unsigned commits
  are rejected. See [CONTRIBUTING.md](CONTRIBUTING.md).
- **Branch as `<type>/<name>`** matching your commit's conventional-commit type
  (`feat/`, `fix/`, `docs/`, `refactor/`, `test/`).
