## Description: <br>
Use this skill when answering a question about previously analyzed or freshly scoped VSS video, or when reading a stored VSS memory job or record by id, or whenever a question should be answered by running the `vss memory introspect` command; it routes through hot context, agent Markdown notes, `vss memory get` or `vss memory query`, `vss memory introspect`, or an exact-window `vss vlm run`, and is not for video retrieval or metadata-answerable questions. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators of a deployed NVIDIA Video Search and Summarization (VSS) blueprint use this skill to have an agent answer questions about analyzed or explicitly scoped video by routing through conversation context, agent Markdown memory, structured VSS memory, deployment-side introspection, or a single bounded VLM inspection via the `vss` CLI. <br>

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
- [AGENTS.md (vss CLI bootstrap, exit codes, and rules)](../../../AGENTS.md) <br>
- [BENCHMARK.md](BENCHMARK.md) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [NVIDIA AI Blueprint: Video Search and Summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Natural-language answer grounded in `vss` CLI JSON results, with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Reports the `vss` CLI exit code alongside results and cites returned job/record handles; at most one VLM inspection per grounded scope.] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
17 evaluation tasks (16 positive, 1 negative), one attempt per task, each run in an isolated sandbox pod in a `k8s-sandbox` environment (evaluator version `1.5.6`, evaluated 2026-10-15); baseline is the same task attempted without the skill. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Is the answer correct? Final-answer correctness against the reference answer. <br>
- Discoverability: Was the right skill loaded when needed? Expected-skill selection, decoy avoidance, and workflow execution. <br>
- Effectiveness: Did the skill help complete the task? Equal-weight mean of goal completion and expected workflow adherence. <br>
- Efficiency: Did it avoid wasted tool calls and token usage? 50% tool-call productivity and 50% token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 90.1% (uplift unavailable) | 84.5% (uplift unavailable) |
| Security | 100.0% → 100.0% (±0.0 points) | 67.7% → 100.0% (+32.3 points) |
| Correctness | 20.0% → 95.3% (+75.3 points) | 38.8% → 83.5% (+44.7 points) |
| Discoverability | 84.7% (uplift unavailable) | 88.1% (uplift unavailable) |
| Effectiveness | 36.9% → 77.4% (+40.5 points) | 49.0% → 56.6% (+7.6 points) |
| Efficiency | 93.3% (uplift unavailable) | 94.4% (uplift unavailable) |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
