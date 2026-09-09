// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { getAgentParamsPosition } from '../lib-src/agentParamsPosition';

describe('getAgentParamsPosition', () => {
  it('opens above a trigger near the bottom of the viewport', () => {
    expect(
      getAgentParamsPosition({
        anchorRect: { top: 700, bottom: 720, right: 390 },
        viewportHeight: 800,
        viewportWidth: 400,
      }),
    ).toEqual({ bottom: 108, right: 10, maxHeight: 684 });
  });

  it('falls back below the trigger when there is not enough room above', () => {
    expect(
      getAgentParamsPosition({
        anchorRect: { top: 50, bottom: 70, right: 390 },
        viewportHeight: 500,
        viewportWidth: 400,
      }),
    ).toEqual({ top: 78, right: 10, maxHeight: 414 });
  });

  it('constrains the panel to the available space in a short viewport', () => {
    expect(
      getAgentParamsPosition({
        anchorRect: { top: 120, bottom: 140, right: 390 },
        viewportHeight: 220,
        viewportWidth: 400,
      }),
    ).toEqual({ bottom: 108, right: 10, maxHeight: 104 });
  });
});
