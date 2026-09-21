## Description: <br>
Generates a minimal, option-blind evidence plan for a video question before memory binding or visual inspection. It begins with one claim by default, permits no more than two initial claims, and adds one claim only when a later sufficiency check proves an independently assessable requirement is missing. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Developers and operators who need a small, auditable set of visible claims and evidence requirements before a VSS agent retrieves memory or inspects video. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Over-decomposition can increase latency and visual inspection cost, while answer-choice leakage can bias the plan. <br>
Mitigation: The skill is option-blind, defaults to one claim, caps the initial plan at two claims, and treats missing evidence as a gathering gap rather than automatically creating another claim. <br>

## Reference(s): <br>
- [NVIDIA AI Blueprint: Video Search and Summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>

## Skill Output: <br>
**Output Type(s):** [Evidence Plan] <br>
**Output Format:** [JSON conforming to references/evidence-plan.schema.json] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Contains observable claims, evidence types, coverage requirements, and support/falsification tests; contains no answer choices, evidence records, or conclusions] <br>

## Evaluation Agents Used: <br>
- Codex (`codex`) <br>

## Evaluation Tasks: <br>
Behavior fixtures cover minimal initial decomposition, compound questions, loop expansion, gathering gaps, and non-activation for direct answer requests. <br>

## Evaluation Metrics Used: <br>
- Schema validity <br>
- Claim-count compliance <br>
- Option blindness <br>
- Correct distinction between plan expansion and evidence gathering <br>
- Skill discoverability and routing <br>

## Evaluation Results: <br>
Repository behavior fixtures are included; no release benchmark has been recorded yet. <br>

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
