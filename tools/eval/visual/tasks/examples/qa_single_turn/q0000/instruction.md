# Task

Using the `vss` CLI against the configured backend (`vss configure --base-url` is already
set), answer the following question about the archived video:

> **Was a forklift active near the loading dock between 14:00 and 15:00, and what was it
> doing?**

## Output contract

Write your final answer to `/output/answer.json`:

```json
{
  "answer": "<your answer>",
  "evidence": [{"dss_key": "<video key>", "t0": 0.0, "t1": 0.0, "clip": "<optional clip path>"}],
  "confidence": 0.0
}
```

Do not finish until the answer file has content. Use `vss` CLI tools (not raw HTTP) for
all backend interaction.
