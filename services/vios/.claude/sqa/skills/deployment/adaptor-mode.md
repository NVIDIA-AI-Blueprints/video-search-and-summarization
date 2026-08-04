# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Skill: VIOS Adaptor Mode Selection

VIOS / VST supports multiple **adaptors** that govern how sensors are discovered, controlled, and how the RTSP/video stream is fetched. The adaptor is chosen by editing two files in lockstep, then deploying normally.

Pick this skill whenever the user mentions:
- "milestone" / "milestone_onvif" / "mms" / "VMS" / "ONVIF bridge"
- "onvif cameras" / "direct ONVIF"
- "vst_rtsp" / "raw RTSP" / "test streams" / "local dev with nvstreamer"
- "switch adaptor" / "change adaptor" / "VST_ADAPTOR=…"

---

## Step 1 — Pick the adaptor for the user's source

VIOS ships eight adaptor entries in `adaptor_config.json`. Pick the one matching the camera/VMS in front of you:

| `VST_ADAPTOR` value | `type` | What it connects to | When to use | NVStreamer needed? |
|---|---|---|---|---|
| `vst_rtsp` | vst | Generic RTSP URLs | Local dev with NVStreamer-served clips; any raw RTSP source | Yes (typical local-dev pairing) |
| `streamer` | streamer | NVStreamer instance (file-backed) | Pre-recorded clip playback only | Yes |
| `onvif` | vst | ONVIF-compliant IP cameras (direct) | Real ONVIF cameras on the LAN, no VMS | No |
| `remote` | vst | Remote device library | Multi-host topologies | No |
| `native` | vst | Native sensor SDK / custom hardware | Hardware-integrated builds | No |
| `milestone_onvif` | **mms** | Milestone XProtect via its ONVIF Bridge | Milestone VMS where the bridge is enabled | No (RTSP comes from Milestone) |
| `milestone_soap` | **mms** | Milestone XProtect via SOAP API | Milestone VMS without the ONVIF bridge | No |
| `test_vms` | vst | Test/mock harness | Unit testing only | No |

Two important groups:
- **`type: vst`** uses VIOS's own storage/recorder pipeline → `NGINX_MODE` **family** is `vst` (`vst` for direct, `vst-sdrc` for SDRC).
- **`type: mms`** delegates timeline/storage to the external VMS → `NGINX_MODE=mms`.

> **`NGINX_MODE` has two independent dimensions — do not conflate them.**
> - **Family** (`vst` vs `mms`) is set by the **adaptor type** above.
> - The optional **`-sdrc` suffix** is the **SDRC toggle**, which is **NOT an adaptor and has nothing to do with the adaptor**. It must agree with `VST_USE_SDRC` (`true` → `vst-sdrc`, `false` → `vst`). SDRC is the **default**; choose direct mode with `oneclick_dc_deployment.py deploy --no-sdrc`. See `vios-build-system` for the toggle.

---

## Step 2 — Edit both config files in lockstep

The adaptor choice is split across **two files** that **must agree**:

### File A — `services/vios/deployment/stream-processing/docker-compose/compose.env`

```bash
# Pick one of: vst_rtsp, streamer, onvif, remote, native, milestone_onvif, milestone_soap, test_vms
VST_ADAPTOR=<adaptor-name>

# Family from adaptor type: vst-type → vst ; mms-type → mms.
# Append the -sdrc suffix when VST_USE_SDRC=true (SDRC is the default). The
# -sdrc suffix is the SDRC toggle and is INDEPENDENT of the adaptor.
NGINX_MODE=<vst|vst-sdrc|mms>
```

### File B — `services/vios/deployment/stream-processing/docker-compose/configs/adaptor_config.json`

Set `enabled: true` ONLY on the entry whose `name` matches `VST_ADAPTOR`. All other entries must be `enabled: false`. For `mms`-type entries that require external credentials, also fill in `ip`, `user`, `password`, `port`:

```json
{
  "enabled": true,
  "id": "8ada39c7-5e93-43c5-ae5e-21451c8a8d1b",
  "name": "milestone_onvif",
  "type": "mms",
  "ip": "<vms-host-ip>",
  "user": "<vms-user>",
  "password": "<vms-password>",
  "port": "580",
  ...
}
```

