// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { NextApiRequest, NextApiResponse } from "next";

import handler from "../../../pages/api/vss-chat";

const backendVariables = [
  "VSS_CHAT_BACKEND_MAIN",
  "VSS_CHAT_BACKEND_SIDEBAR",
  "NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL",
  "NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL",
] as const;

type MockResponse = NextApiResponse & {
  body?: unknown;
  statusCode: number;
};

const mockResponse = (): MockResponse => {
  const response = {
    statusCode: 200,
    body: undefined,
  } as unknown as MockResponse;
  response.status = jest.fn((statusCode: number) => {
    response.statusCode = statusCode;
    return response;
  });
  response.json = jest.fn((body: unknown) => {
    response.body = body;
    return response;
  });
  response.end = jest.fn(() => response);
  return response;
};

describe("chat proxy", () => {
  const originalEnvironment = new Map<string, string | undefined>();

  beforeEach(() => {
    for (const variable of backendVariables) {
      originalEnvironment.set(variable, process.env[variable]);
      delete process.env[variable];
    }
  });

  afterEach(() => {
    for (const variable of backendVariables) {
      const value = originalEnvironment.get(variable);
      if (value === undefined) delete process.env[variable];
      else process.env[variable] = value;
    }
    originalEnvironment.clear();
  });

  it("returns a clear 503 for POST when no main backend is configured", async () => {
    const request = {
      method: "POST",
      query: {},
      body: {},
      on: jest.fn(),
    } as unknown as NextApiRequest;
    const response = mockResponse();

    await handler(request, response);

    expect(response.statusCode).toBe(503);
    expect(response.body).toEqual({
      error: "no backend configured for surface: main",
    });
  });

  // The Search tab's last-search hydrator was a client for a POC sidecar that
  // no deployed agent replaces; artifacts on the stream carry hits now.
  it("rejects GET, which no longer proxies a last-search read", async () => {
    process.env.VSS_CHAT_BACKEND_SIDEBAR = "http://vss-agent:8000/chat/stream";
    const fetchMock = jest.fn();
    global.fetch = fetchMock;
    const request = {
      method: "GET",
      query: { surface: "sidebar", conversation: "conversation-1" },
      on: jest.fn(),
    } as unknown as NextApiRequest;
    const response = mockResponse();

    await handler(request, response);

    expect(response.statusCode).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("proxies interaction responses to the agent origin", async () => {
    process.env.VSS_CHAT_BACKEND_MAIN = "http://vss-agent:8000/v1/chat/stream";
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 204,
    } as Response);
    global.fetch = fetchMock;
    const request = {
      method: "POST",
      query: {
        surface: "main",
        interaction:
          "/executions/execution-1/interactions/interaction-1/response",
      },
      body: { response: { type: "text", text: "warehouse" } },
      on: jest.fn(),
    } as unknown as NextApiRequest;
    const response = mockResponse();

    await handler(request, response);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://vss-agent:8000/executions/execution-1/interactions/interaction-1/response",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(request.body),
      }),
    );
    expect(response.statusCode).toBe(204);
  });

  it.each(["main", "sidebar"])(
    "uses the server-only %s backend when public URLs are also configured",
    async (surface) => {
      process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL =
        "http://public-host:8000/v1/chat/stream";
      process.env.NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL =
        "http://public-host:8000/v1/chat/stream";
      process.env.VSS_CHAT_BACKEND_MAIN =
        "http://main-agent:8000/v1/chat/stream";
      process.env.VSS_CHAT_BACKEND_SIDEBAR =
        "http://sidebar-agent:8000/v1/chat/stream";
      const fetchMock = jest.fn().mockResolvedValue({ ok: false, status: 503 });
      global.fetch = fetchMock;
      const request = {
        method: "POST",
        query: { surface },
        headers: { "conversation-id": "conversation-1" },
        body: { messages: [{ role: "user", content: "What happened?" }] },
        on: jest.fn(),
      } as unknown as NextApiRequest;
      const response = mockResponse();

      await handler(request, response);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledWith(
        `http://${surface}-agent:8000/v1/chat/stream`,
        expect.objectContaining({
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Conversation-Id": "conversation-1",
          },
          body: JSON.stringify(request.body),
        }),
      );
      expect(response.statusCode).toBe(502);
      expect(response.body).toEqual({
        error: "agent backend returned HTTP 503",
      });
    },
  );
});
