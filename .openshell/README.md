# VSS Agent Harness: NemoClaw V1 vs. OpenShell Direct

Decision note for where the VSS harness sandbox should be provisioned from.
Compares staying on NemoClaw and moving to its V1 line against dropping NemoClaw
and driving OpenShell directly through its CLI/SDK.

Sources: [NemoClaw `v1` branch](https://github.com/NVIDIA/NemoClaw/tree/v1)
(`docs/`, `examples/`), [OpenShell docs](https://docs.nvidia.com/openshell/latest/)
at v0.1.6 and its
[architecture page](https://docs.nvidia.com/openshell/about/architecture),
[NeMo Fabric docs](https://docs.nvidia.com/nemo/fabric/) and its
[adapter reference](https://github.com/NVIDIA/NeMo-Fabric/blob/main/adapters/README.md),
this repo's `deploy/docker/scripts/nemoclaw/README.md`, `.openclaw/Dockerfile`,
and `assets/vss_nemoclaw_policy.yaml`. Claims about the Hermes image were
checked against `ghcr.io/nvidia/nemoclaw/hermes-sandbox` itself, and claims
about gateway behaviour against the OpenShell source at `v0.1.6`. Section 3's
steps were then executed end to end against gateway 0.1.6 on an `aarch64` host

---

## 1. Background — OpenShell, the layer both options sit on

### Where VSS is today

![Current NemoClaw v0.0.127 harness](assets/harness-current-nemoclaw-v0.svg)

Seven imperative steps; three coupled version pins (`BASE_IMAGE`,
`OPENCLAW_VERSION`, plugin lockfile).

### What the gateway is

OpenShell's control plane, and the piece both options rest on. One long-lived
container per deployment, the only component that touches the container
backend, with a single listener that serves both the CLI/SDK and the
supervisors dialing in. Per the
[architecture page](https://docs.nvidia.com/openshell/about/architecture), it
creates each sandbox as two parts — a trusted **supervisor** and an isolated
**workload** — keeps their state in SQLite or Postgres, issues the JWTs that
bind them, and delivers policy and provider credentials to the supervisor.

Enforcement is not its job — that belongs to the supervisor, below. Logs,
Prometheus metrics and OCSF audit records flow back through the gateway.

One sandbox is therefore two containers, and the trust split lands on that
boundary — identical under either option, since both call the same gateway. The
supervisor is the fragile half: on 0.1.6, if it exits, the driver marks the
sandbox `ControlSupervisorExited` and stops the workload — the failure mode
[#11997](https://github.com/NVIDIA/NemoClaw/issues/11997) reports. A restart
brings up a fresh supervisor and keeps the workload's files, not its process.

**VSS already runs on this.** `.openclaw/Dockerfile` builds an OpenShell sandbox
image, `assets/vss_nemoclaw_policy.yaml` is an OpenShell policy, and steps 3b
and 7 call `openshell` directly today. NemoClaw sits above it as a provisioning
wrapper that authors the policy and the create call; it never replaced the
runtime. Option 2 changes who drives the control plane, not the control plane.

### What the supervisor is

The trusted half of a sandbox, one per sandbox, and the only component that
decides anything at runtime. The gateway creates it and hands it the policy and
the provider credentials; from there every connection the agent attempts is
matched against that policy, credentialed, resolved and dialed by the
supervisor on the agent's behalf. The workload has no network of its own, so
there is no second path out. *What OpenShell enforces* below covers the rules it
applies; what matters here is where it comes from and what that costs.

**It ships with the gateway, not with the harness image.** On 0.1.x both halves
come from images the gateway is configured with — `openshell-sandbox` copied
into the workload as PID 1, `openshell-supervisor` run as a companion container
— so the supervisor's version tracks the gateway, not `.openclaw/Dockerfile`.
Moving to 0.1.x is therefore a gateway upgrade, not an image rebuild.

That also means a containerised gateway no longer needs its data directory at
the same absolute path on host and container. The upstream Compose recipe still
says it does, but at v0.1.5 the driver's `build_binds` returns nothing and its
README states "No workload launch depends on a host bind mount".

### What OpenShell deploys

The first three rows are declared once per deployment in VSS's Compose file
(§3 step 1). The rest are made by the gateway's compute driver — Docker here,
selected by `compute_driver = "docker"` — through the host's Docker socket on
each `sandbox create`. The driver builds the boundary but never makes a policy
decision.

| Resource | Process | Contents and networking |
|---|---|---|
| **Per deployment** — Compose, `harness` profile | | |
| `openshell-init` container | `generate-certs`, then exits | One-shot: writes the JWT signing keys and mTLS certificates into the data volume |
| `openshell-gateway` container | `openshell-gateway` | Host networking, one listener on `127.0.0.1:8080` for the CLI, SDK and supervisors, health on `:8081`; mounts the Docker socket |
| `openshell-data` volume | — | `gateway.db` (SQLite record of every sandbox), the JWT signing key, and the key-encryption key that seals provider credentials |
| **Per sandbox** — the Docker driver | | |
| Workload container | `openshell-sandbox` as PID 1, the harness (`hermes gateway run`) as its child | The harness image, `network_mode=none`, one non-root UID, all capabilities dropped |
| Supervisor container | `openshell-supervisor` | From the supervisor image, host networking; dials the gateway and makes every outbound connection |
| Channel volumes | — | Sandbox bootstrap keys, and the Unix socket the two containers speak over — never the network |
| Supervisor-only volume | — | The supervisor's JWT and gateway credentials; never mounted into the workload |

![How OpenShell brings up the gateway and a sandbox](assets/vss-openshell-deploy-sequence.svg)

0.1.6 reversed which end of that socket dials the other, which imposes a
version rule: a new sandbox needs matching supervisor and sandbox-runtime
binaries, so step 1 pins both images with the gateway.

The per-sandbox rows are siblings of the gateway container, not part of any
Compose project: `compose down` leaves them running, and `openshell sandbox
delete` removes both containers and their volumes.

### What OpenShell enforces

The security boundary is OpenShell's, and it sits outside the agent's trust
domain, so a compromised agent cannot switch it off:

- **Filesystem and syscalls** — Landlock confinement and seccomp filtering, in
  the kernel.
- **Network** — default-deny; a connection is allowed only when its
  destination *and* calling binary match the same policy block. On 0.1.x the
  workload has no network at all and reaches out only through the supervisor.
- **Credentials** — the agent sees an `openshell:resolve:env:<KEY>`
  placeholder; the real secret is added at egress for approved endpoints only.
- **Process** — non-root identity (root is rejected), and the image's
  `ENTRYPOINT` is replaced by the supervisor.
- **Policy changes** — the prover checks each change before approval and flags
  new credentialed reach, new HTTP methods and cloud-metadata access.

### What NemoClaw adds

Four things, and none of them is the boundary above:

| What | Where it lives | Does it harden the sandbox? |
|---|---|---|
| **Harness configuration** — image, policy additions, entrypoint, manifest | `agents/<name>/` | No. It decides what runs and what the policy *says*; OpenShell is still the thing that enforces it |
| **Toolchain purge** — `gcc`, `g++`, `cpp`, `make`, `netcat`, `ncat` removed | the published base image | Yes, genuinely. OpenShell accepts any image and has no view of the build |
| **Build-arg credential filtering** — provider keys and bot tokens stripped from the create command | the `nemoclaw` CLI | Yes, genuinely, but narrow: it keeps secrets out of image history, not out of the sandbox |
| **Entrypoint hardening** — `ulimit` rlimits, capability drops, `setpriv` privilege separation | `scripts/nemoclaw-start.sh` | No, in practice — see below |

The entrypoint row is the one to be careful about, because it is where
NemoClaw's own documentation puts the emphasis. The start script drops
capabilities and re-executes the agent under `setpriv`, but OpenShell runs the
sandbox with `no-new-privileges`, which blocks exactly that call — the script's
own comment says so. OpenShell also discards the image `ENTRYPOINT` at create,
and `.openclaw/Dockerfile` already deletes the rlimit hooks and asserts they
are gone. On the deployed stack this row contributes nothing.

**V1 drops the image-side hardening.** `image/fabric/Dockerfile` has no purge,
no `setpriv` and no rlimit hooks; it runs as `USER node`, uid 10001. The purge
is less dropped than made unnecessary: `build-essential` is confined to a
`common-wheels` builder stage that is discarded, so the runtime image never
installs the toolchain the purge existed to remove. Build-arg filtering is the
one addition that survives. V1's posture is therefore OpenShell's, reached
through a cleaner base image instead of a cleanup step.

Under Option 2 that leaves two items to carry into a Dockerfile VSS already
owns — the toolchain purge and the build-arg denylist. Both are one-time and
bounded. Everything else is configuration OpenShell would enforce anyway, or
inert.

### Sandbox shape: deployed 0.0.116 vs target 0.1.x

![OpenShell sandbox, 0.0.116 and 0.1.x](assets/vss-openshell-sandbox-versions.svg)

The host runs `openshell-gateway 0.0.116`, where the supervisor runs *inside* the
workload — one container, with the policy proxy, OPA engine and inference router
sharing a trust domain with the agent. 0.1.x moves the supervisor into its own
container, turns the workload's networking off entirely, and makes the mediated
channel the only way out, so a compromised agent can no longer reach the
component that decides for it. That is the security case for the 0.1.x upgrade
both options already require, and it is why a running deployment shows one
sandbox container today rather than two.

This is the sandbox boundary only. Where the gateway itself runs is a separate
question, and the three options genuinely differ — see below.

### Where the gateway runs, per option

| | How the gateway runs | Where that comes from |
|---|---|---|
| **Current (v0.0.127)** | Native binary under a systemd user unit — `ExecStart=openshell-gateway`, `StateDirectory=openshell/gateway`, certs generated with `--server-san host.openshell.internal`. Homebrew service on macOS. **No container.** | `scripts/lib/openshell-gateway.service.in` and `scripts/install.sh` on NemoClaw `main`; the unit on this host matches the template verbatim |
| **Option 1 (V1), `management: managed`** | **A container**, from an SDK-pinned digest `ghcr.io/nvidia/openshell/gateway@sha256:bdb4fe24…`, created on `engine: unix:///var/run/docker.sock`, user API on `http://127.0.0.1:17681`, its own `172.30.N.0/24` where N is the first byte of SHA-256(`metadata.uid`). `imagePullPolicy` accepts `IfNotPresent`/`Never` on Docker; `Always` is rejected. Managed gateways speak plain local HTTP and **reject** `credential` and `tls`. | `docs/reference/configuration.md` §Gateway Alternative 1, branch `v1` |
| **Option 1 (V1), `management: external`** | No gateway created. Point `endpoint:` at one that already exists; `credential` and `tls` are accepted here. Most checked-in examples use this. | same §Gateway Alternative 2; `local.yaml`, `fabric-openclaw.yaml`, `explicit-policy.yaml` |
| **Option 2 (OpenShell direct)** | **A Compose service VSS declares** — `ghcr.io/nvidia/openshell/gateway` behind a `harness` profile in the VSS project. Accepts the Docker-socket mount as the price of dropping the host unit. The alternative OpenShell ships is its installer's own systemd unit, deliberately not used; see step 1. | ["Running the Gateway as a Container"](https://docs.nvidia.com/openshell/latest/how-it-works/gateways/container-deployment) and ["Manage Gateways"](https://docs.nvidia.com/openshell/v0.1.5/how-it-works/gateways/overview) |

Both options therefore run the gateway as a container, and the remaining
difference is who declares it: V1 from `deployment.yaml`, Option 2 from a
Compose file VSS owns. That narrows what V1's `management: managed` uniquely
buys to a pinned digest and an owned network. Nothing in either option puts the
gateway *inside* the sandbox — it signs every credential in the system, so it is
always on the other side of the boundary from the agent.

---

## 2. Option 1 — VSS moves to NemoClaw V1

### What V1 is

A ground-up Rust rewrite. Desired-state YAML is compiled by the SDK into
**OpenTofu** graphs, executed through five providers (Docker, Helm, OpenShell,
Fabric, NemoClaw). **NeMo Fabric** is a new layer inside the sandbox that owns
adapter identity, native configuration schemas, validation and agent execution.
The whole CLI is six commands: `onboard`, `plan`, `apply`, `export`, `destroy`,
`plan --destroy`. Every per-sandbox imperative command VSS uses today is gone.

| Command | What it does |
|---|---|
| `onboard [FILE]` | Interactive wizard that writes a new `deployment.yaml`; needs a terminal and creates nothing |
| `plan FILE` | Previews the changes `apply` would make, without touching runtime resources |
| `apply FILE` | Computes a checked plan and executes it: creates or updates the gateway, sandbox, providers and policy, then checks readiness |
| `export` | Writes the deployment as currently observed, from retained state, back out as YAML |
| `destroy` | Removes the workloads it owns, without confirmation, deleting sandbox files and conversation history |
| `plan --destroy` | Previews what `destroy` would remove and what it would retain |

### Concrete steps

**1 — Build the toolchain and bundle** (no release exists, so this replaces
`curl install.sh | bash`):

```bash
cargo ci tools                                   # pinned protoc -> .tools/protoc-36.1
cargo run -p nemoclaw-build -- bundle            # -> dist/linux_amd64
export PATH="$PWD/dist/linux_amd64/bin:$PATH"
nemoclaw --help                                  # must list plan, apply, export, destroy
```

**2 — Build the agent image.** This is where VSS stops:

```bash
cargo images build --platform linux/amd64 openclaw
docker image inspect nc-fabric:openclaw --format '{{index .RepoDigests 0}}'
```

That yields a *stock* OpenClaw image. `plan` requires Fabric discovery metadata
that only this builder attaches, and there is no documented way to add the VSS
plugin, skills tree, workspace docs and `vss` CLI while keeping it.

**3 — Author `deployment.yaml`** (abridged; the policy block is the long part):

```yaml
apiVersion: nemoclaw.nvidia.com/v1alpha1
kind: NemoClawConfig
metadata:
  name: vss-harness
  uid: 6bf061fe-34ce-492a-9be0-f81182de72b2      # fresh uuidgen = deployment identity
spec:
  gateway:
    management: managed                           # NemoClaw brings up the OpenShell gateway
    runtime: { provider: docker }
    endpoint: http://127.0.0.1:17681
    engine: unix:///var/run/docker.sock
    image: ghcr.io/nvidia/openshell/gateway@sha256:...
    networkCIDR: 172.30.121.0/24
  inferenceProviders:
    - name: nvidia-hub
      provider: openai
      api: openai-responses
      endpoint: https://inference-api.nvidia.com/v1
      credential: { env: COMPATIBLE_API_KEY }     # reference, never the value
  sandboxes:
    - name: vss-harness-sandbox
      image:
        ref: <Fabric-labelled VSS harness image>@sha256:...   # <-- unresolved, see step 2
      harness:
        kind: nvidia.fabric.openclaw
        settings:
          native_config:
            gateway:
              mode: local
              bind: loopback
              port: 18789
              controlUi:
                enabled: true
                allowedOrigins: ["https://<brev-secure-link>"]   # replaces CHAT_UI_URL
      network:
        policy:
          explicit:                               # replaces the ENTIRE preset
            version: 1
            filesystem_policy:                    # /opt covers /opt/fabric + /opt/nemoclaw
              read_only:  [/usr, /bin, /sbin, /lib, /lib64, /etc, /opt, /proc]
              read_write: [/sandbox, /tmp, /dev/null, /dev/urandom]
            landlock: { compatibility: best_effort }
            process: { run_as_user: "10001", run_as_group: "10001" }
            network_policies:
              vss-backend:
                name: vss-backend-readwrite
                endpoints:
                  - { host: host.openshell.internal, port: 7777, access: full,
                      allowed_ips: [172.17.0.0/16, 172.18.0.0/16] }
                  - { host: host.openshell.internal, port: 9988, access: full,
                      allowed_ips: [172.17.0.0/16, 172.18.0.0/16] }
                binaries: [{ path: /usr/local/bin/node }, { path: /usr/bin/python3 }]
              ngc:
                name: ngc
                endpoints:
                  - { host: authn.nvidia.com,      port: 443, access: full }
                  - { host: api.ngc.nvidia.com,    port: 443, access: full }
                  - { host: xfiles.ngc.nvidia.com, port: 443, access: full }
                binaries: [{ path: /usr/local/lib/ngc-cli/ngc }]
              # ... the remaining 9 policies from assets/vss_nemoclaw_policy.yaml,
              # with all 10 `tls: terminate` lines dropped (rejected by the pinned
              # OpenShell) and `enforcement: enforce` made explicit (the default is
              # now audit, which logs instead of blocking)
      agent:
        name: main
        inference:
          routes:
            - name: primary
              providerRef: nvidia-hub
              overrides:
                model: aws/anthropic/bedrock-claude-opus-5
```

**4 — Plan and apply:**

```bash
nemoclaw plan  deployment.yaml --state-dir .nemoclaw --non-interactive
nemoclaw apply deployment.yaml --state-dir .nemoclaw --non-interactive -o json
# exits nonzero with reason_code=fabric_health_unsupported (#13031), but keeps
# the sandbox and agent files; verify with `fabric-agent invoke` instead
```

> **Note — non-interactive moved from `onboard` to `apply`.** V1 `onboard` only
> authors YAML: it needs a terminal and creates nothing. Automation skips it,
> commits `deployment.yaml` (or a template rendered and piped in as
> `apply -`), and runs `apply --non-interactive`, which reads credentials from
> env and fails naming any that are missing. Keep `metadata.uid` and
> `--state-dir` fixed for the deployment's life. Later changes are YAML edits
> plus re-apply, replacing `policy-add` and `config set`.

**5 — VSS-side work is unchanged and still imperative:**

```bash
export OPENSHELL_GATEWAY_ENDPOINT=http://127.0.0.1:17681
export OPENSHELL_WORKSPACE=<value reported by plan/apply>
openshell sandbox exec -n vss-harness-sandbox -- vss configure --base-url "$VSS_PUBLIC_URL"
openshell sandbox exec -n vss-harness-sandbox -- vss configure check
openshell sandbox exec -n vss-harness-sandbox -- vss-openclaw-sync
openshell forward start 18789 vss-harness-sandbox -d
```

### Architecture

![Option 1 - NemoClaw V1](assets/harness-option1-nemoclaw-v1.svg)

The ❓ is step 2, stated plainly: `cargo images build` is the only producer of a
Fabric-labelled image, and nothing documents how to get the VSS plugin, skills
tree, workspace docs and `vss` CLI *into* what it emits. Two candidates, neither
confirmed: a second Dockerfile stage `FROM nc-fabric:openclaw@sha256:…`, which
should inherit the `io.nemoclaw.fabric.bridge` label and `/opt/nemoclaw/bridge.json`
that `plan` requires, or teaching the builder itself to emit a VSS variant.
Re-running `docker buildx bake` over a modified tree is not a third option — it
omits those labels, so `plan` cannot select the result.

### Pros / cons

**Pros**

- **One document replaces three steps.** `onboard --from`, `policy-add --from-file`
  and `config set` become fields. Steps 1, 3b, 4, 5 and 7 of the canonical flow
  (`deploy/docker/scripts/nemoclaw/README.md`) do not collapse.
- **`plan` is a real dry run.** Reports resource actions, retained resources,
  deferred work and unverified checks before anything mutates.
- **Automation gets honest.** `--non-interactive -o json` with real exit codes
  replaces the `run_setup_notebook.py` hack that injects
  `NAME = os.environ.get("NAME", NAME)` because section 1.2's three provider
  cells all execute and the last one wins.
- **`controlUi.allowedOrigins` is editable.** A wrong Brev origin becomes an edit
  and a re-apply instead of a sandbox that is dead for its lifetime.
- **Credentials stay references.** `credential: {env: NAME}` never enters state,
  output or diagnostics.
- **Named, shared blocks.** `harnesses:`, `inferences:`, `integrations:` across
  1–32 sandboxes. Discount the inference half — `deploy_vss_switchyard.ipynb`
  already routes through a model router, which `inferences:` would merely declare
  as one endpoint.
- **The gateway stops being a host service.** `management: managed` runs it as a
  container from an SDK-pinned digest with an owned network and storage, so
  there is no systemd unit to install or version-match. Its `networkCIDR` is
  declared, so the policy's `allowed_ips` can match a stated subnet instead of
  the deploy-time `docker inspect` that `sandbox_host_cidrs()` falls back from
  whenever the sandbox is not up yet. Option 2 can run the same container; V1 is
  what makes both declarative.

**Cons**

- **No path to a VSS harness image.** The blocking item. Fabric metadata comes
  only from `cargo images build`; `.openclaw/Dockerfile` starts from a v0 runtime
  digest that has none. Worse, `deploy_nemo_relay.ipynb` assembles part of the
  harness *after* the sandbox is running (`openclaw plugins install` via
  `nemoclaw exec`, gateway restart, load check) — V1 has no equivalent of
  mutating a live sandbox, so that notebook becomes an image-build step first.
- **Policy surgery, not translation.** Explicit policy replaces the whole preset,
  so filesystem and process sections must be authored; `/opt/fabric` and
  `/opt/nemoclaw` read grants are validated and fail `plan` if missing; 10
  `tls: terminate` lines are rejected; omitted `enforcement` silently means audit.
  No reusable policy block, so a second sandbox duplicates ~470 lines.
- **Blast radius on recovery.** There is no targeted sandbox replacement or
  runtime-reset command. The documented way out of an `unknown` runtime is
  whole-deployment `destroy`, which deletes every bound sandbox's files and
  conversation history, including other agents' in the same deployment.
- **`apply` always exits nonzero** at the Fabric health step
  ([#13031](https://github.com/NVIDIA/NemoClaw/issues/13031)), so automation must
  accept `fabric_health_unsupported` and check the agent itself.
- **VSS pins and ships a source-built bundle.** No release
  ([#12638](https://github.com/NVIDIA/NemoClaw/issues/12638)) and no
  compatibility policy ([#12645](https://github.com/NVIDIA/NemoClaw/issues/12645)),
  so each deployment keeps the bundle and state directory it was created with.
- **Image changes are new deployments.** Apply refuses to replace a sandbox
  image, so every `VSS_REF`, plugin or skills bump needs a fresh UID and state
  directory, with the agent workspace — its memory — carried across by hand using
  `openshell sandbox download`/`upload`, which is Option 2's mechanism anyway.
- **An always-on agent on the platform V1 is built for**
  ([#11997](https://github.com/NVIDIA/NemoClaw/issues/11997)). V1 bundles and
  images are Linux AMD64, which leaves VSS's `aarch64` edge targets — DGX Spark
  and Thor, where `-sbsa` images already ship — unaddressed either way. A
  CPU-only Brev run got an OpenClaw reply, but no run has been
  observed past the 15-minute bearer-renewal boundary, where an earlier one froze
  with `ControlSupervisorExited` — an agent going silent mid-session rather than
  failing at deploy. Closed `not_planned` for replanning, **not** because it was
  fixed; `docs/limits.md` still lists the limit.

---

## 3. Option 2 — VSS drops NemoClaw, drives OpenShell directly

### Command mapping

| VSS step today | OpenShell direct |
|---|---|
| `curl install.sh \| bash` | an `openshell-gateway` Compose service from the published image, registered with `openshell gateway add` |
| `nemoclaw onboard --from <Dockerfile>` | `docker build` + `openshell sandbox create --from <image>` |
| `policy-add --from-file` | `--policy ./policy-<harness>.yaml` at create, or `COPY policy-<harness>.yaml /etc/openshell/policy.yaml` |
| provider registration at onboard | `openshell profile import` + `provider create` + `--provider` |
| `config set` (hooks) | image-level config overlay, already owned by the harness Dockerfile |
| `mcp add` | network grant in the policy; the HTTP path already needs nothing |
| `gateway-token` | Still a read-out, both harnesses: Hermes mints its own `API_SERVER_KEY` in the sandbox, OpenClaw keeps a gateway token the UI adapter uses to bypass device pairing. `sandbox exec` fetches either |
| `upload` ENV.md | `--env` at create; `openshell sandbox upload` for the Kubernetes-only rendered copy |
| forward + relay + watchdog | `openshell service expose` — a gateway-managed URL |
| `nemoclaw-start` as the entrypoint | the harness command itself after `--`, so OpenShell tracks the agent's exit status |

### Concrete steps

**1 — Gateway becomes a Compose service.** Decided: the gateway is a container
VSS declares, replacing the systemd unit NemoClaw installs today. OpenShell
publishes a reference
[`deploy/docker/docker-compose.yml`](https://docs.nvidia.com/openshell/latest/how-it-works/gateways/container-deployment)
to start from:

```yaml
services:
  openshell-init:                # one-shot: writes the sandbox JWT signing
    image: ghcr.io/nvidia/openshell/gateway:${OPENSHELL_TAG}  # keys the Docker
    profiles: [harness]                                       # driver requires
    command: [generate-certs, --output-dir, /var/lib/openshell/tls]
    user: "0"                    # seeds a root-owned fresh named volume
    environment: { HOME: /var/lib/openshell }
    volumes: [openshell-data:/var/lib/openshell]
  openshell-gateway:
    image: ghcr.io/nvidia/openshell/gateway:${OPENSHELL_TAG}
    profiles: [harness]          # never materialises for a no-harness build
    depends_on: { openshell-init: { condition: service_completed_successfully } }
    restart: unless-stopped
    network_mode: host           # the supervisor dials the host's own loopback
    command: ["--bind-address", "127.0.0.1", "--port", "8080"]
    group_add: ["988"]           # docker GID, numeric: distroless, no /etc/group
    environment:
      OPENSHELL_GATEWAY_CONFIG: /etc/openshell/gateway.toml
      OPENSHELL_DB_URL: "sqlite:/var/lib/openshell/gateway.db?mode=rwc"  # barred from the TOML
      XDG_DATA_HOME: /var/lib/openshell
      HOME: /var/lib/openshell
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - openshell-data:/var/lib/openshell
      - ./gateway.toml:/etc/openshell/gateway.toml:ro
volumes:
  openshell-data: {}
```

`gateway.toml` is upstream's
[`deploy/docker/gateway.toml`](https://github.com/NVIDIA/OpenShell/blob/v0.1.6/deploy/docker/gateway.toml),
with `health_bind_address` on loopback and the
JWT key paths `init` writes. Three edits VSS has to make to it:

- **Pin `sandbox_runtime_image` and `supervisor_image` to the gateway's
  release.** Upstream ships both as `latest`. Since 0.1.6 a new sandbox
  requires matching supervisor and sandbox-runtime binaries, so a drifting tag
  is a sandbox that refuses to start, not a version-hygiene nicety.
- **Write the version 2 schema.** 0.1.5 rejects the `version = 1` file NemoClaw
  installs on this host: `this build requires version 2; migrate legacy
  fields`. The renames that bite, each a separate startup failure:
  `compute_drivers = ["docker"]` becomes `compute_driver = "docker"`,
  `[openshell.drivers.docker].sandbox_namespace` is gone, and
  `gateway_jwt.ttl_secs = 0` — what NemoClaw writes — is refused as "expected a
  nonzero u64".
- **Decide the authentication posture, and configure the sandbox JWT
  regardless.** 0.1.6 refuses to start unless some user authentication is
  configured; upstream's recipe takes the opt-out,
  `allow_unauthenticated_users = true`, which is defensible only because the
  listener is on loopback — it means any process on the host can drive the
  control plane, so a shared host needs the mTLS certificates `init` already
  writes. The opt-out does not cover the Docker driver's separate requirement:
  without a `[openshell.gateway.gateway_jwt]` block the gateway starts and
  serves happily, then fails every `sandbox create` with `docker sandboxes
  require launch-scoped gateway authentication`. That is what `openshell-init`
  above is for.

```bash
docker compose --profile harness up -d
openshell gateway add http://127.0.0.1:8080 --local --name vss
```

Three constraints the service has to respect:

- **Host networking, loopback bind.** `supervisor_network_mode` defaults to
  `host`, so the supervisor shares the host's network namespace and dials
  `127.0.0.1:<gateway port>` when no `grpc_endpoint` is set. Putting the
  gateway on host networking too means there is no published port to get wrong
  and no bridge peers that can reach it; a LAN machine dialling `127.0.0.1`
  only ever reaches itself. Do not switch the supervisor to `bridge`: it would
  then have its own loopback, and the driver rejects a loopback-only gateway
  for it. The alternative — a dedicated Compose network joined only by the
  gateway and `vss-ui`, with the gateway bound there — is defensible and keeps
  the UI on Compose DNS, but it trades a boundary Docker enforces for one a
  network declaration enforces, and whoever reaches the listener can create
  sandboxes and read credentials while `allow_unauthenticated_users` is `true`.
- **Pass the bind flags explicitly.** The image's `CMD` is `--bind-address
  0.0.0.0 --port 8080` and flags beat the TOML, so the TOML's `bind_address` is
  ignored unless the command is replaced. Name the flags rather than clearing
  the command with `command: []`, which Compose treats as unset.
- **Docker socket, not privilege.** This is Docker-outside-of-Docker: the
  gateway creates sibling containers on the host. It needs read/write on the
  socket via `group_add`, not `--privileged`, and not root in production.

The data directory is a named volume, and needs backups rather than being
treated as cache: it holds the SQLite record of every sandbox, the JWT signing
key, and the key-encryption key that provider credentials are sealed with.
Keeping it across `compose down` is what keeps sandboxes' gateway records.

**One project, one profile.** The gateway sits in the VSS project behind the
`harness` profile, so a deployment stays one `compose up` and one `compose
down`, and a no-harness build — the correct outcome for one that only ingests,
indexes or serves an API — never materialises a Docker-socket mount it would
not use. Ordering is not a reason to split the projects: the harness is
installed against a build that is already running. The sandbox still never
becomes a service; Compose owns the gateway, and the gateway owns the sandboxes
it creates as siblings.

**Why not the installer.** No CLI verb provisions a gateway, so the only
alternative is OpenShell's installer, which runs the same binary under a
systemd user unit — the unit Option 2 exists to drop. Declaring the Compose
service instead keeps the image tag, port and mounts under review with the rest
of the stack rather than as host state. The cost is the socket mount above.

**2 — Convert the policy and prove it.** Translate the preset to OpenShell policy
YAML, then verify the result rather than eyeballing it.

`policy-hermes.yaml` is the candidate — the preset's 11 policies in explicit
form, plus the filesystem, landlock and process sections the preset used to
inherit from the base policy (abridged). It is per-harness because OpenShell
matches a connection on its destination *and* its calling binary, so the
`binaries` lists below are what make it Hermes's rather than OpenClaw's:

```yaml
version: 1
filesystem_policy:
  read_only:  [/usr, /bin, /sbin, /lib, /lib64, /etc, /opt, /proc]
  read_write: [/sandbox, /tmp, /dev/null, /dev/urandom]
landlock: { compatibility: best_effort }
process:  { run_as_user: "998", run_as_group: "999" }   # `sandbox` in .hermes
network_policies:
  vss-backend:
    name: vss-backend-readwrite
    endpoints:
      - { host: host.openshell.internal, port: 7777, protocol: tcp }
      - { host: host.openshell.internal, port: 9988, protocol: tcp }
    binaries: [{ path: /usr/bin/python3.13 }]
  claude_code:
    name: claude_code
    endpoints:
      - host: api.anthropic.com
        port: 443
        protocol: rest
        enforcement: enforce        # audit-mode REST is `unsupported`, not a pass
        rules:
          - allow: { method: POST, path: "/v1/**" }
    binaries: [{ path: /usr/local/bin/node }]
  # ... the remaining 9 policies from assets/vss_nemoclaw_policy.yaml
```

**`binaries` must name the interpreter, not the launcher.** OpenShell matches a
process by the real path the kernel reports, and every `hermes` entry point in
the image is a Python script, so no process ever carries one of those paths —
the connection is opened by `/usr/bin/python3.13`. OpenShell's documentation
makes the same point with `pip`. Inspecting the image also gives `sandbox` as
`998:999`, not the `10001` of the OpenClaw base, so one `boundary-policy.yaml`
cannot cover both harnesses: the prover compares process identity and exact
binary paths, and the two images agree on neither.

**A plain TCP grant is `protocol: tcp`, not the preset's `access: full`, and an
L4 grant cannot share a named policy with REST rules.** That is why the VSS
ports and the harness's REST hosts are separate named entries above.

The `allowed_ips` the preset carries from 0.0.116 are dropped above: with the
default host-mode supervisor the driver publishes host loopback as the backend
address for `host.openshell.internal`, so the `172.17.0.0/16`-style grants
match nothing.

`boundary-policy.yaml` is the ceiling VSS is willing to grant a harness sandbox,
written once by hand. Same paths and same binaries, deliberately wider where it
matters — every method on Anthropic rather than `POST /v1/**`:

```yaml
version: 1
filesystem_policy:
  read_only:  [/usr, /bin, /sbin, /lib, /lib64, /etc, /opt, /proc]
  read_write: [/sandbox, /tmp, /dev/null, /dev/urandom]
landlock: { compatibility: best_effort }
process:  { run_as_user: "998", run_as_group: "999" }
network_policies:
  vss-backend-max:               # L4 only; mixing REST in here is `unsupported`
    name: vss-backend-max
    endpoints:
      - { host: host.openshell.internal, port: 7777, protocol: tcp }
      - { host: host.openshell.internal, port: 9988, protocol: tcp }
    binaries: [{ path: /usr/bin/python3.13 }]
  harness-rest-max:
    name: harness-rest-max
    endpoints:
      - host: api.anthropic.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow: { method: GET,  path: "/**" }
          - allow: { method: POST, path: "/**" }
    binaries: [{ path: /usr/bin/python3.13 }]
```

```bash
openshell-prover check policy-hermes.yaml --boundary boundary-policy.yaml
# result: within_boundary
# coverage: domains=filesystem,network_l4,network_rest,process,landlock
```

Both files must name the **same** filesystem paths and the same exact binary
paths: a candidate `/tmp/cache` under a boundary `/tmp`, or `/usr/bin/curl` under
a boundary glob `/usr/bin/*`, returns `unsupported` rather than passing, because
an image symlink could redirect either. The prover also refuses policies using
GraphQL or MCP rules rather than ignoring them, so the result is honest about
what it covered.

**3 — Build the image and bake the policy in**, so one artifact carries the
plugin, skills, workspace docs, `vss` CLI and the access boundary at one
`VSS_REF`. `.hermes/` already produces that image;
[`.openshell/Dockerfile.policy`](Dockerfile.policy)
derives from it and adds the policy plus two fixes for assumptions the NemoClaw
base makes about its runtime:

- **CA bundle.** The image's certifi `cacert.pem` links to NemoClaw's proxy-CA
  path, so the overlay links that path to where OpenShell 0.1.6 mounts the
  bundle, `/run/openshell-supervisor-ca/material/ca-bundle.crt`.
- **Inference target.** `config.yaml` names the managed inference route 0.1.6
  replaced with sandbox-scoped provider attachments. Under that model the base
  URL and model are the workload's to set, so the overlay leaves them as
  `${env:VSS_HERMES_BASE_URL}` and `${env:VSS_HERMES_MODEL}` for step 5.

The model `api_key` becomes `${env:COMPATIBLE_API_KEY}` and stays a
placeholder: the proxy strips whatever bearer the agent sends and injects the
provider's real key, so only its presence matters.

```bash
docker build -t vss-harness-hermes:$VSS_REF .hermes
docker build -f .openshell/Dockerfile.policy \
  --build-arg HARNESS_IMAGE=vss-harness-hermes:$VSS_REF \
  -t vss-harness-hermes:$VSS_REF-policy .openshell
```

Precedence is `--policy` > `OPENSHELL_SANDBOX_POLICY` > image policy > built-in
default. Keep the runtime-discovered parts — `allowed_ips` from
`orchestrator_mcp_helper.sandbox_host_cidrs`, the `.invalid` Kubernetes ingress
placeholder — on `--policy` at create, or accept the documented fallback CIDRs
for Compose.

**4 — Register the inference provider** (replaces `NEMOCLAW_PROVIDER`/`_MODEL`/
`COMPATIBLE_API_KEY` at onboard):

```yaml
# .openshell/vss-inference-profile.yaml
id: vss-inference
display_name: VSS agent inference
category: inference
inference_capable: true
credentials:
  - name: api_key
    env_vars: [COMPATIBLE_API_KEY]
    required: true
    auth_style: bearer
    header_name: authorization
endpoints:
  - host: inference-api.nvidia.com
    port: 443
    protocol: rest
    enforcement: enforce
    rules:
      - allow: { method: POST, path: "/v1/**" }
binaries:
  - { path: /usr/bin/python3.13 }     # /usr/local/bin/node, for OpenClaw
```

```bash
openshell profile lint   -f .openshell/vss-inference-profile.yaml
openshell profile import -f .openshell/vss-inference-profile.yaml
COMPATIBLE_API_KEY=... openshell provider create \
  --name vss-inference --type vss-inference --credential COMPATIBLE_API_KEY
```

`--credential KEY` reads the value from the environment, which is the form a
custom profile needs: `--from-existing` has no stored credentials to read. The
CLI has to be the gateway's own release, since the two speak Protobuf.

The profile is per endpoint family, because it is what authorizes the host.
One file each covers the notebook's three choices, and step 5 names the one it
imported:

| `deploy_nemoclaw.ipynb` | Profile | Credential |
|---|---|---|
| (a) OpenAI-compatible endpoint | `vss-inference-profile.yaml` | `COMPATIBLE_API_KEY` |
| (b) model served on this host | `vss-inference-local-profile.yaml` | optional |
| (c) build.nvidia.com | `vss-inference-build-profile.yaml` | `NVIDIA_API_KEY` |

A server on a port (b) does not declare is unreachable until the profile
declares it, which is the one thing `NEMOCLAW_ENDPOINT_URL` did not have to
care about. Within a family the model id is free, and step 5 sets it.

**5 — Create the sandbox and expose the harness API:**

```bash
openshell sandbox create \
  --name vss-harness-hermes \
  --from vss-harness-hermes:$VSS_REF-policy \
  --policy ./policy-hermes.yaml \
  --provider vss-inference \
  --env VSS_PUBLIC_URL="$VSS_PUBLIC_URL" \
  --env VSS_HERMES_BASE_URL=https://inference-api.nvidia.com/v1 \
  --env VSS_HERMES_MODEL=aws/anthropic/bedrock-claude-opus-5 \
  --restart-policy on-failure \
  --expose 18642 --expose-authorization-mode bearer-passthrough --detach \
  -- sh -c 'chmod 770 /sandbox;
            C=/sandbox/.hermes/config.yaml
            sed -i -e "s#\${env:VSS_HERMES_BASE_URL}#$VSS_HERMES_BASE_URL#g" \
                   -e "s#\${env:VSS_HERMES_MODEL}#$VSS_HERMES_MODEL#g" "$C"
            E=/sandbox/.hermes/.env
            grep -q "^API_SERVER_KEY=" "$E" || {
              K=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d " \n")
              printf "API_SERVER_KEY=%s\n" "$K" >> "$E"; }
            chmod 640 "$E"
            exec hermes gateway run'

openshell sandbox exec -n vss-harness-hermes -- vss configure --base-url "$VSS_PUBLIC_URL"
openshell sandbox exec -n vss-harness-hermes -- vss configure check
openshell sandbox exec -n vss-harness-hermes -- vss-hermes-sync
openshell policy get vss-harness-hermes --full         # verify effective policy
```

To verify, run the CLI agent, then the API server the UI uses:

```bash
openshell sandbox exec -n vss-harness-hermes -- \
  hermes chat -Q -q 'Reply with the single word: pong'

KEY=$(openshell sandbox exec -n vss-harness-hermes --no-tty -- \
  sh -c '. /sandbox/.hermes/.env; printf %s "$API_SERVER_KEY"')
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  http://default--vss-harness-hermes.openshell.localhost:8080/v1/chat/completions \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"List the VIOS sensors."}]}'
```

`sandbox connect` attaches to the main process's output, not a shell — detach
with Ctrl-P Ctrl-Q, since Ctrl-C reaches `hermes gateway run`. For a shell, use
`openshell sandbox exec -n vss-harness-hermes --tty -- bash`. A `Connection
error` from the agent means the proxy denied the call; the supervisor
container's log names the host and reason.

**The two `--env` values are the model choice, and the `sed` is what makes
them stick.** They replace `NEMOCLAW_ENDPOINT_URL`/`NEMOCLAW_MODEL`, so a
redeploy against a different model is a flag rather than a rebuild. Rendering
them into the file is not redundant: `hermes chat` expands a `${env:}` ref, but
the API server the UI talks to reads the file as written and sends the
placeholder as the model id, which the endpoint rejects with `HTTP 403`. The
model `api_key` ref needs no rendering — the proxy replaces the bearer either
way.

**The trailing command is the harness itself, not a start script.** OpenShell
treats it as the sandbox's canonical main process: it streams the output and
returns the exit status, so a dead agent surfaces as `Error` with
`MainProcessFailed` instead of a healthy-looking container. NemoClaw instead
runs `nemoclaw-start`, and VSS's images carry `CMD ["sleep", "infinity"]` so an
exec-driven sandbox stays alive — which means today nothing watches the agent.
Taking OpenShell's contract costs the work `nemoclaw-start` does on the way up;
see step 6.

**The command carries the two preconditions the image's own launcher would have
met.** Hermes's secret-boundary guard requires `/sandbox` to be group-readable,
which OpenShell does not leave it, and it requires `API_SERVER_KEY` in
`/sandbox/.hermes/.env` at mode 640 rather than in the environment. Both are
the sandbox's to satisfy under Option 2, because `nemoclaw-start` — which does
this work in the image — requires a root PID 1 that the gateway's policy
validation refuses.

`--expose` publishes one of the sandbox's loopback ports at a URL the gateway
manages, so the path becomes client → gateway listener → supervisor → sandbox
with no host-side forward process in it. The URL is
`http://<workspace>--<sandbox>.openshell.localhost:<gateway port>/` — the
gateway's **own** listener, routed by `Host`, so the exposed port is a target
rather than an address and `--expose 18642` publishes nothing on 18642.
`openshell.localhost` is the built-in default domain, so reaching it needs no
certificate, SAN or DNS work; `--server-san` is what admits a different domain,
and is the knob the Brev check below turns. Measured against gateway 0.1.5,
and `bearer-passthrough` re-confirmed on 0.1.6: WebSocket upgrades and plain
HTTP both survive the route, and `bearer-passthrough` delivers the application
`Authorization` header intact where the default `strip` removes it — a bearer
`GET /v1/models` through the route returns Hermes's own `hermes-agent` model. Who can *reach* the route is set by the
bind in step 1, not by the route — the loopback bind is what refuses a
bridge-networked client, so putting the gateway on a network the UI can reach
is a decision about who may create sandboxes, not a data-path constraint.

**Expose `:18642`, the API server, not `:18789`, the dashboard.** The API
server is what the UI adapter talks to and where Hermes serves `/health`. The
port is `18642`, not the documented `8642`: measured in the harness image, the
API server binds the sandbox's own loopback at `API_SERVER_PORT` from
`/sandbox/.hermes/.env`, and `gateway.api_server.port` in `config.yaml` does not
move it. `8642` and `18789` are not Hermes listeners at all — `nemoclaw-start`
runs socat forwarders that republish them from private loopback ports, because
NemoClaw's forward arrives from outside the network namespace. OpenShell
exposes a loopback port from the inside, so `--expose 18642` reaches the API
server directly and the socat pair goes away with the script.

The dashboard does not survive the route as shipped: Hermes refuses a WebSocket
upgrade whose `Host` or `Origin` does not match the address it bound, and the
gateway rewrites `Host` while preserving `Origin`, so the page loads and its
sockets close. Setting Hermes' `dashboard.public_url` to the routed origin
clears that, so routing the dashboard is a config decision rather than a dead
end; until then operators reach it with `openshell forward start 18789`, where
the browser origin is loopback.

That one forward is all that survives of three host-side processes.
`dashboard-relay.py` exists only to reach a forward pinned to loopback and
`dashboard-forward-watchdog.py` only to repair one that holds its port while
carrying no HTTP — neither has a job once the UI's path is a URL and the
remaining forward is an operator starting it by hand.

`--restart-policy` defaults to `no`, which leaves an exited Hermes dead and the
route silently unanswered; `on-failure` has the driver replace the runtime after
a nonzero main-process exit.

**Compose cannot declare the sandbox, so this step needs a script on both
sides.** `gateway.toml` has no sandbox section, and the containers the driver
creates are siblings Compose never sees — `compose down` leaves them running.
Wrap the commands above in one rerunnable script that skips only the `create`
when the sandbox already exists, so an interrupted first run is finished by the
next one, and pair it with a second that calls `openshell sandbox delete`
alongside `compose down`.

Still to confirm: exposed services use the gateway's own listener and TLS
configuration, so the managed URL has to be reachable through the Brev secure
link.

**The UI keeps its existing agent adapter.** The embedded adapter under
`services/ui/apps/nv-metropolis-bp-vss-ui/utils/server/agentAdapter/` already
connects browser chat to an external harness from inside the UI's Next.js
process — a Responses connector for Hermes, an OpenClaw native-WebSocket one,
session mapping, SSE replay, cancellation and snapshot-artifact validation. It
reaches the harness today at
`ws://host.docker.internal:<relay-port>`, so Option 2 repoints
`AGENT_BACKEND_URL` at the gateway-managed URL; the Compose plumbing
(`VSS_AGENT_ADAPTER_ENABLED`, `VSS_AGENT_BACKEND_URL`) is already in place.
Two constraints come with it. The UI has to reach the gateway's listener, and
under step 1's loopback bind that means `vss-ui` moves to `network_mode: host`
and its Compose-DNS `VSS_PROXY_BASE_URL` becomes
`http://127.0.0.1:${HAPROXY_PORT}`; the alternative is to put the gateway and
`vss-ui` alone on a dedicated Compose network and bind the gateway there, which
keeps both on Compose DNS at the cost of giving one more container the right to
create sandboxes. And Hermes is served by the `responses` connector — already
the default `VSS_AGENT_BACKEND_PROTOCOL` in every profile — so
`AGENT_BACKEND_PATH` keeps its `/v1/responses` default and
`AGENT_BACKEND_TOKEN` carries the `API_SERVER_KEY`. Custom headers are a limit
of the OpenClaw connector alone, so Hermes' `X-Hermes-Session-Key` stays
configuration rather than a code change.

Measured against the harness image, the connector needs no changes: the wire
format it already sends is the stateful Responses semantics the API server
implements, `/health` answers unauthenticated, `/v1/responses` rejects a
missing or wrong bearer with `401 gateway_auth_failed`, and the minted key
returns the SSE event names the connector parses.

**6 — Replace what `nemoclaw-start` did on the way up.** This is the one
genuinely new piece of work, and most of it moves to build time rather than
being reimplemented. The script's own steps, and where each lands:

| `nemoclaw-start` did | Under Option 2 |
|---|---|
| Write the harness config (`/sandbox/.hermes`, yaml) | Baked into the image — it is deterministic, and a build-time file needs no post-create restart |
| Point the harness at managed inference | `--provider` (step 4); the agent sees a placeholder and the supervisor adds the key at egress |
| Issue the dashboard token / auto-approve pairing | Nothing to do: the image mints `API_SERVER_KEY` itself and refuses to start if one is passed in (below); the device-pairing helper is OpenClaw-only |
| socat-forward `:8642` and `:18789` outward | Dropped — `--expose` reaches the loopback ports from inside the namespace |
| Seed the workspace, fix ownership under the config dir | Image build, under the same `USER sandbox` the sandbox runs as |
| Clear raw inference keys, tighten rlimits, `setpriv` | Dropped — OpenShell owns this boundary and `no-new-privileges` blocks the `setpriv` step anyway (§1) |
| `wait` on the gateway process | OpenShell's canonical main process does this |

What remains is a config file in the image, not a launcher. The key never
leaves the sandbox: step 5's command mints one into `/sandbox/.hermes/.env`,
and `hermes gateway run` enrols the API server only when it finds a usable one
there. Passing one with `--env` is worse than redundant — the image aborts
with `[SECURITY] Refusing Hermes startup because the process environment
contains raw secret-shaped values`, naming `API_SERVER_KEY`. So the UI's token
is read out of the sandbox with `sandbox exec` after create, the same shape as
OpenClaw's gateway token.

No launcher is needed for the environment either: the base image's passwd entry
is `sandbox:x:998:999::/sandbox:/bin/bash`, so `HOME` resolves to `/sandbox` and
`HERMES_HOME` to `/sandbox/.hermes`, the path the image already assumes. If one
does prove necessary, it should end with `exec hermes gateway run`, so the exit
status still reaches OpenShell. One image value does need changing:
`HERMES_ENVIRONMENT_HINT` tells the agent its "inference [is] routed through
NemoClaw", which under Option 2 it is not, and the hint is what the model reads.

**7 — Or drive it from Python**, which fits the notebooks and the orchestrator
MCP helper better than shelling out to two CLIs. OpenShell publishes SDKs for
Python, TypeScript, Go and Rust against the same gRPC control plane the CLI
uses, and asks only that the SDK and gateway come from one release:

```python
from openshell import SandboxClient

with SandboxClient.from_active_cluster() as client:
    sb = client.create(workspace="default", name="vss-harness-hermes")
    client.wait_ready(sb.name, workspace="default", timeout_seconds=300)
    print(client.exec(sb.name, ["vss", "configure", "check"],
                      workspace="default").stdout)
```

### What VSS does not carry over from NemoClaw

Not every step in
[`deploy/docker/scripts/nemoclaw/README.md`](../deploy/docker/scripts/nemoclaw/README.md)
has an Option 2 equivalent. These drop out rather than move:

- **The installer and `onboard`.** Both exist to produce a gateway and a
  sandbox; step 1 and step 5 do that directly.
- **`policy-add` after create.** OpenShell takes the policy at create, so the
  window where a sandbox is up without its boundary never opens.
- **`dashboard-relay.py` and `dashboard-forward-watchdog.py`.** Both exist to
  get past a forward pinned to loopback. `--expose` gives a gateway-managed URL
  instead, so the UI's path carries no forward process; `openshell forward`
  survives as the operator's way to open the dashboard, which is not routed.
  `gateway-token` does **not** drop out with them: the UI adapter still needs a
  bearer — Hermes' minted `API_SERVER_KEY`, OpenClaw's shared gateway token —
  so only its source moves, from `nemoclaw <sandbox> gateway-token` to a
  `sandbox exec`.
- **`config set --restart`.** Config is in the image; nothing mutates a live
  sandbox.
- **`CHAT_UI_URL` at onboard.** NemoClaw bakes the dashboard origin because
  `gateway.*` cannot be edited afterwards. The UI's own data path needs no
  pre-authorised origin, since it is the `responses` API rather than the
  dashboard. Routing the dashboard itself does need one — Hermes's
  `dashboard.public_url` — but that is a later choice, not a create-time input.
- **The ENV.md upload.** `--env` at create carries `VSS_PUBLIC_URL`; the
  Kubernetes-only rendered copy becomes a separate `sandbox upload` call —
  `create --upload` cannot be combined with the trailing command step 5 needs.
- **`mcp add`.** Already skipped today on the HTTP path; the policy's `:9988`
  grant is what the agent needs.

### Adding a second harness later

The work above is per-harness, so OpenClaw returns as a parallel set of the
same artifacts — no new code path, because OpenShell has no harness concept to
teach. Concretely: build `.openclaw/` to a second image, write
`policy-openclaw.yaml` (same shared blocks, but `binaries` naming
`/usr/local/bin/openclaw` and `/usr/local/bin/node`, and `clawhub.ai` and
`openclaw.ai` in place of `nousresearch.com`), bake the OpenClaw config into
that image, and create with `-- openclaw gateway run --port 18789`.

Only two of the rows in [What each manifest declares](#what-each-manifest-declares)
change anything structural. OpenClaw authenticates its dashboard by device
pairing rather than a bearer key, so the auto-approval that `nemoclaw-start`
performed has to be replaced or the pairing accepted by hand. And its
`inference.provider_type` is `gateway_managed` rather than a `base_url` key in
its own config, so the step 4 provider attaches differently. Everything else —
port, health path, config dir and format, package binary — is data, which is
why it belongs in one table rather than in a `case` statement.

### Architecture

![Option 2 - OpenShell direct](assets/harness-option2-openshell-direct.svg)

The gateway becomes a Compose service behind the `harness` profile; the policy
joins the image; the relay and watchdog scripts are replaced by
`service expose`.

#### How OpenShell and VSS fit together

Drawn for the 0.1.x target, not the deployed 0.0.116.

![VSS on OpenShell](assets/vss-openshell-architecture.svg)

- **Gateway** is the control plane, a Compose service behind the `harness`
  profile. It owns sandbox lifecycle and state,
  signs the only credentials in the system, delivers policy and provider
  credentials, and runs the policy prover. Its **compute driver** provisions
  each sandbox as two containers and builds the isolation boundary.
- **Workload** runs the harness image (OpenClaw, VSS plugin, skills, `vss` CLI,
  baked policy) with networking turned off. Its only path out is the
  supervisor; if the supervisor exits, the agent is stopped rather than running
  unchecked.
- **Supervisor** is the trusted half and the only decision point. It checks every
  connection against the policy, then opens it: to HAProxy `:7777` and the
  orchestrator MCP `:9988` for VSS, and to external inference and chat services
  with the real credential added at egress. The agent never holds a key.
- **Gateway link** delivers policy and credentials to the supervisor and
  carries logs, exec sessions and the exposed harness API, whose `:18642` is the
  loopback target inside the sandbox; the URL itself is served on the gateway's
  own port under a per-sandbox hostname.

A chat turn from the VSS UI to Hermes. The gateway hops were measured on 0.1.5
with a probe server; the Hermes end was measured against the harness image.

![Request path from the VSS UI to the harness](assets/vss-openshell-request-path.svg)

**Deployed from the host.** VSS and the harness are both deployed from the
host — by notebooks, CI, or the `vss-build-vision-ai` coding agent. Each
deploys VSS first, then installs the harness against the running build. The
agent inside the sandbox drives VSS but never deploys or tears it down.

Drawn for the Compose deployment, where the gateway, the sandbox and the VSS
services share one Docker host. On Kubernetes the shape is the same with the
Kubernetes compute driver: the supervisor runs in its own pod, reaches the
workload over mutual TLS on a private Service, and a NetworkPolicy replaces
Docker's networking-off as the outer fence.

### Pros / cons

**Pros**

- **The gateway is already OpenShell's, and now VSS declares it.** The unit on
  the host today is an OpenShell gateway that NemoClaw merely installed. Moving
  it into Compose from the published image follows an upstream recipe, and
  replaces a systemd unit nobody owns with a service in the repository.
- **Smaller change than Option 1, because VSS already drives OpenShell.**
  `deploy_nemoclaw.ipynb` and the canonical flow already call `sandbox exec`,
  `sandbox get` and `forward start/list/stop`; `.openclaw/Dockerfile` builds an
  OpenShell sandbox image; `assets/vss_nemoclaw_policy.yaml` is an OpenShell
  policy. Option 2 extends what the repo does. Option 1 replaces the image build,
  the policy form and the control flow at once.
- **A parallel path to V1, not a fork away from it.** Both futures need the same
  0.1.x-compatible policy and the same end to `policy-add`, `config set`,
  `mcp add` and `upload`. Doing that work now leaves V1 one
  `deployment.yaml` away instead of undoing anything.
- **The UI's host-side relay and watchdog collapse to one gateway URL, verified
  to carry the adapter's WebSocket.** Both exist for a NemoClaw constraint, not
  an OpenShell one — `nemoclaw recover` retires a non-loopback forward as stale
  and re-creates the loopback one, then judges the replacement by a TCP connect
  that a wedged forward still accepts. `--expose` removes the forward and both
  scripts outright. The Hermes dashboard is the exception: it stays behind an
  operator-side `forward` until `dashboard.public_url` is set (step 5).
- **The harness set stays VSS's decision.** OpenShell takes any image at
  `sandbox create --from`, so `.openclaw/` and `.hermes/` are build choices, not
  entries on someone else's supported list.
- **The separate policy step disappears, and what replaces it is a real
  surface.** The policy is baked in the image or passed as one `--policy` flag,
  so there is no preset-merge semantics and no post-create window where the
  sandbox is up without its boundary. Beyond create there is `policy update`
  for precise merges, `policy get --base/--full`, revision rollback, a global
  policy, and the advisor for agent-proposed rules.
- **The conversion is verifiable.** `openshell-prover check` proves the new
  policy grants no more than a boundary you define — the only machine-checkable
  answer to "did 470 lines of translation widen access?"
- **Python SDK matches the repo.** `uv add openshell`, `create`, `wait_ready`,
  `exec`, `create_from_template`, service exposure, labels. Go, Rust and
  TypeScript also available.
- **A blocked request is debuggable first-hand**, from the enforcing layer
  rather than second-hand through NemoClaw's aggregation: `action=deny` log
  lines naming the host, port and calling binary, the effective policy from
  `policy get --full`, and OCSF export for SIEM.
- **One version to track**, instead of `BASE_IMAGE`, `OPENCLAW_VERSION` and the
  plugin lockfile having to move together.
- **Released and supported.** v0.1.6, a support matrix, an upgrade guide, a
  weekly release cadence, and it ships `aarch64` builds — this note's gateway
  and Hermes sandbox were brought up on a DGX Spark, the platform Option 1
  cannot target at all.

**Cons**

- **Image hardening stays with a NemoClaw artifact.** Of the two additions that
  are real (§1), the toolchain purge sits in the published base image that
  `.openclaw/Dockerfile` pins by digest, not in the CLI — so Option 2 keeps it
  by keeping `BASE_IMAGE`, still tracking a NemoClaw release for it. Only a
  rebuild on OpenShell's community image makes the purge VSS's own work.
- **The build-arg denylist becomes VSS's job.** Narrow today — `onboard-args`
  forwards only model and UI values — so this guards a future build arg rather
  than a current leak.
- **No state-preserving rebuild.** A gap against today's v0, not a regression from
  migrating — and cheap to close, since the workspace is a directory:
  `sandbox download` before replace, `upload` after.
- **Policy format conversion is still required** — shared with Option 1, but here
  the prover can check it.
- **Loses blueprint digest verification** and NemoClaw's aggregated
  `logs`/`status`/host-readiness reporting.
- **Two harness images** (`.openclaw/`, `.hermes/`) still need the chosen image
  strategy. Unchanged from today.
- **An invalid baked policy stops the sandbox** with no fallback to the default
  policy. Fail-closed is correct, but lint the policy in CI alongside the image.
- **The Compose gateway carries its own constraints.** A Docker-socket mount in
  a VSS-owned file, a loopback bind that the UI service has to be placed to
  reach, and bind flags that must be named on the command because the image
  `CMD` overrides the TOML.

---

## 4. Supporting multiple harnesses

### Three lists, three layers

"Which harnesses are supported" has three different answers, because the three
layers model a harness differently. Conflating them overstates what either
option buys.

| Layer | What "supported" means | The list |
|---|---|---|
| [OpenShell](https://docs.nvidia.com/openshell/about/supported-agents) | Pre-installed in the base image, and whether the default policy already names that agent's endpoints and binary paths | Claude Code (full coverage), GitHub Copilot CLI (full), OpenCode (partial), Codex (none); Ollama and Pi as bundled images. OpenClaw and Hermes are listed only as "Blueprint-managed", deferred to NemoClaw |
| [NeMo Fabric](https://docs.nvidia.com/nemo/fabric/integrations/harness-integrations/overview) | An adapter translating a normalized config, lifecycle, result and telemetry contract into harness-native form | Claude Code, Codex, Cline, Factory Droid, Hermes, LangChain Deep Agents, Kilo Code, mini-SWE-agent, NOOA, OpenClaw, OpenHands, OpenCode, Pi, Qwen Code, Remote Agent |
| [NemoClaw](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/platform-support) | A validated lifecycle: onboarding, policy, dashboard, state transfer | OpenClaw (default), Hermes, LangChain Deep Agents Code — plus Pi, a release candidate in [`agents/`](https://github.com/NVIDIA/NemoClaw/tree/main/agents) on `main` that is not selectable by default |

**OpenShell has no harness abstraction.** Its unit is an image plus a policy:
`sandbox create --from` takes any standard Linux image, replaces its
`ENTRYPOINT` with the supervisor, and runs the command passed after `--`.
Isolation is agent-agnostic; only the endpoints and binaries named in the
policy differ. So the list above is documentation convenience, not a
capability boundary — anything that runs in a container runs, at the cost of
one image and one policy per harness.

Treat the bundled-image rows as stale. [OpenShell-Community is
retired](https://github.com/NVIDIA/OpenShell-Community) and states that bare
names such as `base`, `ollama` and `pi` are no longer expanded by `--from`,
while the Supported Agents and Manage Sandboxes pages still describe the old
behaviour.

**NemoClaw is the narrowest of the three and intends to stay there.** Its
platform-support page lists "Other LangChain, AutoGen, CrewAI, or non-listed
agent harnesses" as Unsupported, with new runtimes tracked as a research epic
"not on the current roadmap" (issue #4861).

### How NemoClaw adds a harness (v0; V1 replaces this with Fabric adapters)

![How NemoClaw selects and launches a harness](assets/harness-selection-nemoclaw.svg)

Selection is directory scanning, not a fixed list: every directory under
[`agents/`](https://github.com/NVIDIA/NemoClaw/tree/main/agents) holding a
`manifest.yaml` counts, with candidates hidden unless
`NEMOCLAW_CANDIDATE_AGENTS=1` and a qualification receipt are set — which is
why `main` carries Pi while the platform-support page documents three.

Four artifacts in the agent directory supply the four things OpenShell is
given. Nothing else about the harness reaches it:

| Artifact | Becomes | OpenShell surface |
|---|---|---|
| `Dockerfile`, `Dockerfile.base` | the harness image — `npm`, `pip` or `curl` install | `sandbox create --from` |
| `policy-additions.yaml` | the baseline policy, merged with the selected presets | `--policy` |
| `start.sh` | `startupCommand` on `CreateOpenShellSandboxRequest` | the command after `--` |
| `manifest.yaml` | ports, health probe, config paths, inference keys | `--forward`, provider attach, post-create config writes |

The policy binding is enforced rather than inherited:
`requireAgentPolicyAdditionsPath()` fails a non-OpenClaw agent that ships no
readable `policy-additions.yaml`, "refusing to substitute the OpenClaw
baseline".

This is the concrete form of the per-harness cost that the dimensions table
below prices: an image, a policy, an entrypoint and a manifest per harness,
none of which OpenShell can supply, because the sandbox it creates carries no
harness identity at all. A `ps -ef` inside the deployed VSS sandbox
(`sandbox-ps.txt` in `nemoclaw-debug.tar.gz`) shows `openshell-sandbox` on
PID 1 with the image's `ENTRYPOINT` discarded, NemoClaw's start script as its
child, and `openclaw-gateway` below that (log `tee`/`tail` helpers omitted):

```text
UID          PID    PPID  C STIME TTY          TIME CMD
root           1       0  0 16:54 ?        00:00:10 /opt/openshell/bin/openshell-sandbox --workdir /sandbox
sandbox       97       1  0 16:54 ?        00:00:00 bash /usr/local/bin/nemoclaw-start
sandbox      560      97  1 16:54 ?        00:01:59 openclaw-gateway
```

#### What `start.sh` does

`start.sh` turns a generic image into a running, correctly configured harness,
then stays alive as that harness's supervisor. For OpenClaw it is
[`scripts/nemoclaw-start.sh`](https://github.com/NVIDIA/NemoClaw/blob/main/scripts/nemoclaw-start.sh),
installed as `/usr/local/bin/nemoclaw-start`; §3 step 6 decomposes what it does
and where each piece lands under Option 2. Hermes and Pi ship their own with the
same shape, sharing `scripts/lib/sandbox-init.sh` for the security steps.

The OpenClaw script runs to about 5,600 lines, most of it migration, permission
and pairing logic — which is where NemoClaw's knowledge of a harness actually
lives. VSS does not own it: `.openclaw/Dockerfile` inherits it from
`BASE_IMAGE`, so under Option 2 it either keeps that base image or takes on an
equivalent launcher.

### What each manifest declares

The manifest is the whole integration contract — the reason another harness is
a new directory rather than a new code path. The fields that actually differ:

| Field | `openclaw` | `hermes` | `pi` | `langchain-deepagents-code` |
|---|---|---|---|---|
| `language` | nodejs | python | nodejs | python |
| `install_method` | `npm` | `curl` | `npm` | `pip` |
| `binary_path` | `/usr/local/bin/openclaw` | `/usr/local/bin/hermes` | `/usr/local/bin/pi` | `/usr/local/bin/dcode` |
| `runtime.kind` | gateway (default) | gateway (default) | `terminal` | `terminal` |
| `gateway_command` | `openclaw gateway run` | `hermes gateway run` | — | — |
| `runtime.interactive_command` | `openclaw tui` | `hermes` | `pi` | `dcode` |
| `health_probe` | `localhost:18789/`, 30 s | `localhost:8642/health`, 90 s | — | — |
| `forward_ports` | `18789` | `18789`, `8642` | — | — |
| `config.dir` | `/sandbox/.openclaw` | `/sandbox/.hermes` | `/sandbox/.pi/agent` | `/sandbox/.deepagents` |
| `config.format` | json | yaml | json | toml |
| `device_pairing` / web auth | `true`, device pairing | `false`, bearer `API_SERVER_KEY` | `false` | `false` |
| `inference.provider_type` | `gateway_managed` | `custom` | `openai_compatible` | `openai_compatible` |
| `inference.base_url_config_key` | — (providers block) | `model.base_url` | `providers.openshell.baseUrl` | `models.providers.openai.base_url` |
| `inference.default_model` | — | — | `nemotron-3-super-120b-a12b` | `nemotron-3-ultra-550b-a55b` |
| `mcp.support` | `bridge`, `openclaw-config` | `bridge`, `hermes-config` | `disabled` | `bridge`, `deepagents-config` |
| `package_registry.binary` | `/usr/local/bin/npm` | `/usr/local/bin/pip3` | `/usr/local/bin/npm` | `/opt/venv/bin/pip3` |
| `phone_home_hosts` | openclaw.ai, clawhub.ai | nousresearch.com | — | — |

The `inference` rows are how four harnesses reach one managed route without
NemoClaw knowing any of their config formats: each manifest names the key where
its own base URL lives, and everything resolves to `https://inference.local`.

Harness identity also changes the security posture, not just the plumbing. Pi's
`policy-additions.yaml` fails Landlock closed where the deployed OpenClaw
sandbox runs `best_effort`, and its `binaries` list names the Node interpreter
rather than the package, the same rule §3 step 2 hits with Hermes and Python.
Those binary paths are the only sense in which OpenShell is harness-aware.

### What Fabric gives VSS's two harnesses specifically

The 15-row table above is the wrong number to plan against. Only `.openclaw/`
and `.hermes/` matter here, and they sit at opposite ends of Fabric's maturity:

- **OpenClaw is a shim, not an integration.**
  [`nemo-fabric-adapters-openclaw`](https://pypi.org/project/nemo-fabric-adapters-openclaw/)
  "starts an isolated OpenClaw Gateway and invokes its OpenAI-compatible Chat
  Completions endpoint", supports only the default `openclaw` runtime, does not
  install OpenClaw, and does not support Relay telemetry. It is absent from the
  [configuration-compatibility matrix](https://github.com/NVIDIA/NeMo-Fabric/blob/main/adapters/README.md)
  entirely, which covers only Claude, Codex, Deep Agents, Hermes and
  mini-SWE-agent. Driving OpenClaw as a chat-completions endpoint is not how
  VSS uses it — the VSS plugin, skills tree and `vss` CLI live in the gateway
  and dashboard the adapter bypasses. OpenClaw's own docs also class that
  endpoint as full operator access, to be kept on loopback or private ingress.
- **Hermes is the better-integrated harness, with a version trap.** Models,
  workspace, skills, MCP, instructions, turn limits and Relay telemetry are all
  normalized, and `tools.enabled`/`tools.blocked` map to Hermes-native toolset
  selectors — a policy the adapter cannot enforce fails planning rather than
  being silently ignored, which is the right failure. But the [adapter
  guide](https://docs.nvidia.com/nemo/fabric/integrations/harness-integrations/hermes-agent)
  requires `nemo-relay>=0.9,<0.11` while the published Hermes `v2026.9.24` ships
  Relay 0.8, so telemetry needs a Hermes checkout rather than a release, and an
  incompatible pairing fails at startup. Hermes Agent 0.20+ is no longer
  installable from PyPI at all.

So the telemetry that makes a multi-harness CI leg worth running is exactly
what is missing on both: unsupported on OpenClaw, and gated behind an
unreleased Hermes build.

### Pros / cons, by dimension

| Dimension | OpenShell direct | Fabric / NemoClaw |
|---|---|---|
| **Configuration simplicity** | One Dockerfile and one policy per harness, with no sharing between them — a second harness largely duplicates `assets/vss_nemoclaw_policy.yaml`'s ~470 lines | One `FabricConfig` declares models, tools, MCP and skills once; the adapter translates per harness. But Fabric normalizes *agent* config only, so the per-harness policy duplication remains either way |
| **Integration behaviour** | Uniform isolation, deliberately non-uniform above it: each harness keeps its native config, auth and skills notion | Normalized result, error, artifact and lifecycle contract, which is what makes outcomes comparable. Coverage is uneven and documented as such; see the OpenClaw gap above |
| **Developer experience** | Debugging is first-hand — `action=deny` log lines naming host, port and calling binary; `policy get --full` for the effective policy; `openshell-prover check` to prove a conversion. Adding a harness is an image and a policy | `plan()` and `doctor()` reject unsupported configurations before execution rather than mid-run. Cost is a third layer where a gap can live in the harness, the adapter or the contract, with the least visibility and no ability to patch |
| **CI usefulness** | Contributes nothing to comparability; it only guarantees every leg runs under the same isolation | The right shape for `skills-eval.yml`, which already hand-rolls this with `coding_harness`/`operational_harness` and three adapters in `.github/skill-eval/agents/`. Blocked in practice: no OpenClaw telemetry, Hermes telemetry needs an unreleased build, and NVIDIA calls the NemoClaw–Fabric integration "still being hardened for release compatibility, conformance, and downstream validation" |
| **Bug / maintenance risk** | One dependency at 0.1.x with a support matrix and upgrade guide; failures are VSS's to fix | Three coupled version lines — OpenShell, Fabric runtime, per-adapter package — with the documented rule to use matching runtime and adapter releases "unless a different pairing has been explicitly validated", plus per-adapter Python floors (Hermes 3.11–3.13 via the `nemo-fabric` extra, NOOA 3.12–3.13). NemoClaw concedes Hermes has an empty model-provider compatibility registry "backfilled after failures", no Hermes unit tests, and that "production parity with OpenClaw is not yet asserted" |

### Bottom line

For multi-harness support the two options are not symmetric, and NemoClaw is
the wrong axis: it supports the fewest harnesses of the three layers and says
it will not add more. The real choice is OpenShell direct — broad, because any
image works, at one image and one policy per harness — against Fabric, which
is genuinely built for harness portability but whose contract is thinnest
precisely on OpenClaw, VSS's default.

They are separable, which is the useful conclusion. Fabric is an in-sandbox
execution layer; adopting it in `.github/skill-eval` to make eval legs
comparable does not move sandbox ownership, and that is the same boundary
NVIDIA describes for the NemoClaw integration. Nothing here changes the
Option 2 verdict below; it argues against treating V1 as the only route to
more than one harness.

---

## 5. Verdict

**Take Option 2 — drive OpenShell directly — and do it incrementally.**

**Option 1 is not qualified on the platform it targets today.** Linux AMD64 has not been
shown to survive the 15-minute bearer renewal an always-on agent depends on
([#11997](https://github.com/NVIDIA/NemoClaw/issues/11997)), and every apply
exits nonzero at Fabric health
([#13031](https://github.com/NVIDIA/NemoClaw/issues/13031)). Add no release, no
support policy, an alpha schema, and no path to a Fabric-labelled VSS image.
NemoClaw's own migration guide says to keep the
existing deployment and evaluate V1 beside it — that is advice not to migrate.

**Leaving NemoClaw costs little.** Gateway bring-up does not need NemoClaw: the
host unit running today is already an OpenShell gateway from a published
binary, so Option 2 redeclares it as a Compose service rather than building
anything new. What NemoClaw uniquely adds is image hardening and credential
filtering — both bounded, one-time ports into a Dockerfile VSS already owns.

**Option 2 moves VSS toward Option 1, not away from it.** Both futures need the
same policy work (OpenShell 0.1.x-compatible, `tls: terminate` removed, explicit
`enforcement`, filesystem and process sections) and both need VSS to stop
depending on `policy-add`, `config set`, `mcp add` and `upload`.
Doing that against a released OpenShell that runs on both VSS architectures is
strictly better than
against a pre-release Rust branch. If V1 matures, the remaining delta is
authoring one YAML document plus the Fabric image question — which NVIDIA must
solve for every downstream consumer anyway.

### Sequence

1. **Move the gateway into Compose** — the published image behind a `harness`
   profile — and verify against the existing sandbox before changing anything
   else. Retire the systemd unit only once that is proven.
2. **Convert the policy and prove it** with `openshell-prover check`. Highest-risk
   step, and the only one with a machine-checkable success criterion.
3. **Bake the policy into the image**, keeping the runtime-discovered CIDR and
   ingress values on `--policy` at create.
4. **Replace the remaining `nemoclaw` calls** with `openshell` and Python SDK
   equivalents; evaluate `service expose` as the relay/watchdog replacement.
5. **Port the hardening** and add build-arg credential filtering.
6. **Add a workspace preserve/restore helper** around image replacement, closing
   the `rebuild` gap on VSS's own terms.
7. **Track V1** on [#11997](https://github.com/NVIDIA/NemoClaw/issues/11997),
   [#13031](https://github.com/NVIDIA/NemoClaw/issues/13031) and
   [#12638](https://github.com/NVIDIA/NemoClaw/issues/12638).

### One thing to raise with NVIDIA now

Ask the NemoClaw team how a third party produces a Fabric-labelled agent image
that extends a bundled harness with its own plugin, skills and CLI. It blocks
Option 1 outright, it is the requirement least likely to be solved incidentally,
and VSS is unlikely to be the only downstream consumer of v0's
`--from <Dockerfile>` workflow.
