---
name: vss-run-gym-eval
description: >-
  Score a running VSS deployment with NVIDIA NeMo Gym, so a VSS eval produces a
  scalar reward in the same contract a training loop consumes. Use this skill
  when a developer or agent wants to evaluate VSS with Gym rather than the
  bespoke in-profile harness ("evaluate this deployment with Gym", "run the Gym
  eval", "compare our eval against Gym"), or wants a side-by-side comparison of
  the two harnesses scoring one identical stack. Composes the eval runner as a
  delta on an existing developer profile; it never adds a profile or a shipped
  service.
license: Apache-2.0
metadata:
  version: "0.1.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint evaluation nemo-gym reward"
---

# Evaluate VSS with NeMo Gym

`vss-run-gym-eval` scores a VSS deployment with **NVIDIA NeMo Gym**, producing a
scalar reward per task instead of a harness-specific report.

> ### ⚠ This is a distinct scorer, not a pass-through of VSS's own eval
>
> The resources server applies **its own reward formula and its own judge**:
> `reward = 0.4 × (the policy called the tool) + 0.6 × quality`, where quality
> comes from a ground-truth grader or an LLM judge against the task rubric.
>
> **A number produced here is not numerically comparable to a score from VSS's
> CI skill-eval.** The two measure different things: this asks *how well a policy
> uses VSS to answer a question*, while the CI eval asks *did this check pass*.
> Compare trends, failure modes and regressions — never absolute values, and never
> claim score equivalence between the two harnesses.
>
> A future scorer that genuinely wraps `generic_judge.py` and forwards its
> `passed / total` unchanged would support that stronger claim. This one does not.

## References

- [`references/delta.md`](references/delta.md) — the eval-runner delta, Foundation
  selection, the image gate, and why the delta must not diverge from its
  Foundation.
- [`references/run.md`](references/run.md) — the two-phase `gym env start` /
  `gym eval run` lifecycle, ops hygiene, and the reward traps that return 0.0
  without erroring.
- [`references/results.md`](references/results.md) — where rollouts land, how to
  read a reward, the sequential comparison protocol, and BLADE.

## The resources server

Scoring is performed by a NeMo Gym **resources server**, `vss_ask_video`, which
implements Gym's `verify()`: it asks the running VSS about a video sensor through
an `ask_vss` tool and grades the answer against the task's rubric with an
independent judge.

**It is not vendored here.** It is a Gym component — it implements Gym's
interface, imports Gym, and runs inside Gym's runtime, which VSS never executes.
Keeping it in the Gym repository means its CI tests it against the API it targets;
an earlier copy maintained outside Gym silently broke when that API changed and
nobody noticed until it was run.

Obtain it from the Gym repository and stage it into a Gym checkout as
`resources_servers/vss_ask_video`, then follow [`references/run.md`](references/run.md).
That document is the operational contract: staging, the two-phase lifecycle, the
judge settings the server requires at startup, and the reward traps worth knowing
before trusting a number.

## Prerequisites

| Requirement | Check |
|---|---|
| A running VSS deployment, or a Foundation profile you can deploy | `docker ps --format '{{.Names}}' \| grep -qx vss-agent` |
| `VSS_APPS_DIR` set to the repo's `deploy/docker` | `test -d "${VSS_APPS_DIR}/developer-profiles"` |
| A **post-#2376** `nemo-gym` image tag | run the image gate in the next section; it exits non-zero on a tag that fails |
| NGC credentials — for pulling the image once a tag passes the gate, not for the gate itself | `test -n "${NGC_CLI_API_KEY}"` |

## ⛔ Image gate — check this before pulling anything

The published `nvcr.io/nvidia/eval-factory/nemo-gym:26.05` records a build date of
2026-06-01 in its config blob (NGC lists a later *push* date; the gate reads the
recorded build, which is the one that matters) and **predates [NVIDIA-NeMo/Gym#2376](https://github.com/NVIDIA-NeMo/Gym/pull/2376)**
(merged 2026-08-11), which removes bundled royalty-bearing codec binaries. Its
layer history still `apt-get install`s ffmpeg, so it carries the libraries
`.github/scripts/check_no_patented_codecs.py` forbids in VSS containers.

**Do not pull or run a `nemo-gym` tag built before #2376.** Verify the tag first.
This reads the manifest list, then the platform manifest, then the **config
blob** — three hops, roughly 20 KB total. It never pulls a layer, so the 13 GB
image never touches the host:

```bash
REPO=nvidia/eval-factory/nemo-gym
TAG="${VSS_GYM_EVAL_TAG:?set the tag explicitly; there is deliberately no default}"
# Anonymous pull-scope token: this repository is publicly readable, and the gate
# only ever reads metadata. No NGC credential is needed to RUN THE GATE -- one is
# needed later to pull the image, once a tag passes.
TOK=$(curl -fsS --connect-timeout 5 --max-time 30 "https://nvcr.io/proxy_auth?scope=repository:${REPO}:pull" | jq -er .token) || { echo "GATE FAIL: could not obtain a registry token"; exit 1; }

# 1. manifest list -> the linux/amd64 manifest
AMD=$(curl -fsS --connect-timeout 5 --max-time 30 -H "Authorization: Bearer $TOK" \
  -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json" \
  "https://nvcr.io/v2/${REPO}/manifests/${TAG}" \
  | jq -r '.manifests[]? | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest' | head -1)
# Fail closed: a single-arch (non-index) manifest yields no .manifests[], so AMD is empty.
[ -n "$AMD" ] || { echo "GATE FAIL: no linux/amd64 manifest for ${TAG} -- do not proceed"; exit 1; }

# 2. that manifest -> its config blob digest
CFG=$(curl -fsS --connect-timeout 5 --max-time 30 -H "Authorization: Bearer $TOK" \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
  "https://nvcr.io/v2/${REPO}/manifests/${AMD}" | jq -r '.config.digest')
[ -n "$CFG" ] || { echo "GATE FAIL: could not resolve the config blob -- do not proceed"; exit 1; }

# 3. the config blob -> build date and layer history
BLOB=$(curl -fsSL --connect-timeout 5 --max-time 60 -H "Authorization: Bearer $TOK" "https://nvcr.io/v2/${REPO}/blobs/${CFG}") \
  || { echo "GATE FAIL: config blob fetch failed -- do not proceed"; exit 1; }

# Fail closed on an unusable blob. Without these checks a malformed or empty
# response yields no history to scan, so the codec count prints 0 and READS AS A
# PASS -- the gate would wave through the very image it exists to stop.
#
# The emptiness test is not redundant: `jq -er` on EMPTY input produces no output
# and exits 0, so an empty blob would sail past the `.created` check below. That
# was verified, not assumed.
[ -n "$BLOB" ] || { echo "GATE FAIL: empty config blob -- do not proceed"; exit 1; }
CREATED=$(echo "$BLOB" | jq -er '.created') \
  || { echo "GATE FAIL: no .created in config blob -- do not proceed"; exit 1; }
[ -n "$CREATED" ] || { echo "GATE FAIL: empty .created -- do not proceed"; exit 1; }
# Require history entries we can actually READ. A non-empty array is not enough:
# an entry such as {} yields no created_by, jq emits nothing, and the codec count
# below becomes 0 -- so an image whose history is entirely uninspectable would be
# accepted exactly like one confirmed clean. Absence of evidence is not evidence.
#
# `arrays` is load-bearing: `.history | length` on an OBJECT returns its key
# count, and `.history[]` iterates that object's values, so a blob carrying
# `"history": {"x": {"created_by": "..."}}` yields TOTAL == READABLE and sails
# through. Reject anything that is not an array before counting.
HIST_TOTAL=$(echo "$BLOB" | jq -er '.history | arrays | length') \
  || { echo "GATE FAIL: .history is absent or not an array -- do not proceed"; exit 1; }
HIST_READABLE=$(echo "$BLOB" | jq -r '[.history[] | select(type=="object") | .created_by | select(type=="string" and length > 0)] | length')
[ "${HIST_TOTAL:-0}" -gt 0 ] \
  || { echo "GATE FAIL: config blob has an empty .history -- do not proceed"; exit 1; }
[ "${HIST_READABLE:-0}" -eq "${HIST_TOTAL}" ] \
  || { echo "GATE FAIL: ${HIST_READABLE}/${HIST_TOTAL} history entries are inspectable -- cannot establish provenance, do not proceed"; exit 1; }

# `|| true` is required, not defensive: grep -c exits 1 when it matches nothing,
# so under `set -e` a CODEC-FREE image -- the one this gate exists to approve --
# would abort the script before it could pass. Use `|| true`, NOT `|| echo 0`:
# grep -c already prints 0 on no match, so echoing another produces "0\n0" and
# breaks the numeric comparison below.
CODEC_LAYERS=$(echo "$BLOB" | jq -r '.history[].created_by // empty' | grep -cE 'ffmpeg|libav|x264|x265' || true)
case "${CODEC_LAYERS}" in (*[!0-9]*|"") echo "GATE FAIL: unreadable codec count (${CODEC_LAYERS})"; exit 1 ;; esac
echo "created: ${CREATED}"
echo "codec-installing layers: ${CODEC_LAYERS}"
# Name the offending layer. "1 codec layer" is a number to argue with; the actual
# `apt-get install ... ffmpeg ...` line is the evidence, and it is what to quote
# when reporting a rejection.
if [ "${CODEC_LAYERS}" -ne 0 ]; then
  echo "$BLOB" | jq -r '.history[].created_by // empty' \
    | grep -E 'ffmpeg|libav|x264|x265' | head -1 | sed 's/^/matched layer: /' || true
fi

# ENFORCE. Printing the two values is not a gate -- a caller checking the exit
# status would read success for a codec-bearing image. Decide here, in the script.
# First acceptable instant is the START OF THE DAY AFTER the fix merged, so a build
# stamped anywhere on the merge day itself -- which may predate the merge commit --
# is not accepted.
FIX_EPOCH=$(date -u -d '2026-08-12' +%s)
CREATED_EPOCH=$(date -u -d "${CREATED}" +%s) \
  || { echo "GATE FAIL: unparseable .created (${CREATED}) -- do not proceed"; exit 1; }

if [ "${CREATED_EPOCH}" -lt "${FIX_EPOCH}" ]; then
  echo "GATE FAIL: build predates NVIDIA-NeMo/Gym#2376 -- do not pull ${TAG}"; exit 1
fi
if [ "${CODEC_LAYERS}" -ne 0 ]; then
  echo "GATE FAIL: ${CODEC_LAYERS} layer(s) install codec packages -- do not pull ${TAG}"; exit 1
fi
echo "GATE PASS: ${TAG} postdates the fix and records no codec install"
```

**Accept the tag only if `created` is after 2026-08-11 AND the codec-layer count
is 0.** Both conditions, not either: a later build date does not by itself prove
the codec removal is in the image.

**Know what this gate does and does not establish.** It reads *recorded build
metadata*. It can show that a layer ran a codec install, and it fails closed on
an empty, malformed or history-less config blob — but it **cannot prove absence**:
libraries inherited from a base image leave no `created_by` entry, and a build
dated after the fix could still have been cut from an older source revision.
Treat a pass as "no evidence of a codec install, and the build postdates the fix",
which is the strongest claim available without pulling and scanning the
filesystem. If certainty is required, run
`.github/scripts/check_no_patented_codecs.py --image <ref>` against a pulled
image on a host where pulling it is acceptable.

If the tag fails the gate, **stop and report it** rather than proceeding. A
codec-bearing image must not be pulled onto a VSS host by a VSS skill.

Run against `26.05` this returns `created: 2026-06-01T12:53:28-07:00` and
`codec-installing layers: 1` — a clear fail, and the reason this gate exists.
The same blob shows `Entrypoint: null` and `Cmd: ["/bin/bash"]`, which is why the
runner needs an explicit command (see `references/delta.md`).

This skill carries the image pin itself. `nemo-gym` is an external evaluation
tool, not a VSS product image: it lives outside the four
`first_party_registry_roots`, which is why it is deliberately absent from
`deploy/docker/container-inventory.json` and `containers.env`.

## Routing

| Request | Route |
|---|---|
| Score a running deployment with Gym | Stage the resources server into a Gym checkout and run the two-phase lifecycle on the host (`references/run.md`). This is the verified path. |
| "Run it as a container" / "add it to the Compose project" | `references/delta.md` describes that packaging, but it has **not been run** and cannot be until a codec-clean image exists. Say so rather than improvising it. |
| Compare VSS's eval against Gym's on one stack | Sequential comparison protocol below. Never run both stacks at once. |
| Evaluate with no deployment running | Deploy the Foundation first via `vss-build-vision-agent` or `vss-deploy-profile`, then return here. |
| "Add Gym to a profile" / "create a gym profile" | **Stop.** This skill never adds a profile or a shipped service. The runner is a delta in `_builds/<name>/`, which is gitignored and never a Compose profile. |
| A tag that predates Gym#2376 is the only one available | Stop at the image gate above and report it. |

## When not to use this skill

| Situation | Use instead |
|---|---|
| You want VSS's own evaluation result, or a `passed / total` figure comparable to CI | The existing skill-eval in `.github/skill-eval/`. This skill's reward is a different quantity — see the warning above. |
| You want to evaluate the CI skill-eval corpus through Gym | Not supported. Those tasks are shell execution in a sandbox; see the scope note in `references/run.md`. |
| You want to add Gym to a profile, or a `gym-eval` service to a compose file | Neither is done. The runner is a runtime delta under `_builds/`, never a profile or a shipped service. |
| No VSS deployment is running | Deploy one first via `vss-build-vision-agent` or `vss-deploy-profile`, then return. |
| The only available image tag fails the gate | Stop and report it. Do not pull it, and do not weaken the gate to proceed. |

## Foundation selection

Default to **`lvs`**. Its `config.yml` registers `lvs_video_understanding`;
`base`'s registers it zero times and offers only `video_understanding`, the
sparser of the two. A comparison run on `base` therefore risks conflating a
harness difference with the weaker sampling, which is the confound the whole
exercise exists to avoid — so prefer a Foundation that registers the denser
variant.

Use a different Foundation only when the user names one, or when the deployment
already running is a different profile — in which case the Foundation **must** be
that profile, because the comparison scores the stack that is actually up.

## Comparison protocol (side-by-side)

The comparison is **two harnesses scoring one identical stack**, so stack
identity is the control variable.

> ### ⚠ Identity is guaranteed for the service set, NOT for resolved values
>
> The delta adds exactly one service key, so by construction it **preserves every
> Foundation service and adds only `gym-eval`** — the two service sets differ by
> that one runner and nothing else. **Resolved values do not.** `dev-profile.sh` writes
> host-specific values — model modes, endpoints, device IDs, host paths — into the
> profile's `generated.env`, and delta resolution reads the checked-in `.env`,
> the checked-in `overrides.env`, and the build's `override.env`, **not
> `generated.env`**. A delta composed against an already-running, host-customized
> deployment can therefore deploy a *different* stack while looking identical.
>
> **So before comparing, do one of these:**
>
> - **Preferred:** carry the running deployment's resolved values into the build's
>   `override.env`. Read them from the Foundation's `generated.env` rather than
>   assuming the checked-in defaults apply.
> - Or deploy the Foundation from checked-in values with no host customization, so
>   `generated.env` adds nothing the delta would miss.
>
> Then verify rather than trust — compare the resolved environment, not just the
> service list:
>
> ```bash
> diff <(docker compose ... -f <foundation> config | grep -E '^\s+[A-Z_]+:' | sort) \
>      <(docker compose ... -f _builds/<name>/compose.yml config | grep -E '^\s+[A-Z_]+:' | sort)
> ```
>
> Differences confined to the `gym-eval` service are expected. Any difference on a
> Foundation service means the two stacks are not the same stack, and the
> comparison is void.

The two runs must also be sequential:

1. Deploy the Foundation. Run VSS's own eval. **Capture and persist the results
   now.**
2. Compose and deploy the eval delta, carrying the resolved values above. Run the
   Gym eval. Capture its results.
3. Compare.

**Step 1's capture is not optional.** Every developer profile resolves to
`COMPOSE_PROJECT_NAME=vss` and the same host ports, and `dev-profile.sh` runs
`state_down` before every `state_up` — which tears down the previous deployment
and its data directory. Results not persisted before the switch are gone.

Running both stacks concurrently is not supported and would not be a better
experiment: they would contend for the same GPU, so the measurement would reflect
contention rather than harness behaviour.

## What this skill does not do

- **It does not add a profile, a service, or any file under `deploy/docker/`.**
  The only writable location is `_builds/<name>/`.
- It does not modify VSS's verifiers. Reward comes from the existing judge.
- It does not evaluate the CI skill-eval corpus. Those tasks are shell execution
  in a sandbox and require Gym's `harbor_agent`, which currently pins a January
  2026 Harbor commit incompatible with VSS's `harbor==0.20.0`. Tracked upstream
  in [NVIDIA-NeMo/Gym#2596](https://github.com/NVIDIA-NeMo/Gym/pull/2596).
