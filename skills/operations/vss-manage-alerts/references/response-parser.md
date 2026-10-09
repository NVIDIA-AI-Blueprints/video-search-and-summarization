# Custom VLM Response Parser (alert enhancement)

"Alert enhancement" means Alert Bridge turns the verification VLM's reply into
structured fields — severity, description, confidence, whatever the use case
needs — through a custom parser class, instead of reducing it to a Yes/No
verdict. This file owns the parser contract, how to generate one, a worked
example (parser and prompt), and the verifier settings it needs.

**It is a deploy-time change.** `vss-build-vision-ai` owns the wiring (mount,
config copy, approval, recreate) in
[`services/alerts.md` § Custom VLM response parser](../../../vss-build-vision-ai/references/services/alerts.md#custom-vlm-response-parser-alert-enhancement).
This skill never writes a parser into a deployment, edits Compose, or restarts
Alert Bridge; on a running stack it confirms and hands off (SKILL.md, Step 2).

## What a parser changes

- **Loaded once, at startup.** Alert Bridge reads `vlm.response_parser` (a
  dotted `module.ClassName` path) when it starts
  (`enhance_alert_with_vlm.py` `_load_pluggable_parser()` →
  `schemas.base_response_parser.load_response_parser()`). There is no runtime
  API for it: it is not a `vlm_params` key, and `PUT
  /api/v1/verification/config` does not reach it. Adding or changing a parser
  means a changed bind mount and config, and a recreated container.
- **CV verification only** (`MODE=2d_cv`). The real-time path (`2d_vlm`) never
  calls a parser.
- **Verifier pipeline only.** The parser runs on alerts and incidents that
  arrive through the pipeline (Kafka from Behavior Analytics, or `POST
  /api/v1/alerts` / `POST /api/v1/incidents`). **On-demand verification never
  runs it:** `POST /api/v1/verification/ondemand` builds its own handler without
  the parser (`ondemand_verification_service.py`), so its results keep the
  built-in parsing — the reply in `reasoning`, no `vlm_response` — even on a
  parser deployment.
- **Global.** One parser per deployment. It replaces the built-in Yes/No
  parsing for **every** alert type, and `parse()` receives only the reply text —
  it cannot tell which alert type produced it.
- **Success output.** `info.verdict` is `""` and `info.vlm_response` is
  `json.dumps(parse(reply))` — a JSON **string**; decode it once to read the
  fields. `verificationResponseCode` is `"200"`; there is no `info.reasoning`.
  **No alert becomes `confirmed` or `rejected` on this path.** A `verdict` key
  inside the parser's output is the parser's own field, not Alert Bridge's
  verdict.
- **Parse failure.** `parse()` raises or returns a non-dict →
  `verificationResponseCode "500"`, `verificationResponseStatus "Pluggable
  parser failed: <ErrorType>: <message>"`, `verdict "verification-failed"`,
  `errorSource "pluggable_parser"`, and no `vlm_response`.
- **Load failure.** A missing module or class, no `parse` method, or a
  constructor that needs arguments aborts Alert Bridge at startup. The
  container restarts in a loop and its log shows the import error instead of
  `Pluggable response parser active: '<dotted path>'`.

## Contract

- A class with `parse(self, raw_response: str) -> dict`. Subclassing anything
  from Alert Bridge is not required.
- Instantiable with no arguments.
- **Thread-safe.** Alert Bridge shares one instance across all worker threads
  (`alert_agent.num_workers`) and the async dispatcher. Never mutate `self`
  inside `parse()`; module-level compiled regexes and constants are fine.
- **Pure and fast.** `parse()` runs synchronously in the verification worker:
  no file or network I/O, no subprocesses, no sleeps, and bounded work on the
  reply text. Anything slower holds up every alert behind it.
- Return a flat dict of primitives. Nested dicts and lists stay nested inside
  the `vlm_response` string; values `json.dumps` cannot serialize (`datetime`,
  `Decimal`, `set`, `bytes`) are coerced with `str()` and logged as a warning.
- **Packaging.** A directory holding an empty `__init__.py` and
  `<module>.py`, bind-mounted read-only at `/app/parsers`; the dotted path is
  `parsers.<module>.<Class>`. The container runs as uid `65532`, so the files
  must be world-readable (`chmod -R a+rX`).
- Use `vlm.response_parser`, not `vlm.custom_parser_module` — that is the
  older registry keyed by `response_format`. Setting both logs a startup
  warning; leave `custom_parser_module` unset.

## Generate a parser

The example below is the mold: one alert type, its prompt, and the parser that
reads it. Keep its structure; change the alert type, fields and prompt wording
to what the user asked for.

1. **Design the output.** From the request, name the fields the alert needs,
   with their types and allowed values. If the request names none, ask the
   user which fields and values they want before drafting; never deploy the
   example's fields or dotted path by default. The parser is global, so every alert
   type the deployment verifies must produce a reply it accepts: give them the
   same field set, or make the parser accept each shape in use.
2. **Write the prompt** in the draft copy of `alert_type_config.json`, one entry
   per alert type, naming exactly the fields the parser reads. The system prompt
   asks for one valid JSON object only. `alert_type` must equal the `category`
   the producer emits (Behavior Analytics, `POST /api/v1/alerts`, or `POST
   /api/v1/incidents`). Size `vlm_params.max_tokens` for the reply.
   The user prompt is a template: Alert Bridge replaces every `{path}` with
   that field of the alert (for example `{place.name}`) and drops the alert
   when the field is missing. Describe the fields in words, as the example
   does, and never paste a JSON sample with single braces into it; write a
   literal brace as `{{` or `}}`. The system prompt is not templated.
3. **Write the parser**: take the `<answer>` payload or drop `<think>` blocks,
   strip a Markdown code fence, `json.loads` (falling back to a single JSON
   object embedded in prose), coerce each field, and **raise** when there is no JSON object or a
   required field is invalid. Alert Bridge then records `verification-failed`
   with `errorSource: "pluggable_parser"`, which an operator can see. A fallback
   that stores the raw text as a success hides a broken prompt or a reply cut at
   `max_tokens`; write one only when the user asks for it, and say what it
   hides.
4. **Test it before deploying** (below).

## Example: ladder PPE

An illustration to adapt, not something the profile ships: no parser is
deployed until one is generated for the user's request. `FOV Count Violation`
is the alerts profile's stock alert type (ladder PPE, the
`sample-warehouse-ladder` sample). The parser, saved as
`parsers/ppe_enhancement.py` with the dotted path
`parsers.ppe_enhancement.PPEEnhancementParser`:

