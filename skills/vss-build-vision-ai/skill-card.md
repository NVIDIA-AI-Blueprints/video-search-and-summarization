## Description: <br>
Add agent-ready vision capabilities — dense captioning, detection, search, alerting, summarization — to an agent or application through a customizable, self-contained vision stack built on the NVIDIA VSS Blueprint. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to compose, configure, and deploy a self-contained vision application stack — a stock developer profile (base, alerts, lvs, search), the warehouse industry profile, or a lean custom delta combination — on top of the NVIDIA VSS Blueprint via Docker Compose. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Yes] <br>
**Credential Type(s):** [API key] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [composition.md — delta-profile rules, Foundation selection, resolution, validation](references/composition.md) <br>
- [deployment.md — resolved Compose deployment lifecycle](references/deployment.md) <br>
- [agent-harness.md — in-stack agent and NemoClaw/OpenClaw/Hermes harnesses](references/agent-harness.md) <br>
- [prerequisites.md — host, GPU runtime, firewall, credential, and NGC checks](references/prerequisites.md) <br>
- [sizing.md — developer-profile sizing, model placement, stream capacity](references/sizing.md) <br>
- [profiles/ — current developer profile capabilities and service sets](references/profiles/) <br>
- [services/ — capability-owner contracts for service keys and knobs](references/services/) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [Video Search and Summarization Blueprint](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [video-search-and-summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Configuration instructions, Shell commands, Files] <br>
**Output Format:** [Markdown with inline bash code blocks plus generated Compose and env build artifacts] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Writes build artifacts under _builds/<name>/ (override.env, compose.yml, resolved.yml) and presents an architecture diagram before deployment.] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative), each attempt run in its own isolated sandbox pod in a k8s-sandbox environment. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use, checking for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the final answer is correct against the reference answer. <br>
- Discoverability: Whether the right skill was loaded and activated when needed, avoiding decoys. <br>
- Effectiveness: Whether the skill helped complete the user's goal and the expected workflow was followed. <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `token_efficiency`: Actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 65.2% (uplift unavailable) | 67.2% (uplift unavailable) |
| Security | 77.3% → 54.6% (-22.7 points) | 40.9% → 68.2% (+27.3 points) |
| Correctness | 27.3% → 67.3% (+40.0 points) | 52.7% → 78.2% (+25.5 points) |
| Discoverability | 98.0% (uplift unavailable) | 76.5% (uplift unavailable) |
| Effectiveness | 10.8% → 23.7% (+12.9 points) | 17.6% → 33.3% (+15.7 points) |
| Efficiency | 82.5% (uplift unavailable) | 79.7% (uplift unavailable) |

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
