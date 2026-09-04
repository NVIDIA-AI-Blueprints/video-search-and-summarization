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

# Two-origin gateway harness

`gateway-brev.sh` asks one question: **does the gateway's contract survive being
reached on a second origin that the deployment does not listen on, whose TLS is
terminated by something outside the deployment entirely?**

That is the Brev secure-link topology, and it is where the interesting failures
live. A browser loads `https://<name>.brev.cloud`, something in front of the box
terminates TLS, and the deployment only ever sees plain HTTP on its own port. If
any service mints an absolute URL from the hop it can see rather than from the
origin the client used, the browser is handed an `http://` URL on an `https://`
page and blocks it as mixed content. Single-origin testing cannot see this at
all, because there the two origins are the same.

## Why this is a script and not a CI job

Most of what this file used to assert **has** been ported to Python and now runs
in CI. What remains here is what a CI job cannot reach: the first three need a
real TLS terminator in front of a real deployment, and the fourth needs stored
media, which a CI job has no way to produce.

| Assertion | Where it is proven |
|---|---|
| Real-TLS transport: certificate validates for the public name, HTTP/2 negotiates, HSTS | **here only**, and only in `real-tls` mode (section 6) |
| `X-Forwarded-Proto` honoured, so redirects and `Location` headers come back on the public origin | **here only** (section 3b) |
| The advertised WebSocket scheme pairs with the page scheme | **here only** (section 5b) |
| Byte-range requests: 206, `Content-Range`, measured body length, alias parity | **here only** (section 7), and only with stored media present |

In CI those are **skipped, not passed**. A green CI run is not evidence for any
of the four.

Reproducing a synthetic public origin is a developer task, which is why this
harness stays checked in rather than being deleted once CI went green.

One thing this harness does **not** cover, stated so nobody infers otherwise:

- **Mixed-content blocking in a real browser.** `curl` cannot observe it. The
  configuration-level preconditions are checked (section 3b, section 5b, and
  every minted absolute URL in section 3); the browser behaviour itself is not.

Range requests **are** covered, as of section 7 — see below. They were not
until then, and the gap is worth naming because it lasted: the `Accept-Ranges`
short-circuits have been in `haproxy.cfg.template` throughout, and nothing
anywhere asserted what came back when a client took them up on the offer.

## How to run it

Against a deployment on the same box, taking the public origin from the
deployment's own configuration:

```bash
cd deploy/docker/scripts/gateway-harness
./gateway-brev.sh [<profile-dir-name>]      # default: dev-profile-alerts
```

It runs from the host using `curl --resolve`, so no in-bridge sidecar is needed.
Everything is overridable — `PUBLIC_HOST`, `PUBLIC_PORT`, `PUBLIC_SCHEME`,
`PUBLIC_WS_SCHEME`, `RESOLVE_TARGET`, `INTERNAL_ORIGIN`; see the header comment.

The harness picks one of three modes by **probing**, not by being told, so a run
on a real Brev box and a run on a developer box differ only in what the probe
finds:

- **`real-tls`** — something actually terminates TLS for the public origin. Only
  this mode can prove the transport assertions in the table above.
- **`simulated-tls`** — the deployment declares an https public origin but
  nothing here terminates it. Requests are presented the way an external
  terminator presents them: plain HTTP, `Host:` without the default port,
  `X-Forwarded-Proto: https`.
- **`direct-http`** — no https public origin is declared at all.

### Reproducing the two-origin split without a terminator

`brevsim.env` declares a synthetic public origin, and `recreate.sh` layers it
over the gateway alone:

```bash
./recreate.sh brevsim.env vss-haproxy-ingress   # apply the synthetic origin
./gateway-brev.sh
./recreate.sh - vss-haproxy-ingress             # put the real origin back
```

Always run the restore. While `brevsim.env` is applied the gateway's declared
public origin is a name that does not resolve, so anything reaching the
deployment by its real address gets the 404 `x-vss-gateway-deny: unknown-host`
response rather than being served.

## Reading the result

Three buckets, and only one of them is a verdict on the gateway:

- **PASS / FAIL** — an assertion ran and the gateway's behaviour was or was not
  correct.
- **SKIP** — the assertion could not run here. Every skip prints its reason, and
  a skip is **never** decided by the check's own outcome. A route is only
  skipped when the compose project neither declares nor runs a service that
  could back it, which is why the summary lists `expected mounts` and `absent
  mounts` explicitly.

There is deliberately no `known()` bucket. An expected failure is still counted
and printed as `FAIL`, and the script still exits 1. That means **the tally has
to be read against the configuration that produced it** — which is what the
next section is for.

### Section 7, range requests, and the two failures it finds

