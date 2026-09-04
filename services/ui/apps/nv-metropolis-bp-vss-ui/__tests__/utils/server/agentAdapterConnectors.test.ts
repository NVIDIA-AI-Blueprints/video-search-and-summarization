/** @jest-environment node */

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentAdapterConfig } from "../../../utils/server/agentAdapter/config";
import { OpenClawConnector } from "../../../utils/server/agentAdapter/connectors/openClaw";
import { ResponsesConnector } from "../../../utils/server/agentAdapter/connectors/responses";
import type { WebSocketLike } from "../../../utils/server/agentAdapter/connectors/websocket";
import { parseCreateRunRequest } from "../../../utils/server/agentAdapter/contract";

const config = (
  overrides: Partial<AgentAdapterConfig> = {}
): AgentAdapterConfig => ({
  backendProtocol: "responses",
  backendUrl: "http://agent.local",
  backendPath: "/v1/responses",
  backendToken: "backend-secret",
  backendModel: "agent",
  backendSessionField: "user",
  backendSessionHeader: "X-Agent-Session",
  backendHeaders: {},
  requestTimeoutMs: 5_000,
  runRetentionMs: 60_000,
  maxRuns: 100,
  maxEventsPerRun: 1_000,
  maxEventCharsPerRun: 1_000_000,
  maxThreadStateChars: 1_000_000,
  ...overrides,
});

const request = parseCreateRunRequest({
  thread_id: "thread-1",
  input: [{ role: "user", content: "Find a clip" }],
});

const sseResponse = (
  ...events: Array<[string, Record<string, unknown>]>
): Response =>
  new Response(
    events
      .map(
        ([type, payload]) =>
          `event: ${type}\ndata: ${JSON.stringify({ type, ...payload })}\n\n`
      )
      .join(""),
    { headers: { "Content-Type": "text/event-stream" } }
  );

class FakeOpenClawSocket extends EventTarget implements WebSocketLike {
  readyState = 0;
  binaryType: BinaryType = "arraybuffer";
  readonly sent: Record<string, unknown>[] = [];
  private sessionKey = "";
  private runId = "";

  constructor() {
    super();
    queueMicrotask(() => {
      this.readyState = 1;
      this.dispatchEvent(new Event("open"));
      this.message({
        type: "event",
        event: "connect.challenge",
        payload: { nonce: "nonce", ts: 1 },
      });
    });
  }

  private message(payload: Record<string, unknown>): void {
    this.dispatchEvent(
      new MessageEvent("message", { data: JSON.stringify(payload) })
    );
  }

  send(data: string): void {
    const frame = JSON.parse(data) as Record<string, unknown>;
    this.sent.push(frame);
    const params = frame.params as Record<string, unknown>;
    if (frame.method === "connect") {
      queueMicrotask(() =>
        this.message({
          type: "res",
          id: frame.id,
          ok: true,
          payload: {
            type: "hello-ok",
            protocol: 4,
            auth: {
              role: "operator",
              scopes: ["operator.read", "operator.write"],
            },
            features: {
              methods: ["chat.send", "chat.abort"],
              events: ["chat", "session.tool"],
            },
          },
        })
      );
    } else if (frame.method === "chat.send") {
      this.sessionKey = String(params.sessionKey);
      this.runId = "upstream-run";
      queueMicrotask(() => {
        this.message({
          type: "res",
          id: frame.id,
          ok: true,
          payload: { runId: this.runId, status: "accepted" },
        });
        this.message({
          type: "event",
          event: "session.tool",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            data: { id: "tool-1", name: "vss", phase: "started" },
          },
        });
        this.message({
          type: "event",
          event: "session.tool",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            data: {
              id: "tool-1",
              name: "vss",
              phase: "completed",
              result: { ok: true },
            },
          },
        });
        this.message({
          type: "event",
          event: "chat",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            state: "delta",
            deltaText: "Found it",
          },
        });
        this.message({
          type: "event",
          event: "chat",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            state: "final",
          },
        });
      });
    }
  }

  close(): void {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }
}

describe("embedded adapter connectors", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("normalizes Responses text and tool events while keeping credentials server-side", async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse(
        [
          "response.output_item.added",
          {
            response: { id: "resp_1" },
            item: {
              type: "function_call",
              id: "item-1",
              call_id: "call-1",
              name: "search",
            },
          },
        ],
        [
          "response.function_call_arguments.delta",
          { item_id: "item-1", delta: '{"q":"fire"}' },
        ],
        [
          "response.output_item.done",
          {
            item: {
              type: "function_call",
              id: "item-1",
              call_id: "call-1",
              name: "search",
              status: "completed",
              arguments: '{"q":"fire"}',
            },
          },
        ],
        ["response.output_text.delta", { delta: "Found it" }],
        ["response.completed", { response: { id: "resp_1" } }]
      )
    ) as jest.Mock;

    const connector = new ResponsesConnector(config());
    const events = [];
    for await (const event of connector.run(
      request,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.arguments.delta",
      "tool.completed",
      "message.delta",
    ]);
    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect((options.headers as Headers).get("Authorization")).toBe(
      "Bearer backend-secret"
    );
    expect((options.headers as Headers).get("X-Agent-Session")).toMatch(
      /^vss-ui:/
    );
    expect(JSON.parse(options.body as string)).toEqual(
      expect.objectContaining({ model: "agent", stream: true, store: true })
    );
  });

  it("uses native OpenClaw chat and tool events with narrow requested scopes", async () => {
    const socket = new FakeOpenClawSocket();
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
      }),
      () => socket
    );
    const events = [];
    for await (const event of connector.run(
      request,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.completed",
      "message.delta",
    ]);
    expect(events[1].data._artifact_source).toEqual({ ok: true });
    const connect = socket.sent.find((frame) => frame.method === "connect");
    expect((connect?.params as Record<string, unknown>).scopes).toEqual([
      "operator.read",
      "operator.write",
    ]);
  });
});
