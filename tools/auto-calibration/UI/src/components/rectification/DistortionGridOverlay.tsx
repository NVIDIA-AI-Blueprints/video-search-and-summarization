/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

import React, { useEffect, useRef } from 'react';
import { Box } from '@mui/material';
import { drawDistortionGrid, type DistortionModel } from '../../utils/distortionGrid';

interface DistortionGridOverlayProps {
  lines: number[][][];
  cx: number;
  cy: number;
  displayWidth: number;
  displayHeight: number;
  nativeWidth: number;
  nativeHeight: number;
  model: DistortionModel;
  k1: number;
  k2: number;
  rotationDeg: number;
  visible: boolean;
}

const DistortionGridOverlay: React.FC<DistortionGridOverlayProps> = ({
  lines,
  cx,
  cy,
  displayWidth,
  displayHeight,
  nativeWidth,
  nativeHeight,
  model,
  k1,
  k2,
  rotationDeg,
  visible,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible || displayWidth <= 0 || displayHeight <= 0) return;
    const scale = displayWidth / nativeWidth;
    canvas.width = Math.round(displayWidth);
    canvas.height = Math.round(displayHeight);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    drawDistortionGrid(ctx, lines, cx, cy, scale, model, k1, k2, rotationDeg);
  }, [
    lines,
    cx,
    cy,
    displayWidth,
    displayHeight,
    nativeWidth,
    nativeHeight,
    model,
    k1,
    k2,
    rotationDeg,
    visible,
  ]);

  if (!visible) return null;

  return (
    <Box
      component="canvas"
      ref={canvasRef}
      sx={{
        position: 'absolute',
        left: 0,
        top: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
      }}
    />
  );
};

export default DistortionGridOverlay;
