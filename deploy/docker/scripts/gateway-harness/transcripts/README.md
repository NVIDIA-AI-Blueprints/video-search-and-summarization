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

## `fr35-offhost-dns-2026-09-09`

**26 pass / 1 fail / 5 skip, exit 1.**

| | |
|---|---|
| Deployment | `deployment-host`, 10.176.222.x, machine-id `fa1e16e7…`, kernel 6.8.0-136, 2× L40 |
| Agent | `agent-workstation`, 10.21.84.x, kernel 7.0.0-30 |
| Profile | `dev-profile-alerts`, Compose project `fr35` |
| Harness commit | `70be8984e` (PR #1983 head at the time of the run) |
| Canonical name | `vss-gateway.fr35.corp` → 10.176.222.x, resolved from the agent side **through DNS** (CoreDNS private zone on the test network) |
| Second origin | `http://10.176.222.x:7777` |

Same two-machine topology as the earlier run: deployment on 10.176.222.x,
agent-side container on the workstation (10.21.84.x), no shared Docker network.
The only deliberate change is how the canonical name is served: a small CoreDNS
instance on a Docker bridge network holds an A record for
`vss-gateway.fr35.corp` in a private zone (`fr35.corp`). The client container
was given `--dns` pointing at that server and **no `--add-host` entry**, so
section 1a reports resolution through DNS with no reserved-suffix warning.
This is internal DNS on the test network — not a publicly delegated record —
and it mirrors how a customer with a corporate zone would reach the deployment.

Section 1d still refuses an undeclared origin with `404` and
`x-vss-gateway-deny: unknown-host`. Section 5 shows the `vss` CLI configuring
against the canonical hostname and answering through it — ordinary resolution,
not a per-request pin.

The one failure is the same backend fault as the earlier run:
`/llm/v1/models → 502` (NIM KV-cache exhaustion on 2× 48GB L40). The harness
attributes it to the backend on the second origin.

## `fr35-offhost-loadedcfg-2026-09-09`

**26 pass / 1 fail / 5 skip, exit 1** — the same verdict as the DNS run, from a
repeat of the same harness 63 minutes later.

| | |
|---|---|
| Deployment | `deployment-host`, 10.176.222.x, machine-id `fa1e16e7…`, kernel 6.8.0-136, 2× L40 |
| Agent | container on `agent-workstation`, 10.21.84.x |
| Profile | `dev-profile-alerts`, Compose project `fr35` |
| Harness commit | `7ee377496` |
| Canonical name | `vss-gateway.fr35.corp` → 10.176.222.x, resolved by system resolution; **see the correction below** |
| Second origin | `http://10.176.222.x:7777` |

### What this run adds, and why its agent side is here at all

The two runs above record what the gateway *did*. Neither records what the
gateway had *loaded*. A template in the tree and a template in the running
process are different claims, and a transcript that rests on the first is
evidence about a file rather than about a deployment: the config has to be
shipped and the container recreated for the run to be about the commit under
test at all.

This run's deployment side reads the config out of the **running** HAProxy and
counts what is in it. Every count matches
`deploy/docker/services/infra/haproxy/haproxy.cfg.template` at `7ee377496`
exactly:

| Loaded config | Count | Template |
|---|---|---|
| `internal-only` occurrences (FR-29) | 12 | 12 |
| `x-vss-gateway-deny` markers | 6 | 6 |
| `x-vss-gateway-unavailable` markers | 3 | 3 |
| `acl known_host` lines | 18 | 18 |
| `acl h_internal` lines | 8 | 8 |
| `acl gw_internal_src` lines | 4 | 4 |
| `timeout server 900s` (`bk_vss_agent`) | 1 | 1 |
| `timeout server 600s` | 3 | 3 |

So the 403s section 6 records are the deny rules in this tree firing, not a
coincidence of some older config the container happened to still be holding.
It also captures both root causes in the file itself — the LLM NIM's KV-cache
`ValueError` and `rtvi-vlm`'s NGC authentication failure — which the runs above
have only as prose here.

Its **agent side is a near-duplicate of the DNS run** and is committed only so
the pair is a pair. Line for line the two differ in timestamps, container id,
temp paths, section 1a (below), and nothing else: the same 26/1/5, the same
sections 2–6 outcomes, the same single `/llm` 502. A deployment-side capture
with no agent side would leave the config counts unattached to any observed
request, and pairing it with the DNS run's agent side would be pairing files
from two different runs.

### Corrections — the transcript is unedited, so they belong here

**Section 1a overclaims, and the harness no longer emits that line.** It printed
"this is the FR-35 claim in its strongest form: external resolution, no
override". This run used the harness at `7ee377496`, which predates the source
discrimination added in `50c21a145`; it reported that a name resolved without
establishing *what* answered. The current harness probes for an A record, names
the source, and flags a reserved TLD. **The DNS run above is the FR-35
resolution evidence; this run is not, and its 1a line should not be quoted.**

**The verbatim deny block prints four lines under a heading that says two.**
Both are right and the heading is the loose one: there are two internal-only
*routes*, each carrying two deny conditions — one for `!h_internal` and one for
`!gw_internal_src` — so four rules. The capture truncates each line at a fixed
width, which cuts off exactly the trailing `if …` clause that tells them apart,
so they read as duplicated. The `12` in the count table is the authoritative
figure. This is a defect in the out-of-tree ssh capture wrapper, not in the
recorded config; the fix is to print the `if` clause rather than the first 140
columns.

**The raised agent timeout is evidenced as configured, not as exercised.**
`timeout server 900s` on `bk_vss_agent` is shown to be in the loaded config. No
request in this run ran long enough to need it, so nothing here shows a
long summarization surviving the edge.

### What this run does not show

- **No ingest and no stored media.** As with the runs above, `vios list` and
  `vst_sensor_list` returned empty sets. The repository's sample `.mp4`s are Git
  LFS pointers, so no recording was available to register on either host.
- **No real TLS.** The origin is plain HTTP. The warning in
  `deploy/docker/remote-agent.env.example` — that the plaintext default is a
  trusted-network assumption and not an approved one — applies to this run as
  written.
- **`/llm` is still 502**, for the reason recorded in the deployment-side file:
  a 30B model cannot allocate a KV cache on a 48GB L40 when the profile expects
  1× 96GB. It is attributed to the backend on the second origin and was **not**
  tuned away with `gpu_memory_utilization`; a transcript nobody can reproduce
  would be the worse outcome.

## Redactions

All transcripts were captured from live internal machines. What was changed
before committing, and nothing else:

| Was | Now | Why |
|---|---|---|
| the deployment host's address | `10.176.222.x` | exact address of an internal host |
| the agent workstation's addresses | `10.21.84.x`, `192.168.34.x` | same |
| the two machine names | `deployment-host`, `agent-workstation` | internal names; kept distinct because their distinctness is the evidence |
| the deployment's full `machine-id` | first 8 hex + `…` | enough to show the two hosts differ |
| a GitLab runner's container name | `runner-<redacted>-build` | it embeds a runner token and a project id, and the runner is unrelated to the profile |
| the MCP session id | first 8 hex + `…` | credential-shaped, though it expired with the run |

The same table was applied to `fr35-offhost-loadedcfg-2026-09-09`, whose
agent-side header additionally named both machines in prose
(`AGENT_HOST_NOTE`); those two names are pseudonymised to the same
`deployment-host` / `agent-workstation` pair used everywhere else, so the
sentence still says the hosts are distinct physical machines on distinct
subnets. Its `machine-id` and MCP session id are truncated to the first 8 hex
in the `… (truncated)` form the other deployment-side files use.

One further edit, not a redaction: each deployment-side header pointed at its
agent-side file by that file's original `.log` name, which is not the name it
has here. Nothing in any file's recorded output was changed.

The `/24`s are deliberately left legible: that the two hosts were on different
subnets is part of what the run demonstrates, so masking the host octet removes
the address without removing the topology. Docker bridge addresses
(`172.17.0.x`, `172.18.0.1`) are untouched — they are defaults, and
`172.17.0.3` is what shows the agent side was in a container.

No credential values appear in any of these files. `NGC_API_KEY` appears above,
and in `fr35-offhost-loadedcfg-2026-09-09.deployment-side.txt`, as a variable
name inside a quoted error message — never as a value.