> **Heads-up — known dead variables in `compose.env`:** `ADAPTOR_IP`, `ADAPTOR_USER`, `ADAPTOR_PASSWORD`, `AI_BRIDGE_ENDPOINT` (around lines 84-87) are **not referenced by any container or script** in the current standalone deploy. The live values are only the ones inside `adaptor_config.json`. Keep them in sync if you set both (to avoid drifting documentation), but the JSON is the only thing actually consumed.

> **Credentials hygiene:** `adaptor_config.json` contains a plaintext password when configured for `mms`. Treat the file as a secret — do not commit it, or add it to `.gitignore` / `git update-index --skip-worktree`.

---

## Step 2.5 — Credentials pre-flight (mms-type adaptors only)

`mms`-type adaptors (`milestone_onvif`, `milestone_soap`) talk to an external VMS and require **three fields** populated in `adaptor_config.json` on the enabled entry: `ip`, `user`, `password`. If any of these is empty when the deploy runs, sensor-ms either fails to register or registers and never gets streams — a hard-to-debug failure.

Before deploying with an mms-type adaptor, run this check from the deployment directory:

```bash
cd <PROJECT_ROOT>/services/vios/deployment/stream-processing/docker-compose

ENV_ADAPTOR=$(grep -E '^VST_ADAPTOR=' compose.env | cut -d= -f2)

python3 -c "
import json, sys
cfg = json.load(open('configs/adaptor_config.json'))['vst']
target = '$ENV_ADAPTOR'
match = [e for e in cfg if e.get('enabled') and e['name'] == target]
if not match:
    sys.exit(0)  # consistency check (Step 3) will flag this
e = match[0]
if e['type'] != 'mms':
    print(f'OK: type={e[\"type\"]!r} does not require JSON-level credentials')
    sys.exit(0)
missing = [f for f in ('ip', 'user', 'password') if not e.get(f)]
if missing:
    print(f'MISSING CREDENTIALS for {e[\"name\"]!r}: {\", \".join(missing)}'); sys.exit(2)
print(f'OK: {e[\"name\"]!r} credentials populated (ip={e[\"ip\"]}, user={e[\"user\"]}, password=***)')
"
```

Interpret the result:

- `OK: …` → proceed to Step 3.
- `OK: type=… does not require JSON-level credentials` → proceed to Step 3 (vst/streamer/etc. adaptors handle credentials per-sensor at runtime via the VIOS API or UI).
- `MISSING CREDENTIALS for <name>: <field>, …` → **stop and gather the missing fields before deploying**. Three options:
  1. **User provided creds in the prompt** (e.g. *"deploy vios in milestone mode with ip=10.127.52.104 user=onvifuser01 password=foo"*) — parse them, then proceed to the write step below.
  2. **User did not provide creds** — ask once:
     > *"The `<adaptor>` adaptor needs `<missing fields>` to talk to the VMS. They aren't set in `adaptor_config.json`. Please provide them (e.g. `ip=10.127.52.104 user=onvifuser01 password=…`), or paste the values you'd like written. I'll update `adaptor_config.json` after you confirm."*
  3. **User wants to set them manually** — point them at `configs/adaptor_config.json` and offer to re-run the deploy after they save the file.

### Writing creds back into adaptor_config.json (only after explicit user consent)

When you have the missing values, show a **dry-run diff** of what you'll write before touching the file:

```bash
# Dry-run preview (does not modify the file)
python3 -c "
import json
cfg = json.load(open('configs/adaptor_config.json'))
target = '$ENV_ADAPTOR'
for e in cfg['vst']:
    if e.get('enabled') and e['name'] == target:
        print('Will set on entry:')
        print(f'  name = {e[\"name\"]}')
        print(f'  ip   : {e.get(\"ip\") or \"(empty)\"!r} -> {\"<NEW_IP>\"!r}')
        print(f'  user : {e.get(\"user\") or \"(empty)\"!r} -> {\"<NEW_USER>\"!r}')
        print(f'  pass : {\"***\" if e.get(\"password\") else \"(empty)\"} -> ***')
"
```

Ask the user to confirm the diff, then write:

