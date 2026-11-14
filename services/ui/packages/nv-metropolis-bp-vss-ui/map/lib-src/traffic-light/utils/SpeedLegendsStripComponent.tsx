// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { Widget } from "common/map";
import SpeedLegendsStrip from "../components/SpeedLegendsStrip/SpeedLegendsStrip";

/**
 * Returns Stateless SpeedLegendsStrip Component
 *
 * @return {object} JSX React Component showing speed legends
 */
export function SpeedLegendsStripComponent() {
  return (
    <Widget left="45vw" top="2.25vw" height="1vw" width="8vw">
      <SpeedLegendsStrip />
    </Widget>
  );
}
