<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Recorded harness runs

Each run is a pair of files with a shared stem:

| File | Side | Produced by |
|---|---|---|
| `<stem>.agent-side.txt` | the remote agent host | `TRANSCRIPT=<path> gateway-offhost.sh` |
| `<stem>.deployment-side.txt` | the deployment host | `ssh` to the deployment host, commands quoted in its header |

`TRANSCRIPT` normally names a `.log`; the checked-in copy is `.txt` because
`*.log` is gitignored repo-wide and an evidence file that cannot be committed
is not evidence. Nothing but the extension differs.

The pair exists because the agent-side transcript alone cannot show what it was
talking to. Section 0 of the harness proves the caller is not the deployment
host, and the companion file records what the deployment host actually was —
hostname, `machine-id`, addresses, kernel, the gateway's declared identity, the
published port, and the running service set.

## `fr35-offhost-2026-09-09`

**23 pass / 1 fail / 5 skip, exit 1.**

| | |
|---|---|
| Deployment | `deployment-host`, 10.176.222.x, machine-id `fa1e16e7…`, kernel 6.8.0-136, 2× L40 |
| Agent | `agent-workstation`, 10.21.84.x, machine-id `f5eb8ec0…`, kernel 7.0.0-30 |
| Profile | `dev-profile-alerts`, Compose project `fr35` |
| Commit under test | `bcf6b2471` (PR #1983 head at the time of the run) |
| Canonical name | `vss-gateway.fr35.test` → 10.176.222.x, resolved from the agent side's **`/etc/hosts`**, not from DNS |
| Second origin | `http://10.176.222.x:7777` |

Different machines, different subnets, different kernels, different
`machine-id`s, and no Docker network in common. The agent side ran in a
container **on the workstation** so that the canonical name had a real
`/etc/hosts` entry without root — see "RESOLVE_TARGET is not good enough on its
own" in the harness README. `AGENT_HOST_NOTE` in the transcript header records
the physical machine, because `hostname` inside that container is a container
id.

### Read this before quoting the run: what it does and does not establish

The transcript is the unedited output of the run apart from the redactions
listed at the end of this file, and two of its lines claim more than the run
supports. They are left in place rather than rewritten — editing a transcript
to agree with its summary is how evidence stops being evidence — so the
corrections belong here.

**The name resolved from `/etc/hosts`, not from DNS.** Section 1a printed
"this is the FR-35 claim in its strongest form: external resolution, no
override". That line was wrong, and the harness no longer emits it for a run
like this one: the address came from the `--add-host` entry Docker wrote into
the container's `/etc/hosts`, and `vss-gateway.fr35.test` sits under `.test`, a
TLD reserved by RFC 6761 that can never be delegated in public DNS. So the run
satisfies FR-35 as SRD 9.3 words it — "`/etc/hosts` on the … remote agent
host" — and **not** the acceptance line's "external DNS resolves the same
canonical gateway hostname". What it does establish, and what a `curl
--resolve` mapping would not, is that ordinary name resolution served the whole
client: the `vss` CLI configured itself against the canonical name and answered
through it in section 5, which is the agent's actual code path. `gateway-offhost.sh`
now probes for an A record, names the source it got, and says outright when the
name is under a reserved TLD.

**The two machines are attested asymmetrically.** The deployment side is
attested by the companion file — its own `machine-id`, kernel, addresses and
service set, captured on that host. The agent side ran in a container, so
`hostname -I` inside it reports only `172.17.0.3` and section 0a compares that
against the deployment address, which is a weak comparison. What is not weak is
0b and 0c: no Compose service name resolved and none of the raw service ports
(`vss-va-mcp:9000`, `vst-ingress:30888`, `elasticsearch:9200`) answered, which
a second container on the deployment host could not have managed. That the
container's host was specifically the workstation on 10.21.84.x rests on
`AGENT_HOST_NOTE`, which is operator-supplied text. A reviewer re-running this
should take the topology from 0b/0c and from the deployment-side capture, not
from that header line.

**The negative control is a real refusal.** Section 1d aimed
`Host: fr35-not-a-declared-origin.invalid` at the same address and got
`404` with `x-vss-gateway-deny: unknown-host` — HAProxy's `known_host` ACL
answering, not a connection timeout or a dropped packet. That is the assertion
that makes 1c mean something.

**Sections 0–5 only.** This run predates section 6 (the FR-29 exposure tiers),
which was added to the harness after it, so the transcript ends at section 5.
Section 6 has no recorded run here.

### The one failure is a backend, and the harness says so

`/llm/v1/models` answered **502**. The harness replayed the same path on the
second origin, got the same 502, and attributed it:

> attribution: the same 502 comes back on http://10.176.222.x:7777, so this is
> the backend's own response relayed faithfully, not this origin's handling

The NIM's own nginx was up while its vLLM engine was not, so HAProxy had a
live server to forward to and relayed what it got. Cause, from the container's
log on the deployment host:

```
ValueError: No available memory for the cache blocks.
```

The 30B FP4 model's weights fill one 48GB L40 with nothing left for KV cache.
This box is 2× 48GB, not the 1× 96GB the profile has been run on before. It was
**not** worked around by raising `gpu_memory_utilization`: tuning the
deployment until a route passes would make the transcript describe a
configuration nobody else would reproduce.

### Two backends were genuinely unavailable, and that is recorded as SKIP

- `rtvi-vlm` — the VLM could not start: `Could not authenticate with NGC.
  Check if NGC_API_KEY and model path is correct.` The NGC credential available
  on this host is expired; `nvcr.io` returns `unauthorized` for every manifest
  fetch. Reported as a blocker rather than papered over.
- `lvs` / `video-summarization` — not part of the alerts profile.

All three answered **503 with `x-vss-gateway-unavailable`**, which is how a
remote caller distinguishes "not in this deployment" from "deployed and
broken". The run therefore exercises all three states the gateway can report:
present (200), absent (503 + marker), and broken (502, unmarked). Section `2z`
asserts the distinction actually fired.

### What this run does not show

- **No stored media.** `vios list` and the MCP `vst_sensor_list` tool both
  returned empty sets. Those are answers at exit 0, not failures — but nothing
  here exercises byte-range replay of a real recording. `gateway-brev.sh`
  section 7 covers that, on the deployment host, and needs a file-backed sensor.
- **No real TLS.** The public origin is plain HTTP, so the transport claims
  `gateway-brev.sh` reaches only in `real-tls` mode are untested here. Section
  4b asserts the `X-Forwarded-Proto` half, which is what a terminator in front
  would rely on.
- **`search` and `summarize` exited 4**, meaning unified memory is not
  configured in this deployment. That is the documented "this command group's
  backend is not here" and is a SKIP, not a pass.

## Redactions

Both transcripts were captured from live internal machines. What was changed
before committing, and nothing else:

| Was | Now | Why |
|---|---|---|
| the deployment host's address | `10.176.222.x` | exact address of an internal host |
| the agent workstation's addresses | `10.21.84.x`, `192.168.34.x` | same |
| the two machine names | `deployment-host`, `agent-workstation` | internal names; kept distinct because their distinctness is the evidence |
| the deployment's full `machine-id` | first 8 hex + `…` | enough to show the two hosts differ |
| a GitLab runner's container name | `runner-<redacted>-build` | it embeds a runner token and a project id, and the runner is unrelated to the profile |
| the MCP session id | first 8 hex + `…` | credential-shaped, though it expired with the run |

One further edit, not a redaction: the deployment-side header pointed at the
agent-side file by its original `.log` name, which is not the name it has here.
Nothing in either file's recorded output was changed.

The `/24`s are deliberately left legible: that the two hosts were on different
subnets is part of what the run demonstrates, so masking the host octet removes
the address without removing the topology. Docker bridge addresses
(`172.17.0.x`, `172.18.0.1`) are untouched — they are defaults, and
`172.17.0.3` is what shows the agent side was in a container.

No credential values appear in either file. `NGC_API_KEY` appears above as a
variable name in a quoted error message, not as a value.
