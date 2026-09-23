# BYOM Quality Gates

## Required Smoke Coverage

Test each modality the model claims to support:

- a text-only sanity prompt;
- an image prompt with explicit image routing;
- a video prompt with declared sampling parameters;
- a negative/control prompt for grounding.

Fail the port when output is empty, truncated, unrelated, repetitive, invalid
Unicode, token soup, or transport framing rather than model content.

## Grounding

Compare media responses with visible evidence. The answer should name present
objects and actions, respect timing and sampling assumptions, and avoid confident
claims about unseen details or events.

## Evidence

Capture:

- redacted request payload or command;
- model source, immutable revision and backend;
- container tag and digest when available;
- eager-mode state and reason;
- relevant context, tensor-parallel, quantization, memory and media settings;
- raw response samples for every tested modality;
- latency, throughput, GPU utilization, memory and error counts.
