## Description: <br>
Score a running NVIDIA VSS deployment with NeMo Gym, producing a scalar reward per task in the contract a training loop consumes, and support a side-by-side comparison against VSS's own eval harness on one identical stack. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Developers and engineers evaluating NVIDIA Video Search and Summarization (VSS) deployments on GPU-equipped hosts, and comparing VSS's bespoke evaluation harness against NeMo Gym on the same deployment. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Yes] <br>
**Credential Type(s):** [API key] <br>

NGC credentials are required to pull the NeMo Gym evaluation image. Judge-model access may additionally require an LLM endpoint credential. Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

Risk: The published `nemo-gym` image built before NVIDIA-NeMo/Gym#2376 bundles royalty-bearing codec libraries that VSS containers must not carry. <br>
Mitigation: The skill gates on image provenance and refuses to pull a tag that predates that fix. The gate reads the manifest list, the platform manifest, and the image config blob — approximately 20 KB — and accepts a tag only when the recorded build date postdates the upstream fix and no layer in the recorded history installs a codec package. No image layer is pulled. <br>

Risk: A deployment evaluated by a stack that has drifted from its Foundation produces scores that are not comparable, and the drift is silent. <br>
Mitigation: The evaluation runner is composed as a delta adding exactly one service key to a Foundation profile, so by construction every Foundation service is preserved and the two stacks differ by exactly that one runner. Resolved values are not identical by construction — delta resolution does not read the Foundation's generated.env — so the skill states that limit and provides verification diffs for both the service list and the resolved environment, to be run before any comparison is trusted. <br>

Risk: Comparison results can be lost because developer profiles share a Compose project name and are torn down before redeployment. <br>
Mitigation: The comparison protocol requires persisting each harness's results before switching stacks. <br>
