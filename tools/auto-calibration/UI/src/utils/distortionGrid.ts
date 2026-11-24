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

/**
 * Forward-distortion grid math (matches core/rectification/distortion_models.py).
 */

export type DistortionModel = 'simple_radial' | 'radial' | 'simple_divisional';

export function radialScale(
  r2: number,
  model: DistortionModel,
  k1: number,
  k2: number,
): number {
  if (model === 'simple_radial') return 1.0 + k1 * r2;
  if (model === 'radial') return 1.0 + k1 * r2 + k2 * r2 * r2;
  if (model === 'simple_divisional') {
    const denom = 2.0 * k1 * r2;
    if (Math.abs(denom) < 1e-30) return 1.0;
    let inside = 1.0 - 4.0 * k1 * r2;
    if (inside < 0) inside = 0;
    return (1.0 - Math.sqrt(inside)) / denom;
  }
  return 1.0;
}

export function drawDistortionGrid(
  ctx: CanvasRenderingContext2D,
  lines: number[][][],
  cx: number,
  cy: number,
  scale: number,
  model: DistortionModel,
  k1: number,
  k2: number,
  rotationDeg: number,
): void {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  const rot = (rotationDeg * Math.PI) / 180;
  const cr = Math.cos(rot);
  const sr = Math.sin(rot);

  ctx.strokeStyle = 'rgba(255,0,255,0.9)';
  ctx.lineWidth = 2;
  for (const line of lines) {
    ctx.beginPath();
    for (let i = 0; i < line.length; i++) {
      const xu = line[i][0];
      const yu = line[i][1];
      const xn0 = xu - cx;
      const yn0 = yu - cy;
      const xn = xn0 * cr - yn0 * sr;
      const yn = xn0 * sr + yn0 * cr;
      const r2 = xn * xn + yn * yn;
      const sc = radialScale(r2, model, k1, k2);
      const xd = (xn * sc + cx) * scale;
      const yd = (yn * sc + cy) * scale;
      if (i === 0) ctx.moveTo(xd, yd);
      else ctx.lineTo(xd, yd);
    }
    ctx.stroke();
  }

  ctx.strokeStyle = 'rgba(255,0,0,0.9)';
  const mx = cx * scale;
  const my = cy * scale;
  ctx.beginPath();
  ctx.moveTo(mx - 8, my);
  ctx.lineTo(mx + 8, my);
  ctx.moveTo(mx, my - 8);
  ctx.lineTo(mx, my + 8);
  ctx.stroke();
}

export function defaultParamsFromCamera(
  cam: {
    committed: DistortionParamsLike | null;
    auto_estimate: DistortionParamsLike | null;
  },
): DistortionParamsLike {
  const src = cam.committed ?? cam.auto_estimate;
  if (src) {
    const model = (src.model as DistortionModel);
    const allowed: DistortionModel[] = ['simple_radial', 'radial', 'simple_divisional'];
    return {
      model: allowed.includes(model) ? model : 'simple_divisional',
      k1: src.k1,
      k2: src.k2 ?? 0,
    };
  }
  return { model: 'simple_divisional', k1: 0, k2: 0 };
}

export interface DistortionParamsLike {
  model: DistortionModel;
  k1: number;
  k2: number;
}

export function paramsMatchAutoModel(
  model: DistortionModel,
  autoModel: DistortionModel | null | undefined,
): boolean {
  if (!autoModel) return model === 'simple_radial';
  return model === autoModel;
}
