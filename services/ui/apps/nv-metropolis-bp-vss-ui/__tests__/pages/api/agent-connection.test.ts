// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { NextApiRequest, NextApiResponse } from 'next';
import { agentAdapterHandler, getAgentAdapterService, resetAgentAdapterForTests } from '../../../utils/server/agentAdapter';
import { ConnectorError } from '../../../utils/server/agentAdapter/connectors/base';
import { OpenClawConnector } from '../../../utils/server/agentAdapter/connectors/openClaw';
import { RunNotFoundError, RunStore, type RunRecord } from '../../../utils/server/agentAdapter/store';

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
  query: { path: path.split('/') },
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

  it('shares one bounded store while keeping token connectors separate', () => {
    const first = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'first-token' });
    const again = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'first-token' });
    const second = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'second-token' });
    expect(first).toBe(again);
    expect(second).not.toBe(first);
    expect(second!.store).toBe(first!.store);
    const input = {
      threadId: 'same-thread',
      input: [{ role: 'user' as const, content: 'hello' }],
      history: [],
      surface: 'vss-ui',
      metadata: {},
    };
    const firstRun = first!.store.create(input, 'same-key', first!.ownerFingerprint).record;
    const secondRun = second!.store.create(input, 'same-key', second!.ownerFingerprint).record;
    expect(secondRun.runId).not.toBe(firstRun.runId);
    expect(() => second!.store.get(firstRun.runId, second!.ownerFingerprint)).toThrow(RunNotFoundError);
  });

  it('does not allocate a run store for a rejected token', async () => {
    jest.spyOn(OpenClawConnector.prototype, 'checkConnection')
      .mockRejectedValue(new ConnectorError('rejected', 'backend_auth_error'));
    const run = response();
    await agentAdapterHandler(request('runs', 'POST', 'rejected-token'), run);
    expect(run.statusCode).toBe(401);
    expect(globalThis.__vssEmbeddedAgentAdapterSessions?.size ?? 0).toBe(0);
  });

  it('serves an accepted token without another gateway handshake', async () => {
    const check = jest.spyOn(OpenClawConnector.prototype, 'checkConnection').mockResolvedValue();
    const first = response();
    await agentAdapterHandler(request('capabilities', 'GET', 'valid-token'), first);
    expect(first.statusCode).toBe(200);
    expect(check).toHaveBeenCalledTimes(1);

    check.mockRejectedValue(new ConnectorError('unreachable', 'backend_unreachable'));
    const later = response();
    await agentAdapterHandler(request('capabilities', 'GET', 'valid-token'), later);
    expect(later.statusCode).toBe(200);
    expect(check).toHaveBeenCalledTimes(1);
  });

  it('rechecks a token after a run reports it rejected', async () => {
    const service = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'revoked-token' });
    service!.credentialsRejected = true;
    const check = jest.spyOn(OpenClawConnector.prototype, 'checkConnection')
      .mockRejectedValue(new ConnectorError('rejected', 'backend_auth_error'));
    const capabilities = response();
    await agentAdapterHandler(request('capabilities', 'GET', 'revoked-token'), capabilities);
    expect(check).toHaveBeenCalledTimes(1);
    expect(capabilities.statusCode).toBe(401);
    expect(capabilities.body).toMatchObject({ error: { code: 'backend_auth_error' } });
    expect(globalThis.__vssEmbeddedAgentAdapterSessions?.size ?? 0).toBe(0);
  });

  it('retains finished run history after evicting an idle token connector', () => {
    const first = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'token-0' });
    const run = first!.store.create({
      threadId: 'thread-1',
      input: [{ role: 'user', content: 'hello' }],
      history: [],
      surface: 'vss-ui',
      metadata: {},
    }, undefined, first!.ownerFingerprint).record;
    first!.store.finish(run, 'run.completed');
    for (let index = 1; index < 32; index += 1) {
      getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: `token-${index}` });
    }
    const next = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'token-32' });
    expect(next).toBeTruthy();
    expect(globalThis.__vssEmbeddedAgentAdapterSessions?.size).toBe(32);
    const resumed = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'token-0' });
    expect(resumed).not.toBe(first);
    expect(resumed!.store.get(run.runId, resumed!.ownerFingerprint)).toBe(run);
    expect(() => next!.store.get(run.runId, next!.ownerFingerprint)).toThrow(RunNotFoundError);
  });

  it('keeps an active connector when the idle-session cache fills', () => {
    const active = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'token-0' });
    const run = active!.store.create({
      threadId: 'thread-1',
      input: [{ role: 'user', content: 'hello' }],
      history: [],
      surface: 'vss-ui',
      metadata: {},
    }, undefined, active!.ownerFingerprint).record;
    for (let index = 1; index < 33; index += 1) {
      getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: `token-${index}` });
    }
    expect(globalThis.__vssEmbeddedAgentAdapterSessions?.size).toBe(32);
    expect(getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'token-0' })).toBe(active);
    active!.store.finish(run, 'run.completed');
  });

  it('evicts the oldest completed run at global capacity without exposing it to another token', () => {
    const store = new RunStore(60_000, 1, 10, 1_000, 10_000);
    const input = {
      threadId: 'same-thread',
      input: [{ role: 'user' as const, content: 'hello' }],
      history: [],
      surface: 'vss-ui',
      metadata: {},
    };
    const first = store.create(input, undefined, 'owner-a').record;
    store.finish(first, 'run.completed');
    const replacement = store.create(input, undefined, 'owner-b').record;
    expect(replacement.runId).not.toBe(first.runId);
    expect(() => store.get(first.runId, 'owner-a')).toThrow(RunNotFoundError);
    expect(() => store.get(replacement.runId, 'owner-a')).toThrow(RunNotFoundError);
    expect(store.get(replacement.runId, 'owner-b')).toBe(replacement);
  });

  it('cancels a cached active run locally during a gateway outage', async () => {
    const service = getAgentAdapterService({ ...process.env, AGENT_BACKEND_TOKEN: 'valid-token' });
    const record = {
      terminal: false,
      snapshot: () => ({ run_id: 'run-1', status: 'running' }),
    } as unknown as RunRecord;
    jest.spyOn(service!.store, 'get').mockReturnValue(record);
    const cancel = jest.spyOn(service!, 'cancelRun').mockResolvedValue(record);
    const check = jest.spyOn(OpenClawConnector.prototype, 'checkConnection')
      .mockRejectedValue(new ConnectorError('unreachable', 'backend_unreachable'));

    const result = response();
    await agentAdapterHandler(request('runs/run-1/cancel', 'POST', 'valid-token'), result);
    expect(result.statusCode).toBe(202);
    expect(cancel).toHaveBeenCalledWith('run-1');
    expect(check).not.toHaveBeenCalled();

    service!.credentialsRejected = true;
    check.mockRejectedValue(new ConnectorError('rejected', 'backend_auth_error'));
    const rejected = response();
    await agentAdapterHandler(request('runs/run-1/cancel', 'POST', 'valid-token'), rejected);
    expect(rejected.statusCode).toBe(401);
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
