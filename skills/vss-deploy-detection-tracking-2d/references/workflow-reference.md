# Workflow Reference

Status messages, error recovery, and agent-vs-script responsibility tables for the deploy workflow.

## Status Messages (what to print at each transition)

| When | Print |
|---|---|
| Before Step 1 | *(no print — just call `TodoWrite` with the full 10-task plan)* |
| Identifying use case | `Identifying use case to deploy...` |
| Use case confirmed | `Use case confirmed: <usecase>. Looking up its NGC resources and config files in references/usecases.md.` |
| Detecting platform | `Detecting target platform via uname -m and nvidia-smi...` |
| Platform auto-accepted | `Platform: <platform> (arch=<ARCH>, Jetson=<yes|no>, GPU=<GPU>) — auto-detected, no confirmation needed.` |
| Platform fallback (rare) | `Could not auto-detect platform — asking user.` |
| Collecting image ref | `Need the RTVI-CV docker image reference. Asking user...` |
| Image verified | `Docker image verified: <IMAGE> (arch: <ARCH>, matches <PLATFORM>)` |
| Image mismatch | `WARNING: image <IMAGE> is <ARCH> but platform is <PLATFORM>.` |
| Checking NGC creds | `Checking NGC credentials at ~/.ngc/config...` |
| NGC creds reused | `Using existing NGC config for org <ORG> — skipping credential prompt.` |
| NGC creds saved | `NGC credentials saved to ~/.ngc/config (chmod 600, reused on every future run).` |
| Collecting NGC refs | `Collecting NGC resource references for <usecase>...` |
| Pipeline configured | `Pipeline config: batch=<N>, streams=<mode>, input=<type>, sink=<sink>` |
| Resource reuse | `Reusing existing resource: <NAME> (saved ~10 GB download)` |
| Resource download start | `Downloading <RESOURCE>... (this may take several minutes)` |
| Resource download done | `Downloaded <RESOURCE> ✓` |
| Checking for existing | `Checking for an existing RTVI-CV container using image <IMAGE>...` |
| Existing found (good mounts) | `Found existing <NAME> running <IMAGE> with correct mounts — asking user whether to reuse, restart, or go parallel.` |
| Existing found (bad mounts) | `Found existing <NAME> but required mounts are missing: <LIST> — asking user to restart or go parallel.` |
| Reusing | `Reusing existing container <NAME> — skipping docker run, going straight to config apply.` |
| Stopping to restart | `Stopping <NAME> to relaunch with fresh config...` |
| Parallel launch | `Launching parallel container <NEW_NAME> on REST port <PORT> (existing <OLD_NAME> untouched)...` |
| Launching container | `Launching RTVI-CV container (name=<CONTAINER_NAME>, image=<IMAGE>)...` |
| Container up | `Container is running. Entering for configuration...` |
| Applying config | `Applying <usecase> configuration inside container...` |
| Discovering paths | `  - discovering NGC resource paths via find $RESOURCES...` |
| Substituting paths | `  - updating model path placeholders in configs...` |
| Batch size | `  - running update_batch_size.sh <usecase> <N>...` |
| Sink update | `  - applying <sink> sink edits to main config...` |
| Source list | `  - populating source list (static mode, <N> streams)...` |
| Extra setup | `  - running setup_<gdino|sparse4d>.sh...` |
| Encoder deps — validated | `  - ENCODER_DEPS: x264enc already registered ✓ (no install needed)` |
| Encoder deps — installing | `  - ENCODER_DEPS: software video encoders missing, installing via user_additional_install.sh (one-time, ~1-2 min)...` |
| Encoder deps — stale marker | `  - ENCODER_DEPS: marker present but x264enc missing — removing stale marker and reinstalling` |
| Encoder deps — installed | `  - ENCODER_DEPS: install complete, x264enc registered ✓ — filedump sink ready` |
| Encoder deps — install failed | `  - ENCODER_DEPS: install FAILED — show /tmp/ds_user_install.log to the user; fall back to eglsink or enc-type=0 hardware` |
| Encoder deps — skipped (flag) | `  - ENCODER_DEPS: --skip-encoder-install set, x264enc is missing; expect pipeline failure unless you flip [sink2] enc-type=0 afterwards` |
| Engine prelaunch (nvinfer) exact | `  - ENGINE PRELAUNCH (exact) — <model> b<N> engine already present, DS will deserialize directly ✓` |
| Engine prelaunch (nvinfer) compat | `  - ENGINE PRELAUNCH (compatible) — symlinked larger b<M> engine for <model> b<N> request, skipped ~3-5 min build ✓` |
| Engine prelaunch (nvinfer) symlink | `  - ENGINE PRELAUNCH (symlink) — pre-existing symlink from prior deploy, resolves to valid engine ✓` |
| Engine prelaunch (nvinfer) miss | `  - ENGINE PRELAUNCH (miss) — no cached <model> engine >= b<N>, DS will build from ONNX (~3-5 min)` |
| Engine cache hit (exact) | `  - ENGINE CACHE HIT (exact) — reusing cached <model> b<N> engine, skipped ~3-10 min build ✓` |
| Engine cache hit (compat) | `  - ENGINE CACHE HIT (compatible) — reusing larger b<M> <model> engine for b<N> request, skipped ~3-10 min build ✓` |
| Engine cache miss | `  - ENGINE CACHE MISS — no cached <model> engine for b<N>, building now (~3-10 min, one-time cost)...` |
| Engine force rebuild | `  - FORCE REBUILD — ignoring cached <model> engine, rebuilding from scratch...` |
| Engine cache saved | `  - Engine cached at <path> for future reuse ✓` |
| Config done | `Configuration complete.` |
| Initializing log | `Initializing deployment log at $STORAGE/logs/<usecase-and-model>_<ts>.txt (settings + configs + docker cmd)...` |
| Log ready | `Deployment log ready: ~/rtvicv-storage/logs/<usecase-and-model>_<ts>.txt` |
| Starting app | `Starting metropolis_perception_app -c <config-file> (output -> deployment log)...` |
| Caching nvinfer engine | `Linking DS-auto-built engine -> $ENGINE_CACHE_DIR/<model>_b<N>.engine (future deploys skip the rebuild)` |
| App ready | `RTVI-CV is live at http://localhost:9000 — full runtime log at ~/rtvicv-storage/logs/<usecase-and-model>_<ts>.txt` |
| Done | `Deployment complete. Switch to this skill's API USAGE flow to add streams.` |
| Stream add plan | `Adding <N> streams dynamically with <DELAY>s spacing — total add time ≈ <(N-1)*DELAY>s.` |
| Stream add progress | `Adding stream <i>/<N>: <camera_id> (<camera_url>)...` |
| Stream added | `Added <camera_id> ✓ (<i>/<N>)` |
| Stream gap | `Waiting <DELAY>s before next stream add (pipeline attach stability)...` |
| Stream add done | `All <N> streams added.` |
| Removing stream | `Removing stream <camera_id> (<camera_url>)...` |
| Stream removed | `Stream <camera_id> removed ✓ (<ACTIVE-1>/<MAX_BATCH> active)` |
| Stop app | `Stopping perception app inside <CONTAINER_NAME> (container stays up for fast redeploy)...` |
| App stopped | `Perception app stopped. Container <CONTAINER_NAME> is idle — call Step 5 again to restart with new config.` |
| Stop docker | `Stopping container <CONTAINER_NAME> (graceful)...` |
| Docker stopped | `Container <CONTAINER_NAME> stopped. Cache + NGC creds preserved on host.` |

