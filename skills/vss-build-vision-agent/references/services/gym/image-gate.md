<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Image gate

Run this before pulling any `nemo-gym` tag. It reads registry metadata only,
roughly 20 KB across three hops, and never pulls a layer.


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

