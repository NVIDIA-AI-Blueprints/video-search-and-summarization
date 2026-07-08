# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Deployment Agent — Master Guide

Standalone stack management for VIOS. Each operation is independent — the user decides what to run and in what order. Tests and UI validation are out of scope; direct the user to the SQA agent for those.

---

## Operations

| Operation | Skill | Notes |
|---|---|---|
| `build` | `skills/build/build-containers.md` | Build container images from source |
<<<<<<< HEAD
| `deploy` | `skills/deployment/deploy.md` | Deploy the target the user specified; default is **VIOS only** (`deploy --force`). NVStreamer is **opt-in** — deployed only on explicit `+ nvstreamer` / full stack / `--target nvstreamer` (see Operating Rules). For adaptor switches (MMS / Milestone / ONVIF / etc.) also consult `skills/deployment/adaptor-mode.md`. |
=======
| `deploy` | `skills/deployment/deploy.md` | Deploy with the target the user specified; default is stream-processor (read `VST_ADAPTOR` / `NGINX_MODE` from `compose.env`, then probe NVStreamer state if applicable — see Operating Rules). For adaptor switches (MMS / Milestone / ONVIF / etc.) also consult `skills/deployment/adaptor-mode.md`. |
>>>>>>> 5007a4e229affe53f7848545ee04a695657e0f44
| `stop` | `skills/deployment/stop.md` | Stop services by target or all |
| `status` | (inline) | `docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" \| grep -E "vios\|nvstreamer\|redis"` |

---

## Workflow

1. Identify the requested operation: `build`, `deploy`, `stop`, or `status`.
2. Read the corresponding skill file listed above.
<<<<<<< HEAD
3. For `deploy` with the default target, deploy **VIOS only** (`deploy --force`); NVStreamer is **opt-in** per the **Operating Rules** below (only on explicit `+ nvstreamer` / full stack). Other targets:
=======
3. For `deploy` with the default target (stream-processor), follow the NVStreamer state-aware policy in **Operating Rules** below — probe first, then either skip / ask / two-step. Other targets:
>>>>>>> 5007a4e229affe53f7848545ee04a695657e0f44
   ```
   # Full stack — script deploys NVStreamer internally (single command, no probe needed):
   deploy --target all --force

   # NVStreamer only:
   deploy --target nvstreamer --force
   ```
4. After a successful `deploy`, sync `test/bdd_tests/config.json` → `api.base_url` with the resolved BASE_URL.
5. Report the outcome.

---

## Operating Rules

- **"tot" / "top of tree":** If the user says "tot", "top of tree", or any phrase implying they want the latest code (e.g. "build from tot", "deploy tot"), run `git pull` in PROJECT_ROOT before starting the requested operation.
- Never run `--fresh-start` without explicit user approval — it wipes the database.
- **Stop vs. clean stop (`--clean`):** plain `stop` only removes containers; persistent data on the host (VST volume, NVStreamer videos, postgres named volume) survives. Map user phrasing to the flag:
  - "stop" / "tear down" / "shut down" → `stop` (no `--clean`).
  - **"clean stop"** / **"stop and clean (up)"** / "wipe data" / "remove data" / "purge volumes" / "fresh slate" / "reset state" → `stop --clean`.
  - If the user's intent is ambiguous between these two, ask once before running — `--clean` is irreversible. Don't infer `--clean` from the word "clean" alone if the sentence reads more like "stop cleanly" (i.e., gracefully).
