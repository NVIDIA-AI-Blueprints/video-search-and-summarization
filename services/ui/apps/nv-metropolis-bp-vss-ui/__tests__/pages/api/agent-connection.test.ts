// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { NextApiRequest, NextApiResponse } from 'next';
import { agentAdapterHandler, getAgentAdapterService, resetAgentAdapterForTests } from '../../../utils/server/agentAdapter';
import { ConnectorError } from '../../../utils/server/agentAdapter/connectors/base';
import { OpenClawConnector } from '../../../utils/server/agentAdapter/connectors/openClaw';

const keys = ['AGENT_ADAPTER_ENABLED', 'AGENT_BACKEND_PROTOCOL', 'AGENT_BACKEND_URL', 'AGENT_BACKEND_TOKEN'] as const;

const response = () => {
  const res = {
    statusCode: 200,
    body: undefined as unknown,
    setHeader: jest.fn(),
    status(code: number) { this.statusCode = code; return this; },
    json(body: unknown) { this.body = body; return this; },
  };
  return res as unknown as NextApiResponse & typeof res;
};

const request = (path: string, method = 'GET', token?: string) => ({
  method,
  query: { path: [path] },
  headers: token ? { 'x-vss-gateway-token': token } : {},
}) as unknown as NextApiRequest;

describe('NemoClaw runtime token', () => {
  const original = new Map<string, string | undefined>();
  beforeEach(() => {
    for (const key of keys) original.set(key, process.env[key]);
    process.env.AGENT_ADAPTER_ENABLED = 'true';
    process.env.AGENT_BACKEND_PROTOCOL = 'openclaw-ws';
    process.env.AGENT_BACKEND_URL = 'ws://host.docker.internal:18790';
    delete process.env.AGENT_BACKEND_TOKEN;
    resetAgentAdapterForTests();
  });
  afterEach(() => {
    jest.restoreAllMocks();
    resetAgentAdapterForTests();
    for (const key of keys) {
      const value = original.get(key);
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    original.clear();
  });

  it('asks for a token before allowing agent runs', async () => {
    const status = response();
    await agentAdapterHandler(request('connection'), status);
    expect(status.body).toEqual({ state: 'token_required' });
    const run = response();
    await agentAdapterHandler(request('runs', 'POST'), run);
    expect(run.statusCode).toBe(401);
    expect(run.body).toMatchObject({ error: { code: 'gateway_token_required' } });
  });

  it('separates relay failures from rejected credentials', async () => {
    const probe = jest.spyOn(OpenClawConnector.prototype, 'checkConnection');
    probe.mockRejectedValueOnce(new ConnectorError('unreachable', 'backend_unreachable'));
    const unavailable = response();
    await agentAdapterHandler(request('connection', 'GET', 'first-token'), unavailable);
    expect(unavailable.body).toEqual({ state: 'unreachable' });

    probe.mockRejectedValueOnce(new ConnectorError('rejected', 'backend_auth_error'));
    const rejected = response();
    await agentAdapterHandler(request('connection', 'GET', 'first-token'), rejected);
    expect(rejected.body).toEqual({ state: 'authentication_failed' });

    probe.mockResolvedValueOnce();
    const connected = response();
    await agentAdapterHandler(request('connection', 'GET', 'first-token'), connected);
    expect(connected.body).toEqual({ state: 'connected' });
    expect(JSON.stringify(connected.body)).not.toContain('first-token');
  });

  it('keeps an existing server-provided token working without browser input', async () => {
    process.env.AGENT_BACKEND_TOKEN = 'server-token';
    jest.spyOn(OpenClawConnector.prototype, 'checkConnection').mockResolvedValue();
    const status = response();
    await agentAdapterHandler(request('connection'), status);
    expect(status.body).toEqual({ state: 'connected' });
  });

  it('keeps run stores separate for different browser tokens', () => {
    const first = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'first-token' });
    const again = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'first-token' });
    const second = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'second-token' });
    expect(first).toBe(again);
    expect(second).not.toBe(first);
  });

  it('does not allocate a run store for a rejected token', async () => {
    jest.spyOn(OpenClawConnector.prototype, 'checkConnection')
      .mockRejectedValue(new ConnectorError('rejected', 'backend_auth_error'));
    const run = response();
    await agentAdapterHandler(request('runs', 'POST', 'rejected-token'), run);
    expect(run.statusCode).toBe(401);
    expect(globalThis.__vssEmbeddedAgentAdapterSessions?.size ?? 0).toBe(0);
  });
});