```python
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Example pluggable VLM response parser for alert enhancement.

Wire it with ``vlm.response_parser: "parsers.ppe_enhancement.PPEEnhancementParser"``
and mount the ``parsers/`` directory at ``/app/parsers`` in the alert-bridge container.

Alert Bridge creates ONE instance at startup and shares it across all workers,
so ``parse()`` must not mutate ``self``. The returned dict is JSON-encoded into
``info["vlm_response"]``; ``info["verdict"]`` is left empty. Raising makes Alert
Bridge record a ``verification-failed`` event with ``errorSource: pluggable_parser``.

``verdict`` is required: a missing or unrecognised value raises. The other
fields are deliberately lenient: an unparseable ``confidence`` becomes 0.0 (and
values are clamped to 0.0-1.0), an unknown ``severity`` becomes "unknown", and
a missing ``description``/``reasoning`` becomes "". The prose fallback expects a
single JSON object; text holding several objects raises.

Expected VLM output (the prompt must ask for exactly these fields)::

    {"verdict": true, "description": "...", "severity": "high",
     "confidence": 0.9, "reasoning": "..."}
"""

import json
import re
from typing import Any, Dict

_SEVERITIES = ("low", "medium", "high")
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}

# Compiled once at import; read-only afterwards (thread-safe).
_RE_ANSWER = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL | re.IGNORECASE)
_RE_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_RE_FENCE = re.compile(r"^```(?:\w+)?\s*\n(.*?)```\s*$", re.DOTALL)
_RE_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _unwrap(text: str) -> str:
    """Drop reasoning tags and markdown fences around the JSON payload."""
    s = (text or "").strip()
    answer = _RE_ANSWER.search(s)
    s = answer.group(1).strip() if answer else _RE_THINK.sub("", s).strip()
    fence = _RE_FENCE.match(s)
    return fence.group(1).strip() if fence else s


def _load_object(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        found = _RE_OBJECT.search(text)  # JSON embedded in prose
        if not found:
            raise ValueError("VLM response contains no JSON object")
        data = json.loads(found.group(0))
    if not isinstance(data, dict):
        raise ValueError("VLM response JSON is not an object")
    return data


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(f"verdict must be true/false, got {value!r}")


def _to_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class PPEEnhancementParser:
    """Map a JSON VLM answer to verdict + context fields."""

    def parse(self, raw_response: str) -> Dict[str, Any]:
        data = _load_object(_unwrap(raw_response))
        severity = str(data.get("severity", "")).strip().lower()
        return {
            "verdict": _to_bool(data.get("verdict")),
            "description": str(data.get("description") or "").strip(),
            "severity": severity if severity in _SEVERITIES else "unknown",
            "confidence": _to_confidence(data.get("confidence")),
            "reasoning": str(data.get("reasoning") or "").strip(),
        }
```

