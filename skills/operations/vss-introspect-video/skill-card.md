## Description: <br>
Runs an agent-owned, auditable evidence loop for direct video questions. It creates an option-blind evidence plan before retrieval, binds ordinary VSS memory, dispatches bounded one-claim visual inspections, merges results deterministically, and returns a grounded answer or explicit unresolved gaps. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Developers and operators answering evidence-intensive questions about VSS video while preserving claim-level provenance, bounded tool use, contradictory evidence, and reproducible run artifacts. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Parallel visual inspection can produce unsupported conclusions if agents share mutable state, inspect unbounded media, or silently retry failures. <br>
Mitigation: The orchestrator freezes one ledger revision per batch, assigns one claim and bounded windows per subagent, validates all limits from the canonical configuration, batch-merges deterministically, and reports unresolved gaps instead of guessing. <br>

## Reference(s): <br>
- [NVIDIA AI Blueprint: Video Search and Summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>

## Skill Output: <br>
**Output Type(s):** [Analysis, CLI Calls, JSON Artifacts] <br>
**Output Format:** [Grounded Markdown answer or unresolved result plus strict JSON run artifacts] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Includes observation IDs, VSS job/record/sensor/window provenance, contradictory evidence, unresolved gaps, final ledger revision, and artifact directory.] <br>

## Evaluation Agents Used: <br>
- Claude Code (`claude-code`) <br>
- Codex (`codex`) <br>

## Evaluation Tasks: <br>
Lightweight fixtures cover attribute, count, order, identity, whole-video, incomplete-evidence, partial-parallel-failure, memory-only, and ask-video delegation scenarios. A dispatchable mocked-loop task verifies deterministic adapter generation and the skill-owned orchestration contract without requiring a live deployment. <br>

## Evaluation Metrics Used: <br>
- Correct option-blind planning order and routing. <br>
- Use of ordinary VSS memory, VIOS, and VLM commands only. <br>
- Frozen-revision subagent dispatch and deterministic batch merge. <br>
- Provenance, unresolved-gap, artifact-path, and stop-condition reporting. <br>
- Absence of `vss memory introspect`, raw backend HTTP, and planner-authored answers. <br>

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