```bash
python3 - <<'PY'
import json, os, sys
NEW_IP   = os.environ['NEW_IP']
NEW_USER = os.environ['NEW_USER']
NEW_PASS = os.environ['NEW_PASS']
TARGET   = os.environ['VST_ADAPTOR']
path = 'configs/adaptor_config.json'
cfg = json.load(open(path))
n = 0
for e in cfg['vst']:
    if e.get('enabled') and e['name'] == TARGET and e['type'] == 'mms':
        e['ip'], e['user'], e['password'] = NEW_IP, NEW_USER, NEW_PASS
        n += 1
if n != 1:
    print(f'ERROR: expected exactly 1 matching entry, found {n}'); sys.exit(1)
with open(path, 'w') as f:
    json.dump(cfg, f, indent='\t')
    f.write('\n')
print(f'Updated {path}: {TARGET} now has ip/user/password set')
PY
```

**Credentials hygiene reminder** (display this after writing):

> *"Wrote credentials to `adaptor_config.json`. This file is now in your git working tree with a plaintext password. To prevent committing it: `git update-index --skip-worktree services/vios/deployment/stream-processing/docker-compose/configs/adaptor_config.json` (or add it to `.gitignore` if it isn't already)."*

---

## Step 3 — Pre-deploy consistency check

Run this snippet from the deployment directory. It prints the env values and the matching JSON entry, then flags any mismatch:

```bash
cd <PROJECT_ROOT>/services/vios/deployment/stream-processing/docker-compose

ENV_ADAPTOR=$(grep -E '^VST_ADAPTOR=' compose.env | cut -d= -f2)
ENV_NGINX=$(grep -E '^NGINX_MODE=' compose.env | cut -d= -f2)
ENV_USE_SDRC=$(grep -E '^VST_USE_SDRC=' compose.env | cut -d= -f2)
echo "compose.env: VST_ADAPTOR=$ENV_ADAPTOR  NGINX_MODE=$ENV_NGINX  VST_USE_SDRC=$ENV_USE_SDRC"

python3 -c "
import json, sys
cfg = json.load(open('configs/adaptor_config.json'))['vst']
enabled = [e for e in cfg if e.get('enabled')]
print(f'adaptor_config.json: {len(enabled)} enabled entries')
for e in enabled:
    print(f'  - name={e[\"name\"]!r}  type={e[\"type\"]!r}')
target = '$ENV_ADAPTOR'
match = [e for e in enabled if e['name'] == target]
if not match:
    print(f'ERROR: no enabled entry matches VST_ADAPTOR=$ENV_ADAPTOR'); sys.exit(1)
if len(enabled) > 1:
    print('WARNING: multiple enabled entries — disable the others first'); sys.exit(1)
nginx = '$ENV_NGINX'
# NGINX_MODE has two INDEPENDENT axes:
#   1) family (vst|mms): determined by the adaptor type.
#   2) optional '-sdrc' suffix: the SDRC toggle, which is NOT an adaptor concern
#      and must agree with VST_USE_SDRC.
base = nginx[:-5] if nginx.endswith('-sdrc') else nginx
family = 'mms' if match[0]['type'] == 'mms' else 'vst'
if base != family:
    print(f'ERROR: NGINX_MODE={nginx} (family {base!r}) but adaptor type={match[0][\"type\"]!r} expects family {family!r}'); sys.exit(1)
sdrc_nginx = nginx.endswith('-sdrc')
use_sdrc = '$ENV_USE_SDRC'.strip().lower() == 'true'
if sdrc_nginx != use_sdrc:
    print(f'ERROR: SDRC mismatch — NGINX_MODE={nginx} (sdrc={sdrc_nginx}) vs VST_USE_SDRC={use_sdrc}. The -sdrc suffix must match VST_USE_SDRC (this is independent of the adaptor).'); sys.exit(1)
print(f'OK: adaptor family + SDRC toggle are consistent (family={family}, sdrc={use_sdrc})')
"
```

