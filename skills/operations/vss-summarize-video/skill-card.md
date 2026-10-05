## Description: <br>
Use when summarizing a recorded video through HITL-gated LVS, falling back to vss vlm run when LVS is not ready. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to summarize recorded video through the VSS platform's LVS service, producing polished narrative summaries with timestamped events. <br>

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
- [End-to-End Example](references/end-to-end-example.md) <br>
- [CLI Usage](references/cli_usage.md) <br>
- [Video Summarization API](references/video-summarization-api.md) <br>
- [HITL Prompts](references/hitl-prompts.md) <br>
- [Video Summarization Debugging](references/video-summarization-debugging.md) <br>
- [Video Summarization Deployment](references/video-summarization-deployment.md) <br>
- [Video Summarization Environment Variables](references/video-summarization-environment-variables.md) <br>
- [Deploy LVS Service](references/deploy-lvs-service.md) <br>
- [Integrate LVS Service](references/integrate-lvs-service.md) <br>
- [VSS Blueprint Demo](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [VSS GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Markdown with inline JSON from CLI output] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Renders video_summary and timestamped events verbatim from LVS or VLM response] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
Evaluated against 3 tasks (2 positive, 1 negative) in isolated k8s-sandbox pods with 1 attempt per task. Dataset digest: sha256:2d3aa44da1d687045c1aa735919e28dc2ffee16c19aa3676de506299a16876bc. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether it is safe to use, checking for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the final answer is correct against the reference answer. <br>
- Discoverability: Whether the right skill was loaded when needed, checking skill execution and decoy avoidance. <br>
- Effectiveness: Whether the skill helped complete the user's goal, combining goal accuracy (50%) and expected behavior adherence (50%). <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage, combining tool productivity (50%) and token efficiency (50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `accuracy`: Verifies final-answer correctness against the reference answer. <br>
- `skill_execution`: Verifies whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `goal_accuracy`: Checks whether the user's goal was achieved. <br>
- `behavior_check`: Verifies whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Measures tool-call productivity. <br>
- `token_efficiency`: Measures actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | Not available | 78.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | Not available | 33.3% → 100.0% (+66.7 points) |
| Correctness | Not available | 46.7% → 53.3% (+6.6 points) |
| Discoverability | Not available | 92.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | Not available | 38.6% → 48.6% (+10.0 points) |
| Efficiency | Not available | 96.6% — baseline ran, but no comparable score was available; uplift unavailable |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
