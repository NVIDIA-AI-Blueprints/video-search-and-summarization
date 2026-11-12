// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
/**
 * Throttle interval for cartesian map viz canvas content updates.
 * UI config key: cartesianMapVizContentsRenderUpdateFps (frames per second).
 */

export const DEFAULT_CARTESIAN_MAP_VIZ_CONTENTS_RENDER_UPDATE_FPS = 2;

/** Throttle (ms) when fps is invalid (≤ 0). */
export const CARTESIAN_MAP_VIZ_CANVAS_THROTTLE_MS_FALLBACK = 500;

/**
 * Converts cartesian map viz content FPS to throttle interval in milliseconds.
 *
 * @param {number|undefined|null} fps - From UI config or prop; defaults to {@link DEFAULT_CARTESIAN_MAP_VIZ_CONTENTS_RENDER_UPDATE_FPS}
 * @returns {number} Milliseconds between canvas content updates
 */
export function getCartesianMapVizCanvasThrottleMsFromFps(fps) {
  const effectiveFps =
    fps ?? DEFAULT_CARTESIAN_MAP_VIZ_CONTENTS_RENDER_UPDATE_FPS;
  return effectiveFps > 0
    ? Math.round(1000 / effectiveFps)
    : CARTESIAN_MAP_VIZ_CANVAS_THROTTLE_MS_FALLBACK;
}

/**
 * Reads FPS from app config (e.g. Redux appConfig loaded from default-ui-config.json).
 *
 * @param {object|undefined|null} appConfig
 * @returns {number} Milliseconds between canvas content updates
 */
export function getCartesianMapVizCanvasThrottleMsFromAppConfig(appConfig) {
  return getCartesianMapVizCanvasThrottleMsFromFps(
    appConfig?.cartesianMapVizContentsRenderUpdateFps
  );
}