- **Sysctl pre-flight (avoids password prompts mid-deploy).** Before invoking `deploy`, run the script's static probe (no inline shell, no `sudo` invocation — coding agents won't ask for approval on every run):
  ```bash
  python3 oneclick_dc_deployment.py preflight-sysctl
  ```
  It prints a single line: `SYSCTL_PREFLIGHT status=<S> rmem_max=… …`. Branch on `status`:
  - `status=skip` → all four buffers already meet target. Pass `--skip-sysctl` to silence the info log (script would no-op anyway).
  - `status=passwordless` → tuning needed AND `sudo -n` works; run normally, sudo passes through silently.
  - `status=needs_password` + interactive user → ask: *"Deploy will need sudo to tune kernel network buffers (rmem_max → 2 MB, etc.). Provide password? OR pass --skip-sysctl to skip (throughput may be lower under load)."* Then proceed with their choice.
  - `status=needs_password` + non-interactive (can't relay a prompt) → pass `--skip-sysctl` to avoid hanging. The script also auto-skips in this case but the explicit flag is clearer in the deploy log.
- **After a build step:** `compose.env` has pinned versioned tags that do NOT match locally built images. Always append `--all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>` to every deploy command. `--all-tag` covers stream-processor + sensor images; `--nvstreamer-tag` covers NVStreamer. `build.sh` defaults to `latest`. Do not add `--pull-always`.
- Do not modify `deployment/**/docker-compose*.y*ml` without instruction.
- **Default deploy target is stream-processor only.** Requests like "deploy", "deploy VST", "deploy VIOS", or any deploy request without an explicit target → stream-processor.
<<<<<<< HEAD
- **Deployment mode: SDRC (default) vs direct — independent of the adaptor.** The stack now defaults to **SDRC mode** (`VST_USE_SDRC=true`, `COMPOSE_PROFILES=sdrc`, `NGINX_MODE=vst-sdrc`, `sdr-controller` running). This is a **separate axis** from `VST_ADAPTOR` — `vst-sdrc` is **not** an adaptor and has nothing to do with adaptor selection. Map the user's intent:
  - Silent on mode → deploy as-is (SDRC default); do not touch the toggle.
  - "direct mode" / "no sdrc" / "without sdr(c)" / "single-pod" / "no sdr-controller" → add **`--no-sdrc`** to the deploy command. It rewrites `compose.env` to direct values (`VST_USE_SDRC=false`, `NGINX_MODE=vst`, cleared `COMPOSE_PROFILES`, stream-processor on `:30001`) and **persists**, so later deploys stay direct until re-toggled in `compose.env`.
  - "sdrc" / "with sdr" / "scaling" / "multi-pod" / "more than one stream-processor pod" → that is the default; no flag needed.
  - **Scaling REQUIRES SDRC.** Direct mode is **single-pod only** — there is no router to fan stream-bound APIs across pods. If the user wants to scale out (multiple `streamprocessing-ms-N` pods, or more than ~100 streams), SDRC **must** be enabled: do **not** pass `--no-sdrc`, and if `compose.env` is currently direct, switch it back to SDRC first. Treat any "direct mode **and** scaling / multiple pods" request as a contradiction — flag it to the user and keep SDRC.
- **NVStreamer is opt-in — a bare deploy never touches it.** A plain *"deploy"* / *"deploy vios"* / *"deploy vst"* / *"start vios deployment"* (silent on NVStreamer) deploys **VIOS only** (`deploy --force`): do **not** probe for, start, or reuse-decide NVStreamer, and do **not** branch on the adaptor for this. NVStreamer runs only when the user explicitly asks or wants the full stack. Classify the prompt:
  1. **Explicit "NVStreamer too"** — prompt pairs `nvstreamer` with `+`, `and`, `with`, `alongside`, `&`, or `plus` (e.g. *"deploy vios + nvstreamer"*, *"deploy vios and nvstreamer"*). → two-step: `deploy --target nvstreamer --force` then `deploy --force` (or `deploy --target all --force` when the wording reads as "full stack").
  2. **Full stack / everything** — *"full stack"*, *"everything"*, *"legacy/regular/full deployment"*. → `deploy --target all --force` (script deploys NVStreamer internally).
  3. **NVStreamer only** — *"deploy nvstreamer"*. → `deploy --target nvstreamer --force` (no VIOS).
  4. **Anything else (bare deploy / silent / "without nvstreamer")** → `deploy --force` (VIOS only). Do not touch NVStreamer. If the adaptor is `vst_rtsp`/`streamer` and no RTSP source is configured, just note in the response that sensors will register but receive no frames until an RTSP source is provided (re-run with `+ nvstreamer`, or point sensors at an external RTSP/camera) — do **not** deploy NVStreamer automatically. For adaptor consistency checks, see `skills/deployment/adaptor-mode.md`.
=======
- **NVStreamer is independent of the adaptor.** NVStreamer is just an RTSP source the user may or may not want running. The adaptor only determines the **default** when the user is silent on NVStreamer; explicit user intent always wins. Classify the user's deploy prompt:
  1. **Explicit "deploy NVStreamer too"** — the prompt contains any of: `nvstreamer` paired with `+`, `and`, `with`, `alongside`, `&`, or `plus` (e.g. *"deploy vios in milestone adaptor & deploy nvstreamer"*, *"deploy vios + nvstreamer"*, *"deploy vios and nvstreamer"*). → Run the two-step sequence regardless of adaptor: `deploy --target nvstreamer --force` then `deploy --force`. Equivalent (and simpler) is `deploy --target all --force` — pick that when the user's wording reads as "full stack" / "everything", otherwise stay with the two-step so logs clearly show both steps.
  2. **Explicit "skip NVStreamer"** — the prompt contains any of: `without nvstreamer`, `skip nvstreamer`, `no nvstreamer`, `rtsp from elsewhere`, `external rtsp`. → Run only `deploy --force`. No probe. Log: *"User opted out of NVStreamer — sensors must get RTSP from the configured source."*
  3. **Silent on NVStreamer (bare "deploy vios" / "deploy vst" / etc.)** — apply the adaptor-aware default below.
- **Adaptor-aware default (only when the user is silent on NVStreamer).** Read `VST_ADAPTOR` and `NGINX_MODE` from compose.env:
  ```bash
  ENV_FILE=services/vios/deployment/stream-processing/docker-compose/compose.env
  VST_ADAPTOR=$(grep -E '^VST_ADAPTOR=' "$ENV_FILE" | cut -d= -f2)
  NGINX_MODE=$(grep -E '^NGINX_MODE=' "$ENV_FILE" | cut -d= -f2)
  ```
  Then branch:
  - `VST_ADAPTOR ∈ {vst_rtsp, streamer}` (or unset → defaults to `vst_rtsp`) — NVStreamer is the typical RTSP source for this adaptor. **Probe NVStreamer state:**
    - `nvstreamer-1` healthy → reuse, log *"Reusing existing NVStreamer (up <duration>)"*, run `deploy --force` only.
    - `nvstreamer-1` not running / unhealthy → **ask the user once**: *"VIOS adaptor is `<VST_ADAPTOR>` which usually pairs with NVStreamer for RTSP. NVStreamer isn't running. Deploy it alongside, or skip if RTSP comes from elsewhere?"* — confirm → two-step sequence; decline → `deploy --force` with the warning above.
  - `VST_ADAPTOR ∈ {onvif, remote, native, milestone_onvif, milestone_soap, test_vms}` — RTSP comes from a camera / VMS / mock by default. **Default to not deploying NVStreamer**, but do NOT silently skip — announce the decision so the user can override:
    > *"Detected `VST_ADAPTOR=<value>` (`NGINX_MODE=<value>`). RTSP source is external — deploying VIOS only (not NVStreamer). If you also want NVStreamer running as an additional RTSP source, say `+ nvstreamer` and I'll re-run."*
    Then run `deploy --force`. For adaptor details and consistency checks, follow `skills/deployment/adaptor-mode.md`.
>>>>>>> 5007a4e229affe53f7848545ee04a695657e0f44
- **`--target all` is reserved for explicit "full stack" / "everything" wording.** It deploys NVStreamer internally — do not add an extra `--target nvstreamer` step before it; that would deploy NVStreamer twice.
- **MMS adaptor credentials pre-flight.** When `VST_ADAPTOR` resolves to an `mms`-type entry (`milestone_onvif`, `milestone_soap`), check the enabled entry in `adaptor_config.json` for non-empty `ip`, `user`, `password` **before** running the deploy. If any are empty:
  1. First parse the user's prompt for credentials of the form `ip=…`, `user=…`, `password=…`, `host=…`, `vms_host=…`. If found, use them.
  2. Otherwise ask the user once: *"The `<adaptor>` adaptor needs `<missing fields>` to talk to the VMS — they aren't set in `adaptor_config.json`. Please provide `ip`/`user`/`password`, or update the file manually and confirm."*
  3. Before writing creds to `adaptor_config.json`, show a dry-run diff (mask the password as `***`) and require user confirmation.
  4. After writing, remind the user to gitignore / `skip-worktree` the file. See `skills/deployment/adaptor-mode.md` Step 2.5 for the exact commands.
  Never proceed to deploy with missing mms credentials — it will silently fail at runtime.
<<<<<<< HEAD
- **Explicit NVStreamer / full-stack phrases (quick reference):**
  - "full stack" / "everything" / "legacy deployment" / "regular deployment" / "full service deployment" → `deploy --target all --force` (single command; script handles NVStreamer internally).
  - "deploy nvstreamer" / "deploy the streamer" → `deploy --target nvstreamer --force` (no VIOS).
  - "deploy vios + nvstreamer" / "deploy both" → same as full stack (`--target all`).
- After deploy: show the UI link labeled **`VIOS UI:`** — e.g. `VIOS UI: http://<HOST_IP>:30888/vst/#/dashboard` (always "VIOS UI", never a bare "UI"). For an NVStreamer deploy (`--target nvstreamer`) or the full stack, also show each active instance labeled **`NVStreamer UI:`** — e.g. `NVStreamer UI: http://<HOST_IP>:31000/#/dashboard` (one line per active instance, ports `31000…31004`). Show **nothing else for the URL**: **never print the raw `BASE_URL`** — not on its own line, and not combined with the UI link (e.g. `BASE_URL: … — UI: …` is wrong). The UI link already contains host:port; `BASE_URL` is internal-only (used for the link and the `config.json` sync). Also report, from `compose.env`, the active adaptor **and** the deployment mode (SDRC vs direct), so the user can verify the stack came up as intended — e.g. *"Adaptor: `vst_rtsp`, NGINX_MODE: `vst-sdrc`. Mode: SDRC (sdr-controller up)."* or *"Adaptor: `milestone_onvif` (mms-type), NGINX_MODE: `mms`. Mode: direct (VST_USE_SDRC=false). NVStreamer not deployed."*
=======
- **Phrases that bypass the probe + ask** (user explicitly signals full stack):
  - "full stack" / "everything" / "legacy deployment" / "regular deployment" / "full service deployment" → `deploy --target all --force` (single command; script handles NVStreamer internally).
  - "deploy nvstreamer" / "deploy the streamer" → `deploy --target nvstreamer --force` (no VIOS).
  - "deploy vios + nvstreamer" / "deploy both" → same as full stack (`--target all`).
- After deploy: include BASE_URL and VIOS UI link (`<BASE_URL>/vst/#/dashboard`) in the response. Also report the active adaptor mode read from `compose.env` (e.g. *"Adaptor: `milestone_onvif` (mms-type), NGINX_MODE: `mms`. NVStreamer not deployed."*) so the user can verify the stack came up in the intended mode.
>>>>>>> 5007a4e229affe53f7848545ee04a695657e0f44

---

## Response Style

- Lead with the operation result: deployed / stopped / built / status table.
- List running containers and their status after deploy or status operations.
- **It's a "VIOS deployment" in every case — call it that.** SDRC is just one infra component (the SDR controller); it is **not** a different kind of deployment. State the mode **once** in the post-deploy summary (e.g. *"SDRC: on"* or *"direct (no SDR controller)"*) and move on. Do **not** prefix steps/lines with "SDRC-mode" / "direct-mode", and do **not** repeat the mode label throughout the run. Progress and result lines should read as "VIOS deployment …", not "direct-mode deploy …". **This includes the names you give background tasks/monitors** — name them e.g. *"VIOS deployment"* / *"Wait for VIOS deployment"*, never *"direct deploy"* / *"direct-mode deploy"* (those names echo back in monitor events and re-spam the mode label).
- **Never surface `BASE_URL` in output.** Show only the VIOS UI link (it already contains host:port). No standalone `BASE_URL:` line and no combined `BASE_URL: … — UI: …` form. `BASE_URL` is an internal value only.
- No emojis. No filler text.
