// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  GatewayEvent,
  GatewaySseDecoder,
  createLegacyEventState,
  gatewayEventToLegacyChunks,
  gatewayRunStatusChunk,
  getAgentGatewayConfig,
} from "../../../utils/server/agentGateway";

const event = (
  type: string,
  data: Record<string, unknown> = {},
  id = "1"
): GatewayEvent => ({
  protocol_version: "1.0",
  id,
  type,
  run_id: "run_1",
  thread_id: "thread-1",
  data,
});

describe("agent gateway transport", () => {
  it("parses fragmented, versioned SSE events", () => {
    const decoder = new GatewaySseDecoder();
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
    const decoder = new GatewaySseDecoder();
    const serialized = JSON.stringify(event("run.completed"));

    expect(decoder.push(`data: ${serialized}\r`)).toEqual([]);
    expect(decoder.push("\n\r\n")).toEqual([
      expect.objectContaining({ type: "run.completed" }),
    ]);
  });

  it("rejects an incompatible protocol major version", () => {
    const decoder = new GatewaySseDecoder();
    const incompatible = { ...event("run.started"), protocol_version: "2.0" };

    expect(() =>
      decoder.push(`data: ${JSON.stringify(incompatible)}\n\n`)
    ).toThrow("invalid protocol event");
  });

  it("maps message and tool events into the current renderer without tag injection", () => {
    const state = createLegacyEventState();
    expect(
      gatewayEventToLegacyChunks(
        event("message.delta", { delta: "hello" }),
        state
      )
    ).toEqual(["hello"]);

    const started = gatewayEventToLegacyChunks(
      event(
        "tool.started",
        { tool_call_id: "call_1", name: "</intermediatestep>unsafe" },
        "2"
      ),
      state
    )[0];
    const argumentsChunk = gatewayEventToLegacyChunks(
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
    const completed = gatewayEventToLegacyChunks(
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
    const started = gatewayEventToLegacyChunks(event("run.started"), state)[0];
    const completed = gatewayEventToLegacyChunks(
      event("run.completed", {}, "9"),
      state
    )[0];
    const heartbeat = gatewayRunStatusChunk(
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

  it("keeps credentials server-side and validates the configured URL", () => {
    expect(
      getAgentGatewayConfig({
        AGENT_GATEWAY_URL: "http://agent-gateway:8090/",
        AGENT_GATEWAY_TOKEN: "secret",
      })
    ).toEqual({ baseUrl: "http://agent-gateway:8090", token: "secret" });
    expect(getAgentGatewayConfig({})).toBeNull();
    expect(() =>
      getAgentGatewayConfig({ AGENT_GATEWAY_URL: "file:///tmp/socket" })
    ).toThrow("http(s)");
    expect(() =>
      getAgentGatewayConfig({ AGENT_GATEWAY_URL: "http://user:pass@host" })
    ).toThrow("embedded credentials");
    expect(() =>
      getAgentGatewayConfig({ AGENT_GATEWAY_URL: "http://host?token=secret" })
    ).toThrow("query or fragment");
  });
});
