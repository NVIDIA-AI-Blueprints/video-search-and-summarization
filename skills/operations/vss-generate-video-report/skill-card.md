## Description: <br>
Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics, Mode C SOP compliance via the SOP tools. Not for standalone video summarization, real-time alerts or ad-hoc Q&A. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators of an NVIDIA Video Search and Summarization (VSS) deployment use this skill to have an agent generate structured markdown reports: per-clip VLM video analysis reports, narrative incident-range reports from video analytics, and SOP compliance reports from the VA-MCP SOP tools. <br>

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
- [Video analysis report (Mode A)](references/report-types/video-analysis.md) <br>
- [Incident range report (Mode B)](references/report-types/incident-range.md) <br>
- [SOP compliance report (Mode C)](references/report-types/sop-compliance.md) <br>
- [Video Analysis Report template](references/report-templates/video-analysis-report.md) <br>
- [Incident Range Report template](references/report-templates/incident-range-report.md) <br>
- [SOP Compliance Report template](references/report-templates/sop-compliance-report.md) <br>
- [Default VLM prompt](references/default-vlm-prompt.md) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [VSS GitHub repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, API Calls, Shell commands] <br>
**Output Format:** [Markdown report (Video Analysis, Incident Range, or SOP Compliance template), produced via inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Reasoning (`<think>`) blocks are stripped from VLM output; browser-facing clip URLs are rewritten to the public VSS origin; empty-range results render as a single plain-text line.] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
14 evaluation tasks (13 positive, 1 negative), one attempt per task, each run in an isolated sandbox pod (`k8s-sandbox`), compared against a no-skill baseline. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? (`security` signal) <br>
- Correctness: Is the answer correct? (`accuracy` signal) <br>
- Discoverability: Was the right skill loaded when needed? (`skill_execution` signal) <br>
- Effectiveness: Did the skill help complete the task? (50% `goal_accuracy` + 50% `behavior_check`) <br>
- Efficiency: Did it avoid wasted tool calls and token usage? (50% `skill_efficiency` + 50% `token_efficiency`) <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 89.7% — baseline ran, but no comparable score was available; uplift unavailable | 89.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 14.3% → 92.9% (+78.6 points) | 52.9% → 90.0% (+37.1 points) |
| Discoverability | 83.1% — baseline ran, but no comparable score was available; uplift unavailable | 81.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 12.3% → 81.0% (+68.7 points) | 23.3% → 78.4% (+55.1 points) |
| Efficiency | 91.6% — baseline ran, but no comparable score was available; uplift unavailable | 97.2% — baseline ran, but no comparable score was available; uplift unavailable |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
