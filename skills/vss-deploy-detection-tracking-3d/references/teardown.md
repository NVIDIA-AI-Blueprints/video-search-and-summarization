# Teardown Standalone RT-CV-3D MV3DT

Load this reference when the user asks to stop, tear down, clean, reset, or tear down everything for the standalone RT-CV-3D MV3DT deployment.

## File-Input Post-Run Cleanup

For `INPUT_MODE=file`, `vss-rtvi-cv-mv3dt` exits after EOS by design. Before cleanup, classify the run:

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
status="$(docker inspect --format '{{.State.Status}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
exit_code="$(docker inspect --format '{{.State.ExitCode}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
if [ "${status}" = exited ] && [ "${exit_code}" = 0 ] && docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -q 'App run successful'; then
  echo 'file-input run completed successfully; safe to stop remaining support services after artifact verification'
else
  echo "perception status=${status:-missing} exit=${exit_code:-unknown}; inspect logs before cleanup"
fi
```

After `video-output/`, `bev-output/`, and Kafka offset evidence are verified, stop remaining standalone support services for a completed file-input run unless the user asked to keep them running. Do not delete artifacts during post-run cleanup.

## Teardown Scope

Treat `teardown everything` as all runtime resources started by this standalone skill:

- host-side BEV visualizer processes started from this app and tracked in `generated/run-state/bev-visualizer.pid`
- standalone compose containers from `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`
- generated runtime artifacts only when the user explicitly asks to delete generated state, outputs, or local artifacts

Do not delete models, user videos, user-provided calibration/map assets, NGC credentials, unrelated Docker images/volumes, warehouse services, AMC, or VIOS unless the user explicitly names those targets. If AMC/VIOS were started only as calibration prerequisites, route their teardown through their owning skills and do not stop pre-existing shared services.

## Stop Host-Side BEV Visualizer

Stop the BEV visualizer before compose down so saved MP4 output finalizes. Never signal a PID only because it appears in the PID file; validate the tracked process identity first.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
PID_FILE="${RUN_STATE_DIR}/bev-visualizer.pid"
if [ -f "${PID_FILE}" ]; then
  pid="$(cat "${PID_FILE}")"
  if ! printf '%s' "${pid}" | grep -Eq '^[0-9]+$'; then
    echo "WARN: ignoring invalid BEV PID: ${pid}"
  elif ! kill -0 "${pid}" 2>/dev/null; then
    echo "BEV visualizer PID ${pid} is not active"
  else
    current_cwd="$(readlink -f /proc/"${pid}"/cwd 2>/dev/null || true)"
    expected_cwd="$(cat "${RUN_STATE_DIR}/bev-visualizer.cwd" 2>/dev/null || true)"
    current_cmd="$(tr '\0' ' ' < /proc/"${pid}"/cmdline 2>/dev/null || true)"
    expected_cmd="$(cat "${RUN_STATE_DIR}/bev-visualizer.cmdline" 2>/dev/null || true)"
    current_start="$(awk '{print $22}' /proc/"${pid}"/stat 2>/dev/null || true)"
    expected_start="$(cat "${RUN_STATE_DIR}/bev-visualizer.start_ticks" 2>/dev/null || true)"

    case "${current_cmd}" in
      *kafka_bev_visualizer.py*|*kafka_fused_bev_visualizer.py*|*bev-visualizer.sh*) cmd_ok=1 ;;
      *) cmd_ok=0 ;;
    esac
    if [ -n "${expected_cmd}" ] && [ "${current_cmd}" != "${expected_cmd}" ]; then cmd_ok=0; fi
    cwd_ok=0
    if [ -n "${current_cwd}" ] && [ "${current_cwd}" = "${RTCV3D_APP}" ]; then cwd_ok=1; fi
    if [ -n "${expected_cwd}" ] && [ "${current_cwd}" = "${expected_cwd}" ]; then cwd_ok=1; fi
    start_ok=1
    if [ -n "${expected_start}" ] && [ -n "${current_start}" ] && [ "${current_start}" != "${expected_start}" ]; then start_ok=0; fi

    if [ "${cmd_ok}" = 1 ] && [ "${cwd_ok}" = 1 ] && [ "${start_ok}" = 1 ]; then
      kill -TERM "${pid}" 2>/dev/null || true
      deadline=$((SECONDS + 20))
      while kill -0 "${pid}" 2>/dev/null && [ "${SECONDS}" -lt "${deadline}" ]; do
        sleep 1
      done
      if kill -0 "${pid}" 2>/dev/null; then
        echo "WARN: BEV visualizer did not stop after SIGTERM: pid=${pid}"
      else
        echo "BEV visualizer stopped: pid=${pid}"
        rm -f "${PID_FILE}" "${RUN_STATE_DIR}/bev-visualizer.cwd"           "${RUN_STATE_DIR}/bev-visualizer.cmdline"           "${RUN_STATE_DIR}/bev-visualizer.start_ticks"           "${RUN_STATE_DIR}/bev-visualizer.group"
      fi
    else
      echo "WARN: not stopping PID ${pid}; identity check failed (cmd_ok=${cmd_ok} cwd_ok=${cwd_ok} start_ok=${start_ok})"
    fi
  fi
fi
```

If no PID file exists but a BEV window or recording is visibly running, ask before killing untracked processes. Never use broad `pkill` patterns.

## Stop The Stack

Stop containers started by the standalone compose file:

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}/docker"
docker compose --profile "*" down
```

This stops/removes standalone compose resources. It does not delete models, calibration files, generated configs, or visualization outputs.

## Verify Stop

```bash
docker ps --filter status=running --format '{{.Names}}'   | grep -E '^(vss-rtvi-cv-mv3dt|vss-rtvi-cv-bev-fusion|vss-mosquitto-mv3dt|kafka|kafka-topic-init)$'   || echo 'standalone RT-CV-3D containers stopped'
```

## Clean Generated Runtime State

Run this only when the user explicitly asks to delete generated state, saved outputs, or local artifacts in addition to stopping services. If the user only says `teardown everything`, stop runtime resources first and ask before deleting these paths because they include run outputs:

```text
${RTCV3D_APP}/generated/
${RTCV3D_APP}/video-output/
${RTCV3D_APP}/bev-output/
${RTCV3D_APP}/utils/venv/
```

If the user approves generated-state cleanup:

```bash
cd "${RTCV3D_APP}"
rm -rf generated video-output bev-output utils/venv
```

Do not delete `MODELS_DIR`, user videos, user calibration files, `map.png`, or `transforms.yml` unless the user explicitly names those targets.

## Reset Bundled Broker Data

The bundled Kafka service in this standalone compose uses container-local state. A normal `docker compose --profile "*" down` removes the broker container but not pulled images. Use image or volume pruning only when the user explicitly asks for broader Docker cleanup.

## AMC And VIOS

This standalone RT-CV-3D deployment does not include AMC or VIOS. If calibration handoff started AMC, tear it down through `vss-generate-video-calibration`. If this workflow used `vss-manage-video-io-storage` to bring up VIOS as a missing RTSP-calibration prerequisite, tear that VIOS deployment down through the VIOS skill only when the user asks.

Do not stop pre-existing or unrelated VIOS services just because RTSP calibration used them.