## Error Recovery

| Error | Cause | Fix |
|---|---|---|
| `ngc: command not found` | NGC CLI missing on host or old image | Install NGC CLI (`pip install ngcsdk`) or run inside the container where it's pre-installed |
| NGC auth error | Bad API key or wrong org | Back up the bad config (`mv ~/.ngc/config ~/.ngc/config.bak`), re-prompt user |
| `nvidia-container-cli: device error` | GPU index wrong or driver mismatch | Check `nvidia-smi`; try `--gpus all` instead of `--gpus "device=N"` |
| `bind: address already in use` (port 9000) | Another RTVI-CV or dashboard holds 9000 | Stop conflicting process or change `REST_API_PORT` in main config |
| Display (eglsink) shows nothing | X11 forwarding not set up | `xhost +` on host, confirm `DISPLAY` env, mount `/tmp/.X11-unix` |
| `** ERROR: <main:2216>: Failed to set pipeline to PAUSED` (eglsink) | `DISPLAY` inside container is unset, empty, or malformed (e.g. literal `1` instead of `:1`). Happens most on **reused** containers launched earlier with a bad `-e DISPLAY=...`. | Do NOT restart the container. Re-launch with `docker exec -d -e DISPLAY=:<N> -e XAUTHORITY=/root/.Xauthority ...` (see `start-app.md` § 5.b.2). Run the 5.b.1 pre-flight (`xdpyinfo` probe) first to confirm the display resolves before re-launch. |
| `ERROR: [TRT]: ... kFP16` followed by `Retrying without explicit FP16 flag` | nvinfer applies `kFP16` by default; strongly-typed FP16 ONNX models (RT-DETR ships this way) conflict with that flag on the first build attempt. | **Do nothing — this is expected.** The retry succeeds and writes the engine. Wait for `serialize cuda engine to file: ... successfully`. |
| Sparse4D engine build fails | `LD_PRELOAD` not set before setup | Export `LD_PRELOAD=$SPARSE4D_REPO/libmsda_fp16.so` then re-run `setup_sparse4d.sh` |
| GDINO `model.plan` missing | Setup script didn't run or ONNX not found | Re-run `setup_gdino.sh --batch <N>` (check `$RESOURCES` for the ONNX) |
| Batch size change didn't take effect | Edit hit the wrong file | Check `*.bak` files in `$CONFIGS/<usecase>/` to diff; re-run `update_batch_size.sh <uc> <N>` |
| Docker image arch mismatch | Wrong tag for platform (e.g. non-SBSA image on Spark) | Ask user for a different tag; SBSA needs `-sbsa-` in the tag |
| Sparse4D detections look wrong / BEV projection off | Picked the wrong videos directory for warehouse-3d — `.mp4` stems don't match `calibration.json` `sensors[].id` | Re-run Step 4.a's video-dir picker and select the directory whose stems match calibration; if the NGC resource supplies multiple, the picker shows all options |
| Stale cached engine gives wrong output | Cache has an old engine (ONNX changed, but cache file name matched batch) | `FORCE_ENGINE_REBUILD=1 /tmp/scripts/setup_<model>.sh --batch <N>` or pass `--force`; or delete the stale file from `$ENGINE_CACHE_DIR` |
| Engine cache not persisting across runs | `/opt/storage` mount missing from `docker run` | Add `-v $HOME/rtvicv-storage:/opt/storage` to docker run — cache lives at `/opt/storage/engines/` |
| Parallel container fails to bind `:9000` | Another RTVI-CV container already holds port 9000 on `--network=host` | Switch to user-defined bridge network, or in the main config set `[http-server] http-port=9001` (and use that URL for stream add). The skill's "parallel" path does this automatically. |
| Reused container has stale configs | User picked "reuse" but the baked configs inside the container don't reflect new NGC resource / batch | Pick "restart" instead, OR ensure `reference-configs` is mounted from the host so config edits persist correctly. |
| Filedump sink fails with `Failed to create sink_sub_bin_encoder1` / `no element "x264enc"` | Software encoder deps not installed (should have been auto-installed by `update_output_sink.sh filedump`, but was skipped — e.g. `--skip-encoder-install` was passed, or the install script failed silently) | Simplest fix: re-run `docker exec <CONTAINER_NAME> /tmp/scripts/update_output_sink.sh <usecase> filedump` — the new `ensure_encoder_deps` will detect the missing plugin via `gst-inspect-1.0 x264enc` and reinstall even if the marker file is present. If that still fails, inspect `/tmp/ds_user_install.log` inside the container. Last-resort: flip `[sink2] enc-type=0` to use hardware `nvv4l2h264enc` instead. |
| Filedump `.mp4` file won't play in a strict MP4 parser (cloud pipeline, browser `<video>` fallback) | By default the skill writes `.mp4` filename + MKV muxer (container=2) for on-kill recoverability — decoupled from the filename. Most players auto-detect by content, but strict MP4 parsers expect real moov atoms and reject the MKV bytes. | Re-run `update_output_sink.sh <usecase> filedump --container 1` to force true MP4 bytes. Accept the tradeoff: the file is unplayable if the app is killed before writing the moov atom. Delete the old output file first. |
| Filedump output is empty or zero-bytes after `docker stop` | App was killed before MP4 muxer wrote the moov atom (this is why the default muxer is MKV, container=2). | Stop using `--container 1` for development runs; revert to the default (MKV muxer + `.mp4` filename). The file will stay playable through the last written frame even on SIGKILL. |

