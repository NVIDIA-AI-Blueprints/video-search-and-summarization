## Description: <br>
Use when summarizing a recorded video through HITL-gated LVS, falling back to vss vlm run when LVS is not ready. Not for reports, archive search, or live RTSP captioning. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to summarize recorded video through human-in-the-loop–gated LVS or direct VLM fallback, producing polished narrative summaries with timestamped events from a deployed VSS stack. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Not Specified] <br>
**Credential Type(s):** [None identified] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [cli_usage.md](references/cli_usage.md) <br>
- [deploy-lvs-service.md](references/deploy-lvs-service.md) <br>
- [end-to-end-example.md](references/end-to-end-example.md) <br>
- [hitl-prompts.md](references/hitl-prompts.md) <br>
- [integrate-lvs-service.md](references/integrate-lvs-service.md) <br>
- [video-summarization-api.md](references/video-summarization-api.md) <br>
- [video-summarization-debugging.md](references/video-summarization-debugging.md) <br>
- [video-summarization-deployment.md](references/video-summarization-deployment.md) <br>
- [video-summarization-environment-variables.md](references/video-summarization-environment-variables.md) <br>
- [NVIDIA VSS Blueprint Demo](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Markdown with JSON and inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
3 evaluation tasks (2 positive, 1 negative) per agent, each in an isolated k8s-sandbox pod. Dataset digest: sha256:2d3aa44da1d687045c1aa735919e28dc2ffee16c19aa3676de506299a16876bc. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use, checking for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the final answer is correct against the reference answer. <br>
- Discoverability: Whether the right skill was selected, decoys were avoided, and the workflow executed. <br>
- Effectiveness: Whether the skill helped complete the user's goal (50% goal accuracy + 50% behavior check). <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage (50% tool productivity + 50% token efficiency). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity scored under Efficiency. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 79.8% | 69.1% |
| Security | 100.0% → 100.0% (±0.0 points) | 33.3% → 66.7% (+33.4 points) |
| Correctness | 13.3% → 60.0% (+46.7 points) | 40.0% → 66.7% (+26.7 points) |
| Discoverability | 97.5% | 82.5% |
| Effectiveness | 32.0% → 52.8% (+20.8 points) | 39.4% → 47.0% (+7.6 points) |
| Efficiency | 88.8% | 82.6% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
