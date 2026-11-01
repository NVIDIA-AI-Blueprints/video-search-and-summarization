# Request goldens

The exact request bodies this profile sends to models, one JSON file per request kind with named cases. Each producer of a case (the console, eval or the agent) has a test that builds the request with its own code and compares it with the case. Two producers of one case therefore send the same request, and a change to any prompt or setting fails a test until its golden changes in the same PR. `vlm-gateway` forwards the console's body unchanged, so it has no golden of its own.

| File | Cases | Producers |
|---|---|---|
| [`p1.json`](p1.json) | `base` | agent, console, eval |
| | `reviewer` (a reviewer instruction) | agent, console |
| | `captions` (the caption pass's context), `marks` (set-of-marks), `captions-and-marks` | agent |
| [`rp1.json`](rp1.json) | `report` | agent, eval |
| [`caption.json`](caption.json) | `window` (default 20 s, 32 frames), `remainder` (a shorter last window) | agent |
| [`grounding.json`](grounding.json) | `best-match` (grounding, object tracking), `every-match` (the privacy blur's head search) | agent |
| [`zoom.json`](zoom.json) | `look-<type>-frame`, `look-<type>-close-up`, `merge-<type>` for `entity` and `asset` (an instrument takes the asset branch with its own word) | agent |
| [`chart.json`](chart.json) | `request` (dashboard "Ask for a chart") | console |

The tests:
- console: [`tests/parity-golden.test.mjs`](../../incident-console-v2/tests/parity-golden.test.mjs), with the rules in [`tests/support/golden.mjs`](../../incident-console-v2/tests/support/golden.mjs);
- eval: [`tests/test_parity_golden.py`](../../eval/tests/test_parity_golden.py), which also checks that every golden is well formed;
- agent: [`tests/unit_test/incident_contract/test_parity_golden.py`](../../../../../../services/agent/tests/unit_test/incident_contract/test_parity_golden.py).

Each test also asserts that it covers every case naming its producer, so a new case cannot go unchecked.

## Format

A case has:
- `description`;
- `producers`;
- `inputs`: what the test passes to the producer. Placeholders such as `<model>` and `<video-url>` stand for per-call values;
- `transport` (optional): keys a producer may add, with exactly this value. Today that is only the agent's `"stream": false`;
- `body`.

**Comparison:**
- Bodies are compared as JSON values. Key order, whitespace and `0` against `0.0` do not count; booleans are not numbers; arrays keep their order.
- An inline image (`data:image/jpeg;base64,...`) counts as `data:image/jpeg;base64,<jpeg>`, because the encoded bytes depend on the encoder.

**Contract references:**
- The P1 prompt is stored as `{"$contract_text": "incident_extraction_prompt.txt", "sha256": "..."}`.
- The strict `response_format` is stored as `{"$contract_json": "fixtures/response_format.json", "sha256": "..."}`.
- The loader ([`load_golden.py`](load_golden.py), standard library only) substitutes the file after checking its hash. A contract change then updates one hash per case here, instead of copying 27 KB into every case.
- Every other prompt is stored literally, so moving it elsewhere must leave the golden unchanged.

## Changing a request

1. Change the producer's code. If the change is to P1 or RP1, edit the contract files here too and bump [`VERSION`](../VERSION).
2. The producer's golden test fails with a diff. Update the case's `body`, or the `sha256` of a referenced contract file, in the same PR.
3. Every other producer of the case must then match as well: that is the parity check.
4. A change that is not a contract bump also gets a line in [`.docs/decisions.md`](../../.docs/decisions.md) saying why.

**Not covered here:**
- the chat, which makes no model call (`chat-contract-v1` answers from templates);
- the experiment prompts in [`../experiments/`](../experiments/README.md), which have their own registry hashes.
