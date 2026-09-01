// SPDX-License-Identifier: MIT
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CreateAlertRulesView } from '../../lib-src/components/CreateAlertRulesView';
import { AlertRulesType, VerificationAlertConfig } from '../../lib-src/types';

jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, kind, ...rest }: any, ref: any) =>
      React.createElement(
        'button',
        { ...rest, ref, 'data-kind': kind, 'data-foundation': 'Button' },
        children,
      ),
    ),
    TextInput: React.forwardRef(({ onValueChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        'data-foundation': 'TextInput',
        onChange: (e: any) => onValueChange?.(e.target.value),
      }),
    ),
  };
});

const jsonResponse = (body: unknown, ok = true, status = 200, statusText = 'OK') =>
  Promise.resolve({
    ok,
    status,
    statusText,
    json: () => Promise.resolve(body),
  } as Response);

const ControlledView = () => {
  const [kind, setKind] = React.useState<AlertRulesType>('real-time');
  return (
    <CreateAlertRulesView
      isDark={false}
      activeKind={kind}
      onActiveKindChange={setKind}
      onAddNew={jest.fn()}
      alertsApiUrl="http://alerts.test/api/v1"
      vstApiUrl="http://vst.test"
    />
  );
};

describe('CV Alerts Verification rules tab', () => {
  const originalFetch = global.fetch;
  let configs: VerificationAlertConfig[];

  beforeEach(() => {
    configs = [];
    global.fetch = jest.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/realtime')) {
        return jsonResponse({ status: 'success', rules: [], count: 0 });
      }
      if (url.endsWith('/verification/config') && !init?.method) {
        return jsonResponse({ status: 'success', configs, count: configs.length });
      }
      if (url.endsWith('/verification/config') && init?.method === 'POST') {
        const body = JSON.parse(init.body as string);
        const created = {
          ...body,
          created_at: '2026-08-25T00:00:00Z',
          updated_at: '2026-08-25T00:00:00Z',
        };
        configs = [created];
        return jsonResponse(created, true, 201, 'Created');
      }
      if (init?.method === 'PUT') {
        const update = JSON.parse(init.body as string);
        configs = [{ ...configs[0], ...update, updated_at: '2026-08-25T00:01:00Z' }];
        return jsonResponse(configs[0]);
      }
      if (init?.method === 'DELETE') {
        configs = [];
        return jsonResponse({ status: 'success', message: 'deleted' });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('hides disabled Manage Alerts kinds', () => {
    render(
      <CreateAlertRulesView
        isDark={false}
        activeKind="verification"
        onAddNew={jest.fn()}
        alertsApiUrl="http://alerts.test/api/v1"
        enableRealtimeAlerts={false}
        enableCvAlertsVerification
      />,
    );

    expect(screen.queryByRole('tab', { name: 'Real-time Alerts' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'CV Alerts Verification' })).toBeInTheDocument();
    expect(screen.getByTestId('add-verification-rule-inline')).toHaveAttribute(
      'data-foundation',
      'Button',
    );
    expect(screen.getByTestId('verification-filter')).toHaveAttribute(
      'data-foundation',
      'TextInput',
    );
  });

  it('switches tabs and creates, edits, then deletes a verification rule', async () => {
    render(<ControlledView />);
    fireEvent.click(screen.getByRole('tab', { name: 'CV Alerts Verification' }));
    expect(screen.getByTestId('verification-alerts-tab')).toBeVisible();
    expect(screen.getByTestId('create-alert-kind-verification')).toHaveAttribute(
      'aria-selected',
      'true',
    );

    await waitFor(() =>
      expect(screen.getByText(/No rules found/i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('add-verification-rule-inline'));
    fireEvent.change(screen.getByLabelText('Alert type'), {
      target: { value: 'FOV Count Violation' },
    });
    fireEvent.change(screen.getByLabelText('Output category'), {
      target: { value: 'Ladder PPE Violation' },
    });
    fireEvent.change(screen.getByLabelText('User prompt'), {
      target: { value: 'Is anyone missing PPE? Answer yes or no.' },
    });
    fireEvent.click(screen.getByTestId('verification-draft-save'));

    await waitFor(() =>
      expect(screen.getByTestId('verification-config-row')).toBeInTheDocument(),
    );
    const post = (global.fetch as jest.Mock).mock.calls.find(
      (call: [string, RequestInit?]) => call[1]?.method === 'POST',
    );
    expect(JSON.parse(post[1].body)).toEqual({
      alert_type: 'FOV Count Violation',
      output_category: 'Ladder PPE Violation',
      prompt: 'Is anyone missing PPE? Answer yes or no.',
    });
    expect(JSON.parse(post[1].body)).not.toHaveProperty('system_prompt');
    expect(JSON.parse(post[1].body)).not.toHaveProperty('enrichment_prompt');
    expect(screen.queryByLabelText('System prompt')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('verification-edit'));
    expect(screen.queryByLabelText('System prompt')).not.toBeInTheDocument();
    expect(screen.queryByTestId('verification-optional-row')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('User prompt'), {
      target: { value: 'Updated verification prompt' },
    });
    expect(screen.getByLabelText('Alert type')).toHaveAttribute('readonly');
    fireEvent.click(screen.getByTestId('verification-edit-save'));
    await waitFor(() =>
      expect(screen.getByText('Updated verification prompt')).toBeInTheDocument(),
    );
    const put = (global.fetch as jest.Mock).mock.calls.find(
      (call: [string, RequestInit?]) => call[1]?.method === 'PUT',
    );
    expect(JSON.parse(put[1].body)).toEqual({
      output_category: 'Ladder PPE Violation',
      prompt: 'Updated verification prompt',
    });
    expect(JSON.parse(put[1].body)).not.toHaveProperty('enrichment_prompt');

    fireEvent.click(await screen.findByTestId('verification-delete'));
    fireEvent.click(screen.getByTestId('verification-confirm-delete'));
    await waitFor(() =>
      expect(screen.queryByTestId('verification-config-row')).not.toBeInTheDocument(),
    );
  });

  it('validates required fields before calling the API', async () => {
    render(<ControlledView />);
    fireEvent.click(screen.getByRole('tab', { name: 'CV Alerts Verification' }));
    fireEvent.click(screen.getByTestId('add-verification-rule-inline'));
    fireEvent.click(screen.getByTestId('verification-draft-save'));
    expect(
      await screen.findByText('Alert type and user prompt are required.'),
    ).toBeInTheDocument();
    expect(
      (global.fetch as jest.Mock).mock.calls.some(
        (call: [string, RequestInit?]) => call[1]?.method === 'POST',
      ),
    ).toBe(false);
  });

  it('keeps optional fields in a collapsible row section', async () => {
    render(<ControlledView />);
    fireEvent.click(screen.getByRole('tab', { name: 'CV Alerts Verification' }));
    fireEvent.click(screen.getByTestId('add-verification-rule-inline'));
    expect(screen.queryByTestId('verification-optional-row')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Enrichment prompt')).not.toBeInTheDocument();

    const toggle = screen.getByTestId('verification-optional-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(screen.getByTestId('verification-optional-row')).toBeInTheDocument();
    expect(screen.getByTestId('verification-optional-toggle')).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    fireEvent.change(screen.getByLabelText('Alert type'), {
      target: { value: 'FOV Count Violation' },
    });
    fireEvent.change(screen.getByLabelText('User prompt'), {
      target: { value: 'Is anyone missing PPE?' },
    });
    fireEvent.change(screen.getByLabelText('Enrichment prompt'), {
      target: { value: 'Describe the scene in detail.' },
    });
    expect(screen.getByTestId('verification-optional-indicator')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('verification-optional-toggle'));
    expect(screen.queryByTestId('verification-optional-row')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('verification-draft-save'));
    await waitFor(() =>
      expect(screen.getByTestId('verification-config-row')).toBeInTheDocument(),
    );
    const post = (global.fetch as jest.Mock).mock.calls.find(
      (call: [string, RequestInit?]) => call[1]?.method === 'POST',
    );
    expect(JSON.parse(post[1].body)).toEqual({
      alert_type: 'FOV Count Violation',
      output_category: null,
      prompt: 'Is anyone missing PPE?',
      enrichment_prompt: 'Describe the scene in detail.',
    });
  });
});