## Bash Batching (reduce permission prompts)

Sequential bash commands with no user decision between them **must be combined into a single bash tool call** — never one call per line.

| Pattern | Rule |
|---|---|
| Variable set → use → report (same step) | One call |
| Cache check → content scan → engine check | One call (all read-only; result parsed together) |
| `docker exec` for 2+ sub-steps in the same step | One call via `bash -c "cmd1 && cmd2 && cmd3"` |
| Log tail + grep filter | One call |
| Two calls where call 2 is always run after call 1 | One call with `&&` or `;` |
| Two calls where call 2 depends on a conditional from call 1 output | Two calls are OK — genuine branch |

Splitting a single logical operation across multiple bash tool calls multiplies permission prompts and round-trips for no benefit.

## What the Agent Does vs What the Scripts Do

Keep the split clean — scripts do the brittle multi-file work; the agent does everything else.

| Task | Owner |
|---|---|
| Collect user inputs (use case, batch, sink, etc.) | Agent (`AskQuestion`) |
| Detect platform | Agent (one-liner: `uname -m`, `nvidia-smi`, `/etc/nv_tegra_release`) |
| Write `~/.ngc/config` | Agent (simple `cat > file` + `chmod 600`) |
| Download NGC resources | Agent (one-liner: `ngc registry resource download-version ...`) |
| Verify docker image arch | Agent (`docker manifest inspect ...`) |
| Launch docker | Agent (builds command from `platforms.md` template) |
| Edit simple INI/YAML keys (sink, source list, path placeholders) | Agent (sources `common.sh`, one-line calls to `update_ds_config` / `update_yaml_flat`) |
| Discover NGC resource paths | Agent (one-liner `find` commands) |
| **Update batch size across all files for a usecase** | **Script** — `update_batch_size.sh` (multi-file orchestration with per-usecase logic) |
| **Build GDINO TensorRT engine** | **Script** — `setup_gdino.sh` (trtexec with 6 dynamic shape params) |
| **Stage Sparse4D configs + run setup.sh** | **Script** — `setup_sparse4d.sh` (multi-step copy + env check + bash invocation) |
| Start the perception app | Agent (single command from `usecases.md`) |

### Why this split

- Agent strength: one-off logic, user interaction, orchestration
- Script strength: deterministic multi-file edits, complex CLI invocations with many args, idempotency
- Anything that would be a **5+ line bash snippet with variable substitution** belongs in a script — too error-prone for the agent to generate inline every time.
