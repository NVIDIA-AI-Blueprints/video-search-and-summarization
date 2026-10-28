// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { isCancel, joinUrl } from '../../lib-src/warehouse/http';

describe('joinUrl', () => {
  it('appends a plain params object', () => {
    expect(joinUrl('http://example.test/images', { view: 'plan-view', sensorId: 'Camera' })).toBe(
      'http://example.test/images?view=plan-view&sensorId=Camera',
    );
  });

  it('treats abort reason strings as cancellations', () => {
    expect(isCancel('canceled: card scrolled out of view')).toBe(true);
    expect(isCancel({ name: 'AbortError' })).toBe(true);
    expect(isCancel({ __CANCEL__: true })).toBe(true);
    expect(isCancel(new Error('network'))).toBe(false);
  });

  it('appends URLSearchParams used by CameraView', () => {
    const params = new URLSearchParams();
    params.append('place', 'building=Warehouse/room=Room-1');
    params.append('view', 'plan-view');
    const href = joinUrl('http://example.test/images', params);
    expect(href).toContain('view=plan-view');
    expect(href).toContain('place=');
  });
});
