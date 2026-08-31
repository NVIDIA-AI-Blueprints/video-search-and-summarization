// SPDX-License-Identifier: MIT
import { act, renderHook, waitFor } from '@testing-library/react';
import { useCvAlertsVerificationConfigs } from '../../lib-src/hooks/useCvAlertsVerificationConfigs';

const response = (body: unknown, ok = true, status = 200, statusText = 'OK') =>
  Promise.resolve({
    ok,
    status,
    statusText,
    json: () => Promise.resolve(body),
  } as Response);

const sample = {
  alert_type: 'FOV Count Violation',
  output_category: 'Ladder PPE Violation',
  system_prompt: 'You are helpful.',
  prompt: 'Is anyone missing PPE?',
  enrichment_prompt: null,
  created_at: '2026-08-25T00:00:00Z',
  updated_at: '2026-08-25T00:00:00Z',
};

describe('useCvAlertsVerificationConfigs', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('lists verification configs from the configured alert bridge', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'success', configs: [sample], count: 1 }),
    });
    const { result } = renderHook(() =>
      useCvAlertsVerificationConfigs({ alertsApiUrl: 'http://alerts.test/api/v1/' }),
    );
    await waitFor(() => expect(result.current.configs).toEqual([sample]));
    expect(global.fetch).toHaveBeenCalledWith(
      'http://alerts.test/api/v1/verification/config',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('creates, updates, and deletes a config using alert_type as its encoded key', async () => {
    const updated = { ...sample, prompt: 'Updated prompt' };
    global.fetch = jest
      .fn()
      .mockImplementationOnce(() => response({ configs: [], count: 0 }))
      .mockImplementationOnce(() => response(sample, true, 201, 'Created'))
      .mockImplementationOnce(() => response(updated))
      .mockImplementationOnce(() => response({ status: 'success', message: 'deleted' }));

    const { result } = renderHook(() =>
      useCvAlertsVerificationConfigs({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.createConfig({
        alert_type: sample.alert_type,
        output_category: sample.output_category,
        prompt: sample.prompt,
        enrichment_prompt: null,
      });
    });
    expect(result.current.configs).toEqual([sample]);

    await act(async () => {
      await result.current.updateConfig(sample.alert_type, { prompt: updated.prompt });
    });
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://alerts.test/api/v1/verification/config/FOV%20Count%20Violation',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ prompt: updated.prompt }),
      }),
    );
    expect(result.current.configs).toEqual([updated]);

    await act(async () => {
      await result.current.deleteConfig(sample.alert_type);
    });
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://alerts.test/api/v1/verification/config/FOV%20Count%20Violation',
      { method: 'DELETE' },
    );
    expect(result.current.configs).toEqual([]);
  });

  it('surfaces duplicate and validation error messages', async () => {
    global.fetch = jest
      .fn()
      .mockImplementationOnce(() => response({ configs: [], count: 0 }))
      .mockImplementationOnce(() =>
        response(
          { status: 'error', message: "Config 'collision' already exists" },
          false,
          409,
          'Conflict',
        ),
      )
      .mockImplementationOnce(() =>
        response(
          { detail: [{ loc: ['body', 'prompt'], msg: 'String should have at least 1 character' }] },
          false,
          422,
          'Unprocessable Entity',
        ),
      );
    const { result } = renderHook(() =>
      useCvAlertsVerificationConfigs({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(
      result.current.createConfig({ alert_type: 'collision', prompt: 'p' }),
    ).rejects.toThrow("Config 'collision' already exists");
    await expect(
      result.current.updateConfig('collision', { prompt: '' }),
    ).rejects.toThrow('prompt: String should have at least 1 character');
  });

  it('reports durable storage outages from list requests', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: () =>
        Promise.resolve({
          status: 'error',
          message: 'Alert config backend is temporarily unavailable; please retry.',
        }),
    });
    const { result } = renderHook(() =>
      useCvAlertsVerificationConfigs({ alertsApiUrl: 'http://alerts.test/api/v1' }),
    );
    await waitFor(() =>
      expect(result.current.error).toBe(
        'Alert config backend is temporarily unavailable; please retry.',
      ),
    );
  });
});