Its `alert_type_config.json` entry — the prompt lists exactly the fields the
parser reads:

```json
{
  "alert_type": "FOV Count Violation",
  "output_category": "Ladder PPE Violation",
  "prompts": {
    "system": "You are a workplace safety assistant. Respond with one valid JSON object only, no other text.",
    "user": "Look at the people on or near the ladder in the provided frames. Decide whether anyone on the ladder is missing a hardhat or a safety vest.\nReturn JSON with exactly these fields:\n- verdict: true or false\n- description: who is missing which PPE and what they are doing (empty string if verdict is false)\n- severity: low, medium or high\n- confidence: number from 0.0 to 1.0\n- reasoning: one short sentence"
  },
  "vlm_params": {
    "max_tokens": 512
  }
}
```

## Verifier settings

Set these keys inside the existing `vlm:` block of the build's copy of the
verifier `config.yml`, replacing a key that is already there — the stock block
has `request_timeout: 5` — rather than adding a second copy, which YAML resolves
to whichever comes last. Never append a second `vlm:` block: YAML keeps only the
last one, so `base_url` and `model` disappear and every VLM call fails while the
parser still loads.

```yaml
vlm:
  response_parser: "parsers.ppe_enhancement.PPEEnhancementParser"   # the generated parser's dotted path
  request_timeout: 60              # vlm_params.request_timeout is not applied; set it here
```

Leave the other `vlm:` keys, media settings included, as the Foundation ships
them.

- `request_timeout` — the stock value is 5 seconds. Only this global value
  applies; a per-alert-type `vlm_params.request_timeout` is ignored.
- `max_tokens` — a reply cut at the limit fails `json.loads` and lands as
  `verification-failed`. A per-alert-type `vlm_params.max_tokens` overrides the
  global value ([docs note](../../../../docs/alert-verification-service.mdx)).
- Verdicts are less stable when the model answers without reasoning. To get
  it, ask in the user prompt for the reasoning inside `<think></think>` and only
  the JSON inside `<answer></answer>` — the example parser reads both — and
  raise `max_tokens` and `request_timeout` to fit the longer reply.

## Test before deploying

Import the parser exactly as Alert Bridge will and run it on the reply shapes
your prompts produce. `PARSER_ROOT` is the draft directory that holds
`parsers/` — the test runs on the draft, before the build's approval.

```bash
cd "${PARSER_ROOT:?directory that holds parsers/}" && python3 - <<'PY'
import importlib
DOTTED = "parsers.ppe_enhancement.PPEEnhancementParser"   # the vlm.response_parser value
mod, _, cls = DOTTED.rpartition(".")
parser = getattr(importlib.import_module(mod), cls)()
fence = "`" * 3
replies = [  # replace with the shapes your prompts produce
    fence + 'json\n{"verdict": true, "description": "worker on the ladder without a hardhat", "severity": "high", "confidence": 0.9, "reasoning": "r"}\n' + fence,
    '<think>one worker, vest missing</think>\n<answer>{"verdict": "yes", "severity": "medium", "confidence": 0.7}</answer>',
]
for reply in replies:
    out = parser.parse(reply)
    assert isinstance(out, dict), out
    print(out)
try:
    parser.parse("No.")
except Exception as exc:  # expected: Alert Bridge records verification-failed
    print("malformed reply raises:", type(exc).__name__, exc)
else:
    raise SystemExit("a malformed reply did not raise")
