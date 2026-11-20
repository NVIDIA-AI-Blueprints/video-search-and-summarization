// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { vstHttpOrigin } from '../../lib-src/common/components/generic/DashVideo/vstHttpOrigin';

describe('warehouse DASH origin', () => {
  const vstMedia = 'http://vst.example.test:7777/vst/api/v1';

  it('uses the VST origin when the page already shares it', () => {
    expect(vstHttpOrigin(vstMedia, 'http://vst.example.test:7777')).toBe(
      'http://vst.example.test:7777',
    );
  });

  it('uses the page origin when VST is on another host', () => {
    expect(vstHttpOrigin(vstMedia, 'http://localhost:3005')).toBe(
      'http://localhost:3005',
    );
  });

  it('keeps the VST origin when there is no page origin (server / tests)', () => {
    expect(vstHttpOrigin(vstMedia, '')).toBe('http://vst.example.test:7777');
  });
});
