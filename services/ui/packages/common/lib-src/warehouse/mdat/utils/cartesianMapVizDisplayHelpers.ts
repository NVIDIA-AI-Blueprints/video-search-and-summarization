// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
/**
 * Shared layout / display throttling for cartesian map viz (resize, display snapshots, location state).
 */

import {
  getCartesianMapVizCanvasThrottleMsFromAppConfig,
  getCartesianMapVizCanvasThrottleMsFromFps,
} from "./cartesianMapVizCanvasThrottle";

/** Throttle for resize / coordinate-refresh → forceUpdate (ms). */
export const DEFAULT_MAP_VIZ_LAYOUT_REDRAW_THROTTLE_MS = 200;

/**
 * Throttle repeated triggers (e.g. onResize) to one `task` after `delayMs`.
 * `onBeforeSchedule` runs on every `schedule()` call, even when a timeout is already pending
 * (e.g. invalidate caches before coalescing the rerender).
 *
 * @param {object} options
 * @param {number} [options.delayMs]
 * @param {() => void} options.task
 * @param {() => void} [options.onBeforeSchedule]
 * @returns {{ schedule: () => void, dispose: () => void }}
 */
export function createLayoutThrottledTask({
  delayMs = DEFAULT_MAP_VIZ_LAYOUT_REDRAW_THROTTLE_MS,
  task,
  onBeforeSchedule,
} = {}) {
  let timeoutId = null;
  return {
    schedule() {
      onBeforeSchedule?.();
      if (timeoutId != null) return;
      timeoutId = setTimeout(() => {
        timeoutId = null;
        task();
      }, delayMs);
    },
    dispose() {
      if (timeoutId != null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    },
  };
}

/**
 * Layout throttle that calls `component.forceUpdate()` after `delayMs` (default `DEFAULT_MAP_VIZ_LAYOUT_REDRAW_THROTTLE_MS`).
 *
 * @param {{ forceUpdate: () => void }} component - React class instance
 * @param {number} [delayMs]
 * @returns {{ schedule: () => void, dispose: () => void }}
 */
export function createForceUpdateLayoutThrottle(
  component,
  delayMs = DEFAULT_MAP_VIZ_LAYOUT_REDRAW_THROTTLE_MS
) {
  return createLayoutThrottledTask({
    delayMs,
    task: () => component.forceUpdate(),
  });
}

/**
 * Whether to rebuild a canvas display snapshot (e.g. converted dot positions).
 *
 * @param {boolean} snapshotExists - True if a snapshot is already stored
 * @param {number} now - Date.now()
 * @param {number} lastUpdateTimeMs - Timestamp of last snapshot build
 * @param {number} throttleMs - Min interval between snapshot rebuilds
 */
export function shouldRefreshCanvasDisplaySnapshot(
  snapshotExists,
  now,
  lastUpdateTimeMs,
  throttleMs
) {
  return !snapshotExists || now - lastUpdateTimeMs >= throttleMs;
}

/**
 * Like `shouldRefreshCanvasDisplaySnapshot` but derives `throttleMs` from Redux/UI `appConfig`.
 */
export function shouldRefreshCanvasDisplaySnapshotFromAppConfig(
  appConfig,
  snapshotExists,
  now,
  lastUpdateTimeMs
) {
  return shouldRefreshCanvasDisplaySnapshot(
    snapshotExists,
    now,
    lastUpdateTimeMs,
    getCartesianMapVizCanvasThrottleMsFromAppConfig(appConfig)
  );
}

/**
 * Like `shouldRefreshCanvasDisplaySnapshot` but derives `throttleMs` from `mapVizContentsRenderUpdateFps` prop.
 */
export function shouldRefreshCanvasDisplaySnapshotFromFps(
  mapVizContentsRenderUpdateFps,
  snapshotExists,
  now,
  lastUpdateTimeMs
) {
  return shouldRefreshCanvasDisplaySnapshot(
    snapshotExists,
    now,
    lastUpdateTimeMs,
    getCartesianMapVizCanvasThrottleMsFromFps(mapVizContentsRenderUpdateFps)
  );
}

/**
 * Throttles React state updates for `objectsLocationData` (live / replay map dots).
 *
 * @param {object} options
 * @param {() => number} options.getThrottleMs - Returns throttle interval in ms
 * @param {(partialState: object) => void} options.setState - React Component#setState
 * @returns {{ schedule: (data: *) => void, clearScheduledUpdate: () => void, resetScheduleClock: () => void }}
 */
export function createThrottledObjectsLocationState({ getThrottleMs, setState }) {
  let pending = null;
  let lastTime = 0;
  let timeoutId = null;

  const flush = () => {
    setState({ objectsLocationData: pending });
    lastTime = Date.now();
    timeoutId = null;
  };

  const schedule = (data) => {
    pending = data;
    const now = Date.now();
    const elapsed = now - lastTime;
    const throttleMs = getThrottleMs();
    if (elapsed >= throttleMs || lastTime === 0) {
      lastTime = now;
      setState({ objectsLocationData: data });
      if (timeoutId != null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    } else if (timeoutId == null) {
      timeoutId = setTimeout(flush, throttleMs - elapsed);
    }
  };

  const clearScheduledUpdate = () => {
    if (timeoutId != null) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
  };

  const resetScheduleClock = () => {
    lastTime = 0;
  };

  return { schedule, clearScheduledUpdate, resetScheduleClock };
}