PY
```

A non-zero exit is a blocker: fix the parser before resolving the build. If
the user asked for a fallback, replace the malformed-reply check with one that
asserts the fallback's output instead.

Then list the placeholders left in the user prompts. Each name printed must be
a field of the alert payload (`place.name`, `sensorId`, …); anything else is a
stray brace that drops every alert of that type:

```bash
python3 - "${ALERT_TYPE_CONFIG:?path to the alert_type_config.json copy}" <<'PY'
import json, re, sys
for alert in json.load(open(sys.argv[1]))["alerts"]:
    user = alert["prompts"].get("user", "").replace("{{", "").replace("}}", "")
    for path in re.findall(r"\{([^}]+)\}", user):
        print(f"{alert['alert_type']}: {{{path}}}")
PY
```

## Verify after deploying

`$AB` is the Alert Bridge origin, resolved as in vss-manage-alerts SKILL.md
*Deployment prerequisite* (`http://${HOST_IP}:9080` on Docker).

1. **Loaded, by this deploy.** The container must have started after the
   deploy — an older one still runs the previous parser — and its log names
   the dotted path:

   ```bash
   docker inspect -f '{{.State.StartedAt}}' vss-alert-bridge
   docker logs vss-alert-bridge 2>&1 | grep 'Pluggable response parser active'
   ```

   A start time from before the deploy means Compose did not recreate it: the
   revision was written over the old path (vss-build-vision-ai
   `references/services/alerts.md`, *Apply and verify*). An import traceback
   instead of the log line means Alert Bridge is restarting in a loop — fix
   the parser or the path and redeploy.
2. **Every stored prompt is a JSON prompt.** At startup Alert Bridge seeds
   `alert_type_config.json` into its config store, but a value already in the
   store wins — whether it was set through the REST API or seeded from an
   earlier file — and the store lives in Elasticsearch, whose data directory
   (`$VSS_DATA_DIR/data_log/elastic/data`) survives a recreate and `down -v`. On
   a host that has run stock alerts, the stock `FOV Count Violation` Yes/No
   prompt shadows the build's JSON prompt, and the parser raises on every one of
   those alerts. List every stored alert type with `GET
   $AB/api/v1/verification/config` — the parser sees all of them — and where a
   stored `prompt`, `system_prompt` or `vlm_params` differs from the build's
   entry, set it with `PUT $AB/api/v1/verification/config/<alert_type>` (the
   `alert_type` as the list returns it, URL-encoded; Workflow B,
   `references/verification.md`). A `vlm_params` object is merged into the
   stored one, so to drop a key send `"vlm_params": null` first, then the
   build's values.
   The configs that were stored before the parser build deployed are in
   `patches/vlm-as-verifier/stored-configs.before.json` (saved from the running
   stack before the deploy; absent on a fresh host); *Remove a parser* restores
   prompts from it.
   A stored alert type with no JSON prompt in the build is a blocker: write one,
   or tell the user its alerts will land as `verification-failed`.
