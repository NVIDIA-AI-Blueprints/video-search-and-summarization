# OpenClaw introspection demo

## Install the branch skill

From this repository checkout:

```bash
openclaw skills install \
  ./skills/operations/vss-ask-video \
  --as vss-ask-video \
  --force

openclaw skills info vss-ask-video
openclaw skills check
```

Start a new OpenClaw conversation after every install or update. A stale
workspace skill has higher precedence than managed or extra-directory copies;
remove or update it if `/vss-ask-video` does not show version 3.4.0. `/video-ask`
and `/vlm` are stale/local installations, not the canonical VSS operation.

## Enabled

Configure the project-local CLI:

```bash
vss configure memory introspection \
  --enable \
  --judge-endpoint http://127.0.0.1:18789/v1 \
  --judge-model openclaw/default \
  --judge-api-key-env OPENCLAW_GATEWAY_TOKEN
```

Prepare a Markdown note with a summary and VSS job pointer, a structured VSS
record that confirms an event but omits one visual detail, a valid sensor/time
window, and RT-VLM capable of resolving that detail.

Ask:

> Was the worker who entered the loading dock wearing a safety helmet?

Expected:

```text
OpenClaw Markdown memory search
  → relevant but incomplete Markdown
  → vss memory introspect
  → structured VSS retrieval
  → configured judge says insufficient
  → grounded internal VLM inspection
  → synthesized answer
```

## Disabled

```bash
vss configure memory introspection --disable
```

Ask the same question in a new conversation. Expected:

```text
OpenClaw Markdown memory search
  → incomplete Markdown
  → vss memory get/query
  → no introspection
  → no automatic VLM escalation
  → answer states what is known and what cannot be confirmed
```

Confirm `vss configure memory show` still contains the complete judge settings,
then restore availability without repeating the endpoint:

```bash
vss configure memory introspection --enable
```

## Direct VLM control

In a new conversation, request a fresh inspection with an exact sensor and UTC
start/end range. The expected route is one `vss vlm run`; no Markdown search or
introspection is required.

`127.0.0.1` is valid only when OpenClaw Gateway and the VSS CLI share a network
namespace. Otherwise configure a private Gateway URL reachable from the CLI
process. The demo also requires memory and Elasticsearch, existing VSS records,
the configured credential environment variable, and RT-VLM when visual
follow-up is expected.