Stop and report any error from this check **before** invoking the deploy script — they all cause silent runtime failures (sensors don't register, timelines route wrong, etc.) that are hard to debug post-hoc.

---

## Step 4 — Decide if NVStreamer should run alongside

NVStreamer and the adaptor are **independent**. The adaptor only controls how VIOS discovers/controls cameras; NVStreamer is just one possible RTSP source. The two questions are:

1. **Does this adaptor need NVStreamer to function?**
   - `VST_ADAPTOR ∈ {vst_rtsp, streamer}` → typically yes (NVStreamer is the usual RTSP source for these adaptors in local dev).
   - Any other adaptor → no, RTSP comes from a camera / VMS / mock.

2. **Does the user want NVStreamer running anyway?**
   - This is an **independent choice**. NVStreamer can always be deployed as an additional RTSP source — for testing alternative streams, mixing camera + canned video, running parallel workflows, etc.

The deploy flow in `skills/deployment/deploy.md` Step 2 handles this correctly:

- If the user says *"deploy vios + nvstreamer"* / *"deploy vios and nvstreamer"* / *"deploy vios with nvstreamer"* → NVStreamer is deployed too, regardless of the active adaptor.
- If the user says *"deploy vios without nvstreamer"* / *"skip nvstreamer"* → NVStreamer is not deployed, regardless of the active adaptor.
- If the user is silent on NVStreamer → the adaptor's default behavior kicks in (deploy NVStreamer for `vst_rtsp`/`streamer`, skip for the others). User can override with explicit phrasing.

So the answer to *"do I need NVStreamer for milestone_onvif?"* is: **technically no, but you may still want it deployed.** Tell the agent explicitly if you do.

---

## Step 5 — Deploy

The deploy script reads `compose.env` automatically — there are no adaptor-specific CLI flags. Just run the normal deploy:

```bash
cd <PROJECT_ROOT>/services/vios/deployment/stream-processing
python3 oneclick_dc_deployment.py deploy --force
# add --skip-sysctl in agent / CI runs (see DEPLOYMENT_AGENT.md operating rules)
```

---

## Step 6 — Verify the adaptor came up

After deploy completes:

```bash
# 1) Confirm the env var landed inside the sensor container
docker inspect sensor-ms --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ADAPTOR='
# expected: ADAPTOR=<your VST_ADAPTOR>

# 2) Confirm sensor-ms picked up the matching adaptor lib
docker logs sensor-ms 2>&1 | grep -iE 'adaptor|loaded|control_adaptor_lib_path' | head -20
# expected: a line referencing your adaptor's name and the .so it loaded

# 3) Confirm ingress is serving the right config
docker exec vst-ingress sha256sum /etc/nginx/nginx.conf
# match this hash against the local nginx-<NGINX_MODE>.conf to confirm the right file was mounted
```

For `mms`-type adaptors, also verify the VMS is reachable from the host:

```bash
ip=$(grep -E '"ip"' configs/adaptor_config.json | head -1 | sed 's/.*"ip":"\([^"]*\)".*/\1/')
port=$(grep -E '"port"' configs/adaptor_config.json | head -1 | sed 's/.*"port":"\([^"]*\)".*/\1/')
nc -zv "$ip" "$port" 2>&1 || echo "VMS unreachable — check firewall / VPN"
```

---

## Common patterns (quick reference)

`NGINX_MODE` below shows the family; append `-sdrc` when `VST_USE_SDRC=true` (the default — so the shipped `compose.env` uses `vst-sdrc`). The `-sdrc` suffix is independent of the adaptor; use `--no-sdrc` for the plain-family (direct) values.

| Workflow | `VST_ADAPTOR` | `NGINX_MODE` (family) | NVStreamer | Notes |
|---|---|---|---|---|
| Default local dev with prerecorded clips | `vst_rtsp` | `vst` (`vst-sdrc` by default) | yes | Most common. Shipped `compose.env` defaults to SDRC (`vst-sdrc`); add `--no-sdrc` for direct (`vst`). |
| Direct ONVIF cameras on LAN | `onvif` | `vst` (`vst-sdrc` by default) | no | Cameras auto-discovered via ONVIF probe. |
| Milestone XProtect via ONVIF Bridge | `milestone_onvif` | `mms` | no | Requires Milestone ONVIF Bridge add-on enabled in XProtect. |
| Milestone XProtect via SOAP | `milestone_soap` | `mms` | no | Use if ONVIF bridge is unavailable. |
| Unit/integration test harness | `test_vms` | `vst` | no | Mock cameras for CI. |
