## Description: <br>
Use this skill when adding, wiring, or validating a bring-your-own-model implementation for the VSS RT-Embed video embedding microservice, especially VideoPrism on the 3.3.0 / 26.07.3 code line. <br>

This skill is ready for review before commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers integrating a custom VideoPrism embedding backend with RT-Embed through the existing custom model loader, Docker Compose overrides, Helm values, and `/v1` embedding APIs. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Yes] <br>
**Credential Type(s):** [API key, model repository credential] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: BYOM wrappers can fail at startup if required `BaseVlmModel` abstract methods are missing or if model paths point to the wrong implementation. <br>
Mitigation: Follow the VideoPrism BYOM checklist, validate `/v1/models`, and run focused unit/API checks before deployment. <br>

Risk: Video-only embedding models may not support text-to-video search semantics. <br>
Mitigation: Require a compatible text encoder in the same embedding space or return a clear 4xx text-endpoint error. <br>

## Reference(s): <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VideoPrism BYOM Reference](references/videoprism-byom.md) <br>
- [RT-Embed Deployment Skill](../vss-deploy-video-embedding/SKILL.md) <br>


## Skill Output: <br>
**Output Type(s):** [Implementation instructions, Shell commands, Configuration instructions, API Calls] <br>
**Output Format:** [Markdown with inline bash, YAML, and Python code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- claude-code <br>
- codex <br>



## Evaluation Tasks: <br>
Includes a CPU-only routing evaluation for positive VideoPrism BYOM routing and negative default RT-Embed deployment routing. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access. <br>
- Correctness: Checks whether the agent follows the expected workflow and produces the correct final output. <br>
- Discoverability: Checks whether the agent loads the skill when relevant and avoids using it when irrelevant. <br>
- Effectiveness: Checks whether the agent performs measurably better with the skill than without it. <br>
- Efficiency: Checks whether the agent uses fewer tokens and avoids redundant work. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Verifies that the agent loaded the expected skill and workflow. <br>
- `skill_efficiency`: Checks routing quality, decoy avoidance, and redundant tool usage. <br>
- `accuracy`: Grades final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Checks whether the overall user task completed successfully. <br>
- `behavior_check`: Verifies expected behavior steps, including safety expectations. <br>
- `token_efficiency`: Compares token usage with and without the skill. <br>



## Evaluation Results: <br>
Pending NVSkills-Eval execution for the new routing evaluation. <br>

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
