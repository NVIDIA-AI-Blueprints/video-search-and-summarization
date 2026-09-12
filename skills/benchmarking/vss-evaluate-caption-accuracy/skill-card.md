## Description: <br>
Measure whether an RT-VLM configuration change altered caption quality — capture paired baseline and candidate captions over the same videos, score both against a ground truth with an LLM judge, and report the accuracy delta alongside processing time saved. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Developers changing RT-VLM frame selection, decode, or model settings who need evidence that caption quality did not regress before shipping the change. Produces a per-scene accuracy and time-saved table plus per-chunk judge scores. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Caption scores are nondeterministic run to run — repeat runs of an identical configuration have varied by ~0.01 on a single scene, and the baseline/candidate delta has changed sign between them. A small delta read from one run can be mistaken for a real effect. <br>
Mitigation: The skill captures baseline and candidate paired in one session so both see the same conditions, reports chunk-weighted totals, and documents the observed noise floor. Repeat any result that matters before acting on it. <br>

Risk: The combined score averages several F1 axes, one of which (interaction F1) is often computed from very few samples. A flat combined score can therefore mask a real move in entity recall. <br>
Mitigation: The report emits the per-axis F1 detail alongside the combined column, and the skill documentation directs readers to it. <br>

Risk: Ground-truth generation sends video frames to a third-party model endpoint (gpt-4.1 via an OpenAI-compatible API), and the judge stage sends caption text to Anthropic through the local `claude` CLI. <br>
Mitigation: The ground-truth stage is optional and separate — an existing ground truth can be reused via `GT_SRC`, or supplied from any source. Review your data-handling policy before running either stage on restricted footage. <br>

Risk: Capture runs sustained GPU inference over full videos and can occupy the GPU for the duration of the run. <br>
Mitigation: Run on a GPU you own for the session; ensure adequate cooling before long multi-scene runs. <br>
