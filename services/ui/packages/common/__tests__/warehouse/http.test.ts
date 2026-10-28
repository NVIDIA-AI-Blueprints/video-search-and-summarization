// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { isAbortError, joinUrl, readJson } from '../../lib-src/warehouse/http';

describe('joinUrl', () => {
  it('appends a plain params object', () => {
    expect(joinUrl('http://example.test/images', { view: 'plan-view', sensorId: 'Camera' })).toBe(
      'http://example.test/images?view=plan-view&sensorId=Camera',
    );
  });

  it('treats abort reason strings as cancellations', () => {
    expect(isAbortError('canceled: card scrolled out of view')).toBe(true);
    expect(isAbortError({ name: 'AbortError' })).toBe(true);
    expect(isAbortError({ name: 'TimeoutError' })).toBe(true);
    expect(isAbortError(new Error('network'))).toBe(false);
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

describe('readJson', () => {
  it('returns parsed JSON from an OK response', async () => {
    const response = new Response(JSON.stringify({ pts: 12 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    await expect(readJson(response)).resolves.toEqual({ pts: 12 });
  });

  it('throws an HTTP error with response payload when the status is not OK', async () => {
    const response = new Response(JSON.stringify({ error: 'nope' }), {
      status: 500,
      statusText: 'Internal Server Error',
      headers: { 'Content-Type': 'application/json' },
    });
    Object.defineProperty(response, 'url', { value: 'http://example.test/kpi' });

    await expect(readJson(response, 'GET')).rejects.toMatchObject({
      status: 500,
      config: { method: 'GET', url: 'http://example.test/kpi' },
      response: { status: 500, data: { error: 'nope' } },
    });
  });
});
