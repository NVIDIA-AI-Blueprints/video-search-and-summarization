// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { vstHttpOrigin } from '../../lib-src/warehouse/mdat/components/generic/DashVideo/vstHttpOrigin';

describe('warehouse DASH origin', () => {
  it('uses the VST origin, not the /vst/api/v1 media base path', () => {
    expect(vstHttpOrigin('http://10.86.13.219:7777/vst/api/v1')).toBe(
      'http://10.86.13.219:7777',
    );
  });
});
