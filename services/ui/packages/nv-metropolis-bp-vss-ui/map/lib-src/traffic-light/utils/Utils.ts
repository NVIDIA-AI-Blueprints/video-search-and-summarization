// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck

/**
 * Converts mph speed to corresponding color code for depicting traffic
 *
 * @param {number} speed speed value
 * @return {string} color hex code string
 */
export function SpeedToTrafficColor(speed) {
  if (speed < 0)
    return "#A0A0A0"; // gray
  else if (speed <= 10)
    return "#E60000"; // red
  else if (speed <= 20)
    return "#F08080"; // light red
  else if (speed <= 30)
    return "#FFA769"; // orange
  else if (speed <= 45)
    return "#98FB98"; // light green
  else return "#3CB371"; // green
}
