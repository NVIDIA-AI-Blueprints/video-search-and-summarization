// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CreateAlertRulesView } from '../../lib-src/components/CreateAlertRulesView';
import { RealtimeAlertRule } from '../../lib-src/types';

jest.mock('../../lib-src/components/VstStreamThumbnail', () => ({
  VstStreamThumbnail: ({ sensorName }: { sensorName: string }) => (
    <div data-testid="vst-stream-thumbnail">{sensorName}</div>
  ),
}));

const jsonResponse = (body: unknown, ok = true, status = 200, statusText = 'OK') =>
  Promise.resolve({
    ok,
    status,
    statusText,
    json: () => Promise.resolve(body),
  } as Response);

describe('CreateAlertRulesView realtime rules', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('creates a realtime alert rule using the alert API spec fields', async () => {
    let rules: RealtimeAlertRule[] = [];
    global.fetch = jest.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/v1/sensor/list')) {
        return jsonResponse([
          {
            name: 'sample-warehouse-ladder.mp4',
            sensorId: 'vst-sensor-abc',
            state: 'online',
          },
        ]);
      }
      if (init?.method === 'POST') {
        const body = JSON.parse(init.body as string);
        rules = [
          {
            id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
            ...body,
            status: 'active',
            created_at: '2026-04-12T10:30:00+00:00',
          },
        ];
        return jsonResponse(
          {
            status: 'success',
            id: rules[0].id,
            created_at: rules[0].created_at,
            message: 'Realtime alert rule created',
          },
          true,
          201,
          'Created',
        );
      }

      return jsonResponse({ status: 'success', rules, count: rules.length });
    });

    render(
      <CreateAlertRulesView
        isDark={false}
        activeKind="real-time"
        onAddNew={jest.fn()}
        alertsApiUrl="http://alerts.test/api/v1/"
        vstApiUrl="http://vst.test"
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/No real-time alert rules/i)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('add-new-alert-button-inline'));
    fireEvent.change(screen.getByPlaceholderText('rtsp://host:port/path'), {
      target: {
        value:
          '  rtsp://10.86.5.74:31554/nvstream/home/vst/vst_release/streamer_videos/sample-warehouse-ladder.mp4?token=abc  ',
      },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g. collision'), {
      target: { value: '  collision  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Detect any vehicle collisions'), {
      target: { value: '  Detect safety violations with ladder  ' },
    });
    fireEvent.click(screen.getByTestId('realtime-alert-draft-save'));

    await waitFor(() => {
      const postCall = (global.fetch as jest.Mock).mock.calls.find(
        (call: [string, RequestInit?]) => call[1]?.method === 'POST',
      );
      expect(postCall).toBeTruthy();
    });

    const postCall = (global.fetch as jest.Mock).mock.calls.find(
      (call: [string, RequestInit?]) => call[1]?.method === 'POST',
    );
    expect(postCall[0]).toBe('http://alerts.test/api/v1/realtime');
    expect(JSON.parse(postCall[1].body as string)).toEqual({
      live_stream_url:
        'rtsp://10.86.5.74:31554/nvstream/home/vst/vst_release/streamer_videos/sample-warehouse-ladder.mp4?token=abc',
      alert_type: 'collision',
      prompt: 'Detect safety violations with ladder',
      sensor_name: 'sample-warehouse-ladder.mp4',
      sensor_id: 'vst-sensor-abc',
    });

    expect(await screen.findByText('collision')).toBeInTheDocument();
    expect(screen.getByText('Detect safety violations with ladder')).toBeInTheDocument();
    expect(screen.getByText('sample-warehouse-ladder.mp4')).toBeInTheDocument();
  });

  it('deletes realtime alert rules by alert rule id after confirmation', async () => {
    const rule: RealtimeAlertRule = {
      id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
      live_stream_url: 'rtsp://localhost:8554/media/video1',
      alert_type: 'collision',
      prompt: 'Detect any vehicle collisions',
      status: 'active',
    };
    let rules: RealtimeAlertRule[] = [rule];
    global.fetch = jest.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        rules = [];
        return jsonResponse({
          status: 'success',
          id: rule.id,
          message: 'Realtime alert rule deleted',
        });
      }

      return jsonResponse({ status: 'success', rules, count: rules.length });
    });

    render(
      <CreateAlertRulesView
        isDark={false}
        activeKind="real-time"
        onAddNew={jest.fn()}
        alertsApiUrl="http://alerts.test/api/v1"
      />,
    );

    expect(await screen.findByText('Detect any vehicle collisions')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(`Delete alert rule ${rule.id}`));
    fireEvent.click(screen.getByLabelText(`Confirm delete of alert rule ${rule.id}`));

    await waitFor(() =>
      expect(screen.queryByText('Detect any vehicle collisions')).not.toBeInTheDocument(),
    );

    const deleteCall = (global.fetch as jest.Mock).mock.calls.find(
      (call: [string, RequestInit?]) => call[1]?.method === 'DELETE',
    );
    expect(deleteCall[0]).toBe(`http://alerts.test/api/v1/realtime/${rule.id}`);
  });
});
