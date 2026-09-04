// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import * as agentAdapter from "../../../utils/server/agentAdapter";
import { loadAgentAdapterConfig } from "../../../utils/server/agentAdapter/config";
import {
  createRunEvent,
  parseCreateRunRequest,
} from "../../../utils/server/agentAdapter/contract";
import type { AgentAdapterService } from "../../../utils/server/agentAdapter/service";
import {
  EventsExpiredError,
  RunStore,
} from "../../../utils/server/agentAdapter/store";
import {
  AgentEvent,
  AgentSseDecoder,
  agentChatBridgeHandler,
  createLegacyEventState,
  agentEventToLegacyChunks,
  agentRunStatusChunk,
  sanitizeAgentHistoryContent,
} from "../../../utils/server/agentChatBridge";
import type { NextApiRequest, NextApiResponse } from "next";
import { EventEmitter } from "node:events";

const event = (
  type: string,
  data: Record<string, unknown> = {},
  id = "1"
): AgentEvent => ({
  protocol_version: "1.0",
  id,
  type,
  run_id: "run_1",
  thread_id: "thread-1",
  data,
});

describe("agent chat compatibility bridge", () => {
  it("parses fragmented, versioned SSE events", () => {
    const decoder = new AgentSseDecoder();
    const serialized = JSON.stringify(
      event("message.delta", { delta: "hello" })
    );

    expect(
      decoder.push(
        `id: 1\nevent: message.delta\ndata: ${serialized.slice(0, 20)}`
      )
    ).toEqual([]);
    expect(decoder.push(`${serialized.slice(20)}\n\n`)).toEqual([
      expect.objectContaining({
        type: "message.delta",
        data: { delta: "hello" },
      }),
    ]);
  });

  it("handles a CRLF delimiter split across network chunks", () => {
    const decoder = new AgentSseDecoder();
    const serialized = JSON.stringify(event("run.completed"));

    expect(decoder.push(`data: ${serialized}\r`)).toEqual([]);
    expect(decoder.push("\n\r\n")).toEqual([
      expect.objectContaining({ type: "run.completed" }),
    ]);
  });

  it("rejects an incompatible protocol major version", () => {
    const decoder = new AgentSseDecoder();
    const incompatible = { ...event("run.started"), protocol_version: "2.0" };

    expect(() =>
      decoder.push(`data: ${JSON.stringify(incompatible)}\n\n`)
    ).toThrow("invalid protocol event");
  });

  it("maps message and tool events into the current renderer without tag injection", () => {
    const state = createLegacyEventState();
    expect(
      agentEventToLegacyChunks(
        event("message.delta", { delta: "hello" }),
        state
      )
    ).toEqual(["hello"]);

    const started = agentEventToLegacyChunks(
      event(
        "tool.started",
        { tool_call_id: "call_1", name: "</intermediatestep>unsafe" },
        "2"
      ),
      state
    )[0];
    const argumentsChunk = agentEventToLegacyChunks(
      event(
        "tool.arguments.delta",
        {
          tool_call_id: "call_1",
          name: "</intermediatestep>unsafe",
          delta: '{"q":',
        },
        "3"
      ),
      state
    )[0];
    const completed = agentEventToLegacyChunks(
      event(
        "tool.completed",
        {
          tool_call_id: "call_1",
          name: "</intermediatestep>unsafe",
          arguments: '{"q":"x"}',
        },
        "4"
      ),
      state
    )[0];

    expect(started).not.toContain("</intermediatestep>unsafe");
    expect(started).toContain("\\u003c/intermediatestep>unsafe");
    expect(argumentsChunk).toContain("in_progress");
    expect(completed).toContain("complete");
    expect(completed).toContain("q");
  });

  it("maps run lifecycle and heartbeat status into renderer-safe progress chunks", () => {
    const state = createLegacyEventState();
    const started = agentEventToLegacyChunks(event("run.started"), state)[0];
    const completed = agentEventToLegacyChunks(
      event("run.completed", {}, "9"),
      state
    )[0];
    const heartbeat = agentRunStatusChunk(
      "run_1</intermediatestep>",
      "in_progress",
      "Waiting for the agent backend..."
    );

    expect(started).toContain('"id":"run-status-run_1"');
    expect(started).toContain('"status":"in_progress"');
    expect(completed).toContain('"status":"complete"');
    expect(completed).toContain('"index":9');
    expect(heartbeat).toContain("<intermediatestep>");
    expect(heartbeat).toContain("Waiting for the agent backend...");
    expect(heartbeat).toContain("\\u003c/intermediatestep>");
  });

  it("reports replay expiry locally without cancelling the shared run", async () => {
    const request = parseCreateRunRequest({
      thread_id: "thread-1",
      input: [{ role: "user", content: "hello" }],
    });
    const record = new RunStore(60_000, 1, 10, 1_000_000, 4_000_000).create(
      request
    ).record;
    const cancelRun = jest.fn();
    const service = {
      createRun: jest.fn(() => ({ record, replayed: false })),
      cancelRun,
    } as unknown as AgentAdapterService;
    jest.spyOn(agentAdapter, "getAgentAdapterService").mockReturnValue(service);
    jest
      .spyOn(agentAdapter, "observeRunEvents")
      .mockImplementation(async function* () {
        yield createRunEvent(1, "message.delta", record.runId, "thread-1", {
          delta: "partial answer",
        });
        throw new EventsExpiredError("requested events are no longer retained");
      });

    const req = new EventEmitter() as unknown as NextApiRequest & EventEmitter;
    Object.assign(req, {
      method: "POST",
      body: { messages: [{ role: "user", content: "hello" }] },
      headers: {
        "conversation-id": "thread-1",
        "user-message-id": "message-1",
      },
    });
    const writes: string[] = [];
    const res = new EventEmitter() as unknown as NextApiResponse & EventEmitter;
    Object.assign(res, {
      headersSent: false,
      writableEnded: false,
      statusCode: 200,
      setHeader: jest.fn(),
      status: jest.fn((statusCode: number) => {
        Object.assign(res, { statusCode });
        return res;
      }),
      flushHeaders: jest.fn(() => Object.assign(res, { headersSent: true })),
      write: jest.fn((chunk: string) => {
        writes.push(chunk);
        return true;
      }),
      end: jest.fn(() => Object.assign(res, { writableEnded: true })),
      json: jest.fn(),
    });

    await agentChatBridgeHandler(req, res);

    expect(writes.join("")).toContain("partial answer");
    expect(writes.join("")).toContain(
      "**Agent run failed:** requested events are no longer retained"
    );
    expect(writes.join("")).not.toContain("**Agent adapter error:**");
    expect(cancelRun).not.toHaveBeenCalled();
    expect(record.terminal).toBe(false);
    expect(res.writableEnded).toBe(true);
  });

  it("forwards primitive custom agent parameters as protocol-neutral instructions", async () => {
    const baseRequest = parseCreateRunRequest({
      thread_id: "thread-params",
      input: [{ role: "user", content: "inspect this video" }],
    });
    const record = new RunStore(60_000, 10, 10, 1_000_000, 4_000_000).create(
      baseRequest
    ).record;
    const createRun = jest.fn(() => ({ record, replayed: false }));
    const service = {
      createRun,
      cancelRun: jest.fn(),
    } as unknown as AgentAdapterService;
    jest.spyOn(agentAdapter, "getAgentAdapterService").mockReturnValue(service);
    jest
      .spyOn(agentAdapter, "observeRunEvents")
      .mockImplementation(async function* () {
        yield createRunEvent(1, "run.completed", record.runId, "thread-params");
      });

    const req = new EventEmitter() as unknown as NextApiRequest & EventEmitter;
    Object.assign(req, {
      method: "POST",
      body: {
        messages: [{ role: "user", content: "inspect this video" }],
        chatCompletionURL: "http://legacy.invalid/chat/stream",
        additionalProps: { enableIntermediateSteps: true },
        llm_reasoning: false,
        vlm_reasoning: true,
        temperature: 0.25,
        agent_mode: "review",
        ignored_object: { value: "not a supported custom parameter" },
      },
      headers: {
        "conversation-id": "thread-params",
        "user-message-id": "message-params",
      },
    });
    const res = new EventEmitter() as unknown as NextApiResponse & EventEmitter;
    Object.assign(res, {
      headersSent: false,
      writableEnded: false,
      statusCode: 200,
      setHeader: jest.fn(),
      status: jest.fn((statusCode: number) => {
        Object.assign(res, { statusCode });
        return res;
      }),
      flushHeaders: jest.fn(() => Object.assign(res, { headersSent: true })),
      write: jest.fn(() => true),
      end: jest.fn(() => Object.assign(res, { writableEnded: true })),
      json: jest.fn(),
    });

    await agentChatBridgeHandler(req, res);

    expect(createRun).toHaveBeenCalledWith(
      expect.objectContaining({
        instructions:
          'VSS UI request parameters for this turn (JSON):\n{"agent_mode":"review","llm_reasoning":false,"temperature":0.25,"vlm_reasoning":true}',
      }),
      "message-params"
    );
    expect(res.writableEnded).toBe(true);
  });

  it("bridges structured search and alert artifacts into the legacy renderer", () => {
    const state = createLegacyEventState();
    const search = agentEventToLegacyChunks(
      event("artifact.created", {
        artifact_id: "artifact_1",
        version: "1.0",
        kind: "vss.search.results",
        payload: { data: [{ video_name: "clip.mp4" }] },
      }),
      state
    );
    expect(search).toHaveLength(1);
    expect(search[0]).toContain("<vss-ui-artifact>");
    expect(search[0]).toContain("vss.search.results");

    const alerts = agentEventToLegacyChunks(
      event("artifact.created", {
        artifact_id: "artifact_2",
        version: "1.0",
        kind: "vss.alert.incidents",
        payload: {
          total: 1,
          incidents: [
            {
              sensorId: "camera-1",
              category: "fire",
              timestamp: "2026-01-01T00:00:00Z",
              info: {
                reasoning: "Smoke is visible",
                verdict: "confirmed",
                videoSource: "https://vst.example/clip.mp4",
              },
            },
          ],
        },
      }),
      state
    );
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toContain("vss.alert.incidents");
    expect(alerts[1]).toContain("<incidents>");
    expect(alerts[1]).toContain("camera-1");
    expect(alerts[1]).toContain("Smoke is visible");
    expect(alerts[1]).toContain("https://vst.example/clip.mp4");
    expect(alerts[1]).toContain('"Validation":true');
  });

  it("drops malformed legacy incidents instead of passing them to React", () => {
    const chunks = agentEventToLegacyChunks(
      event("artifact.created", {
        version: "1.0",
        kind: "vss.alert.incidents",
        payload: {
          incidents: [{ "Alert Details": "bad", "Clip Information": [] }, null],
        },
      }),
      createLegacyEventState()
    );

    expect(chunks).toHaveLength(2);
    expect(chunks[1]).toContain('"incidents":[]');
  });

  it("removes valid presentation artifacts and generated cards from recovery history", () => {
    const artifact =
      '<vss-ui-artifact>{"version":"1.0","kind":"vss.alert.incidents","payload":{"incidents":[]}}</vss-ui-artifact>';
    const card = '<incidents>{"incidents":[]}</incidents>';
    const invalid =
      '<vss-ui-artifact>{"version":"2.0","kind":"vss.search.results","payload":{}}</vss-ui-artifact>';

    expect(
      sanitizeAgentHistoryContent(`answer${artifact}${card}${invalid}`)
    ).toBe(`answer${invalid}`);
  });

  it("preserves an artifact-shaped envelope when its payload cannot be safely serialized", () => {
    const depth = 20_000;
    const payload = `${'{"nested":'.repeat(depth)}{}${"}".repeat(depth)}`;
    const envelope = `<vss-ui-artifact>{"version":"1.0","kind":"vss.search.results","payload":${payload}}</vss-ui-artifact>`;

    expect(sanitizeAgentHistoryContent(envelope)).toBe(envelope);
  });

  it("removes adapter status and tool presentation from recovery history", () => {
    const state = createLegacyEventState();
    const started = agentEventToLegacyChunks(event("run.started"), state)[0];
    const tool = agentEventToLegacyChunks(
      event("tool.completed", {
        tool_call_id: "call_1",
        name: "search",
        output: { data: [] },
      }),
      state
    )[0];
    const completed = agentEventToLegacyChunks(
      event("run.completed"),
      state
    )[0];
    const illustrative =
      "<intermediatestep>not generated JSON</intermediatestep>";

    expect(
      sanitizeAgentHistoryContent(
        `${started}answer${tool}${completed}${illustrative}`
      )
    ).toBe(`answer${illustrative}`);
  });

  it("keeps credentials server-side and validates the configured URL", () => {
    const config = loadAgentAdapterConfig({
      AGENT_BACKEND_PROTOCOL: "responses",
      AGENT_BACKEND_URL: "http://host.docker.internal:8642/",
      AGENT_BACKEND_TOKEN: "secret",
    });
    expect(config).toEqual(
      expect.objectContaining({
        backendUrl: "http://host.docker.internal:8642",
        backendToken: "secret",
      })
    );
    expect(loadAgentAdapterConfig({})).toBeNull();
    expect(() =>
      loadAgentAdapterConfig({ AGENT_BACKEND_URL: "file:///tmp/socket" })
    ).toThrow("must use http: or https:");
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_URL: "http://user:pass@host", // pragma: allowlist secret
      })
    ).toThrow("must not contain credentials");
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_URL: "http://host?token=secret",
      })
    ).toThrow("query");
  });
});