Section 7 is the one place anything asserts what a byte-range request actually
returns. It needs stored media, and it **discovers** it rather than building a
path — sensor list, then `storage/timelines`, then the replay `picture/url`
API, which is the same walk the product makes. With no file-backed sensor
deployed the whole section skips and says so; that skip is not a pass. Add one
with `vss vios add --type video <file>` and re-run.

It asserts status, `Content-Range` and the **measured** body length together,
because each one alone passes for a broken response: a 206 can carry the whole
file, a correct `Content-Range` can accompany the wrong bytes, and a
1024-byte body can arrive as a truncated 200.

Two assertions fail against VIOS 3.2.0, and both are the media backend's
behaviour rather than the gateway's — the harness attributes them by replaying
the same request on the internal origin, so a reader is not left guessing which
hop to fix:

| Failure | Attribution |
|---|---|
| An unsatisfiable range answers **502**, not the 416 RFC 7233 §4.4 calls for | `vst-ingress` nginx answers 502 on both origins; HAProxy relays it faithfully |
| The minted `imageUrl` carries a **doubled scheme** (`http://http://host/…`) | VIOS mints it; the host is correct, so nothing leaks, but it is unusable verbatim |

The doubled scheme is invisible to `vss`, because
`vss_core.vios.normalise_media_url` reduces any VIOS URL to its path and
re-anchors it on the configured origin — which is exactly why it is worth
asserting here. Every caller that does *not* do that repair, including a
browser handed the REST payload directly, gets a URL it cannot fetch.

Neither is a reason to change the gateway. Both are counted and printed as
`FAIL` and both make the script exit 1, per the no-`known()` rule above.

### Expected tallies

Measured on this branch at `4f0ebe7c7`, `dev-profile-alerts`, ten of eleven
mounts present (`lvs` absent):

| Configuration | Tally | Exit |
|---|---|---|
| Deployment's own origin, no `brevsim.env` | **54 pass / 0 fail / 4 skip** | 0 |
| `brevsim.env` applied | **50 pass / 4 fail / 4 skip** | 1 |
| `brevsim.env` applied, `PUBLIC_SCHEME=https PUBLIC_WS_SCHEME=wss` | **51 pass / 4 fail / 3 skip** | 1 |

Those three predate section 7 and were taken with ten of eleven mounts. A
fourth, measured on a NAT'd AWS host with a public DNS name, plain HTTP, and
only **seven** of eleven mounts present (`alert-bridge`, `kibana`, `lvs`,
`rtvi-vlm` absent — no GPU for the VLM or LLM on that box):

| Configuration | Tally | Exit |
|---|---|---|
| `PUBLIC_HOST=<ec2 dns> PUBLIC_SCHEME=http PUBLIC_WS_SCHEME=ws`, one file sensor | **54 pass / 2 fail / 8 skip** | 1 |

The 2 failures are the VIOS ones in the table above, not gateway regressions.
Four of the eight skips are the four absent mounts, two are the TLS claims that
`direct-http` cannot reach, and the section-7 skips do not fire because a
sensor was present. Do not compare this row against the first three — a
different mount set moves every count.

**The 4 failures under `brevsim.env` are not a gateway regression.** They are
all one thing — four absolute URLs still minted on the box's real address while
the gateway has been told the public origin is the synthetic name:

```
VST_EXTERNAL_URL, VST_BASE_URL, VSS_AGENT_EXTERNAL_URL, VSS_AGENT_REPORTS_BASE_URL
    = http://<host-ip>:7777   but the declared public origin is https://vss-sim.brev.test:443
```

That is an artefact of the simulation, not a defect it found. `recreate.sh`
layers `brevsim.env` over the **gateway container only**; the agent and VST
containers keep the origin they were created with, so the two disagree by
construction. On a real Brev box the whole stack is deployed with one public
origin and these four agree. Do not "fix" them by changing the gateway.

**Why the third row differs from the second.** `PUBLIC_WS_SCHEME=wss` converts
section 5b from a skip into a pass: the check needs the advertised WebSocket
scheme, and the gateway service does not pass `VSS_PUBLIC_WS_PROTOCOL` into the
container, so the harness cannot discover it and must be told. Setting
`PUBLIC_SCHEME=https` alone leaves 5b skipped and the tally at 50/4/4.

Because the counts move with what is deployed and how the harness is
parameterised, compare a run only against another run in the **same**
configuration. A changed total is not by itself a regression.

## Scope

Only `gateway-brev.sh` and its two helpers are checked in. The other harnesses
from the same work — `gateway-proof.sh` (single-origin), `gateway-adversarial.sh`
and `gateway-dns-host.sh` — are deliberately left out: their assertions are
either already covered by the Python tests in CI or become redundant once a CI
job runs an HTTPS origin with gateway-served media. This file is kept because
that job would still not give it a home.