3. **End to end, through the pipeline.** On-demand verification cannot check
   this (it never runs the parser). Submit an incident instead, which Alert
   Bridge consumes from Kafka like a Behavior Analytics one: the alert type as
   `category`, the VIOS sensor **name** of a stream that is recording as
   `sensorId`, a 10–30 s `timestamp`–`end` window that stream has already
   recorded, a unique marker in `info`, and every field the user prompts'
   placeholders read (the listing in *Test before deploying* printed them, for
   example `"place": {"name": "<…>"}`). Mark it in `info`, not with `id`:
   the request accepts an `id`, but it only becomes the Kafka key — the
   `nv.Incident` message Alert Bridge verifies has no `id` field — while
   `info` is carried into the stored result. Alert Bridge fetches
   that clip from VST, runs the VLM and the parser, and writes the result to
   `mdx-vlm-incidents-*`.

   ```bash
   curl -sf -X POST "$AB/api/v1/incidents" -H 'Content-Type: application/json' -d '{
     "category": "<alert_type>", "sensorId": "<VIOS sensor name>",
     "timestamp": "<window start, ISO-8601 UTC>", "end": "<window end, ISO-8601 UTC>",
     "info": {"parserCheck": "<unique value>"}
   }'
   ```

   A retry needs a new window, not just a new marker: Alert Bridge drops an
   incident that repeats the sensor, `timestamp`, category and objects of one
   it saw within the last hour unless its `end` moved by more than 3 seconds
   (the stock `end_time_delta_filter`).

   Poll `GET $AB/api/v1/realtime/incidents` scoped to that sensor and a window
   bracketing the submitted one (`curl -G --data-urlencode sensor_id=<VIOS
   sensor name> --data-urlencode start_time=… --data-urlencode end_time=…`;
   the raw view returns only the newest 100, and the stored `category` is the
   alert type's `output_category`), allow at least 2 minutes, and match
   `info.parserCheck`. Pass: `verificationResponseCode` `200`, `verdict` `""`, and
   `vlm_response` decodes to the schema. `verification-failed` with
   `errorSource: "pluggable_parser"` means the parser raised;
   `verificationResponseStatus` names the exception. No document at all, with
   `Missing placeholder path` in `docker logs vss-alert-bridge`, means the
   incident (or a real producer) lacks a field a prompt reads: the alert is
   dropped before the VLM call and leaves nothing in the store.

   With no stream recording — a fresh build has no source — run steps 1 and 2
   and report that the end-to-end check was not run, and why. Do not register a
   source the user did not ask for.

## Read the results

- On the alerts profile Behavior Analytics emits incidents, so verified
  results — parser output included — are incident-kind and land in
  `mdx-vlm-incidents-*`, readable with `GET $AB/api/v1/realtime/incidents`. So
  do incidents submitted with `POST /api/v1/incidents`. Only alert-kind
  (Behavior-schema) submissions land in `mdx-vlm-alerts-*`.
- Read `info.vlm_response` and decode it once — it is a string, by contract.
  On-demand results never carry it.
- Report the parser's fields as the parser's output. Never turn its `verdict`
  field into a `confirmed` or `rejected` alert, and never call `verdict: ""` a
  failure.
- In Kibana, `info.vlm_response` is a single string field. Charting one field
  needs a runtime or scripted field (see the docs).

## Remove a parser

Removing a parser is a rebuild too, through vss-build-vision-ai: take
`./patches/alert-bridge.yml` out of the build's `compose.yml` and delete it with
every `patches/parsers*/` directory, point `VLM_AS_VERIFIER_CONFIG_FILE` and
`VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` back at the Foundation's files (a JSON
prompt file left mounted is seeded again whenever the store is empty), then
resolve and deploy as for any build. The JSON prompts stay in Elasticsearch,
and the built-in parser cannot read a JSON reply, so every one of those alerts
would land as `verification-failed`. Put each alert type's earlier prompt back
with `PUT $AB/api/v1/verification/config/<alert_type>`:

- its entry in `stored-configs.before.json`, unless the file is absent (a
  fresh host) or that entry asks for a JSON reply — a list first saved before
  a revision holds the earlier revision's JSON prompts and `vlm_params`;
- otherwise, for a stock alert type, its prompts and `vlm_params` (where the
  Foundation sets them) from the Foundation's `alert_type_config.json`; for an
  alert type you created, ask the user what it should be.

For every alert type the build gave `vlm_params` (the example's `max_tokens`
included), send `"vlm_params": null` first and then the restored values, if
any; a `vlm_params` object is merged into the stored one, so the build's keys
outlive a prompt-only `PUT`. Keep `patches/vlm-as-verifier/`, which holds
`stored-configs.before.json`, until these `PUT`s are done.

## Not supported

- **A parser per alert type.** One parser serves the whole deployment.
- **Turning parser output into `confirmed` / `rejected`.** Not on this path.
- **Real-time alerts** (`2d_vlm`). The real-time rule path never calls a parser.
- **NemoClaw sandbox.** Building a parser writes host files and recreates a
  container; the sandbox can do neither. A host coding agent, or the user, runs
  the build.
- **Kubernetes.** The Helm chart has no parser mount.

## Sources

- `services/alert/enhance_alert_with_vlm.py` (`_load_pluggable_parser`)
- `services/alert/src/schemas/base_response_parser.py`
- `services/alert/src/schemas/pluggable_parser_runtime.py`
- `services/alert/src/handlers/prompt_handler/alert_type_config_loader.py` (`seed_to_store`)
- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml`
- `docs/alert-verification-service.mdx` § Pluggable Response Parser
