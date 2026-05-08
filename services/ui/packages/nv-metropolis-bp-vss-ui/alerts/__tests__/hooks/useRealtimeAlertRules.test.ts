// SPDX-License-Identifier: MIT
import { renderHook, waitFor, act } from '@testing-library/react';
import { useRealtimeAlertRules } from '../../lib-src/hooks/useRealtimeAlertRules';
import type { RealtimeAlertRule } from '../../lib-src/types';

const jsonResponse = (body: unknown, ok = true, status = 200, statusText = 'OK') =>
  Promise.resolve({
    ok,
    status,
    statusText,
    json: () => Promise.resolve(body),
  } as Response);

describe('useRealtimeAlertRules', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('fetches realtime rules from the configured API base URL', async () => {
    const rules = [
      {
        id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
        live_stream_url: 'rtsp://localhost:8554/media/video1',
        alert_type: 'collision',
        prompt: 'Detect any vehicle collisions',
        status: 'active',
        created_at: '2026-04-12T10:30:00+00:00',
      },
    ];
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'success', rules, count: 1 }),
    });

    const { result } = renderHook(() =>
      useRealtimeAlertRules({ alertsApiUrl: 'http://alerts.test/api/v1/' }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(global.fetch).toHaveBeenCalledWith(
      'http://alerts.test/api/v1/realtime',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.current.rules).toEqual(rules);
    expect(result.current.error).toBeNull();
  });

  it('creates one alert type per rule and refreshes to return server defaults', async () => {
    const createdRule = {
      id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
      live_stream_url: 'rtsp://localhost:8554/media/video1',
      alert_type: 'collision',
      prompt: 'Detect any vehicle collisions',
      model: 'llama-3.2-90b-vision-instruct',
      chunk_duration: 30,
      status: 'active',
      created_at: '2026-04-12T10:30:00+00:00',
    };
    global.fetch = jest
      .fn()
      .mockImplementationOnce(() => jsonResponse({ status: 'success', rules: [], count: 0 }))
      .mockImplementationOnce((_url: string, init?: RequestInit) => {
        expect(init?.method).toBe('POST');
        expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
        expect(JSON.parse(init?.body as string)).toEqual({
          live_stream_url: 'rtsp://localhost:8554/media/video1',
          alert_type: 'collision',
          prompt: 'Detect any vehicle collisions',
          sensor_name: 'video1',
        });
        return jsonResponse({
          status: 'success',
          id: createdRule.id,
          created_at: createdRule.created_at,
          message: 'Realtime alert rule created',
        }, true, 201, 'Created');
      })
      .mockImplementationOnce(() =>
        jsonResponse({ status: 'success', rules: [createdRule], count: 1 }),
      );

    const { result } = renderHook(() =>
      useRealtimeAlertRules({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    let returnedRule: RealtimeAlertRule | undefined;
    await act(async () => {
      returnedRule = await result.current.createRule({
        live_stream_url: 'rtsp://localhost:8554/media/video1',
        alert_type: 'collision',
        prompt: 'Detect any vehicle collisions',
        sensor_name: 'video1',
      });
    });

    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://alerts.test/api/v1/realtime',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(global.fetch).toHaveBeenNthCalledWith(3, 'http://alerts.test/api/v1/realtime', {
      signal: undefined,
    });
    expect(returnedRule).toEqual(createdRule);
    expect(result.current.rules).toEqual([createdRule]);
  });

  it('deletes a rule by alert rule id', async () => {
    const rule = {
      id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
      live_stream_url: 'rtsp://localhost:8554/media/video1',
      alert_type: 'collision',
      prompt: 'Detect any vehicle collisions',
      status: 'active',
    };
    global.fetch = jest
      .fn()
      .mockImplementationOnce(() => jsonResponse({ status: 'success', rules: [rule], count: 1 }))
      .mockImplementationOnce(() =>
        jsonResponse({
          status: 'success',
          id: rule.id,
          message: 'Realtime alert rule deleted',
        }),
      );

    const { result } = renderHook(() =>
      useRealtimeAlertRules({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );
    await waitFor(() => expect(result.current.rules).toEqual([rule]));

    await act(async () => {
      await result.current.deleteRule(rule.id);
    });

    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      `http://alerts.test/api/v1/realtime/${rule.id}`,
      { method: 'DELETE' },
    );
    expect(result.current.rules).toEqual([]);
  });

  it('surfaces realtime API error envelope messages', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: () =>
        Promise.resolve({
          status: 'error',
          error: 'validation_failed',
          message: 'Missing live_stream_url, alert_type, or prompt',
        }),
    });

    const { result } = renderHook(() =>
      useRealtimeAlertRules({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );

    await waitFor(() => {
      expect(result.current.error).toBe('Missing live_stream_url, alert_type, or prompt');
    });
  });
});
