## Description: <br>
Answers questions about previously analyzed or freshly scoped VSS video by routing through hot conversation context, agent Markdown notes, stored VSS memory (`vss memory get` / `vss memory query`), `vss memory introspect`, or an exact-window `vss vlm run`, using the `vss` CLI against a configured VSS deployment. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers, operators, and agent users of an NVIDIA Video Search and Summarization (VSS) deployment use this skill to answer ad-hoc questions about recorded or analyzed video, read stored VSS memory jobs and records by id, run memory introspection, or perform a bounded visual inspection of a sensor window, URL, or local file. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Optional] <br>
**Credential Type(s):** [API key] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [VSS CLI bootstrap and rules (AGENTS.md)](../../../AGENTS.md) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [NVIDIA AI Blueprint: Video Search and Summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Natural-language answer in Markdown, grounded in JSON results from `vss` CLI commands, with cited job/record handles and exit codes] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [One `vss vlm run` per grounded scope; VLM sampling capped at 60 frames per window; failures reported with the CLI exit code rather than retried] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
17 evaluation tasks (16 positive, 1 negative), one attempt per task, each in an isolated k8s sandbox pod, compared against a no-skill baseline (evaluator version 1.5.6, evaluated 2026-10-09). <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Is the answer correct? Final-answer correctness against the reference answer. <br>
- Discoverability: Was the right skill loaded when needed? <br>
- Effectiveness: Did the skill help complete the task? Equal-weight mean of goal completion and expected workflow adherence. <br>
- Efficiency: Did it avoid wasted tool calls and token usage? Equal-weight mean of tool-call productivity and token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 85.6% — uplift unavailable | 87.2% — uplift unavailable |
| Security | 100.0% → 94.1% (-5.9 points) | 52.9% → 100.0% (+47.1 points) |
| Correctness | 21.2% → 92.9% (+71.7 points) | 43.5% → 81.2% (+37.7 points) |
| Discoverability | 79.7% — uplift unavailable | 94.4% — uplift unavailable |
| Effectiveness | 36.3% → 71.8% (+35.5 points) | 46.0% → 63.7% (+17.7 points) |
| Efficiency | 89.5% — uplift unavailable | 96.9% — uplift unavailable |

Overall verdict: PASS — Recommended for publication.

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
