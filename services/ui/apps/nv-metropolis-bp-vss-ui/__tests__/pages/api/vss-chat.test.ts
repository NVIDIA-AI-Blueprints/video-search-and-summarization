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
  writes: string[];
};

const mockRequest = (
  extras: Partial<NextApiRequest> & Record<string, unknown>,
): NextApiRequest =>
  ({
    on: jest.fn(),
    socket: { setTimeout: jest.fn() },
    headers: {},
    ...extras,
  }) as unknown as NextApiRequest;

const mockResponse = (): MockResponse => {
  const response = {
    statusCode: 200,
    body: undefined,
    writes: [] as string[],
    writableEnded: false,
    socket: { setTimeout: jest.fn() },
  } as unknown as MockResponse;
  response.status = jest.fn((statusCode: number) => {
    response.statusCode = statusCode;
    return response;
  });
  response.json = jest.fn((body: unknown) => {
    response.body = body;
    return response;
  });
  response.writeHead = jest.fn();
  response.write = jest.fn((chunk: Buffer | string) => {
    response.writes.push(Buffer.isBuffer(chunk) ? chunk.toString() : String(chunk));
    return true;
  });
  (response as unknown as { flush: () => void }).flush = jest.fn();
  response.on = jest.fn();
  response.end = jest.fn(() => {
    (response as { writableEnded: boolean }).writableEnded = true;
    return response;
  });
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
    const request = mockRequest({ method: "POST", query: {}, body: {} });
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
    const request = mockRequest({
      method: "GET",
      query: { surface: "sidebar", conversation: "conversation-1" },
    });
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
    const request = mockRequest({
      method: "POST",
      query: {
        surface: "main",
        interaction:
          "/executions/execution-1/interactions/interaction-1/response",
      },
      body: { response: { type: "text", text: "warehouse" } },
    });
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

  it("forwards an upstream stream drop as error_data instead of a silent EOF", async () => {
    process.env.VSS_CHAT_BACKEND_SIDEBAR = "http://vss-agent:8000/chat/stream";
    let reads = 0;
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            if (reads++ === 0) {
              return {
                done: false,
                value: Buffer.from(
                  'intermediate_data: {"id":"1","name":"search"}\n',
                ),
              };
            }
            throw new Error("body timeout");
          },
          releaseLock: () => {},
        }),
      },
    });
    global.fetch = fetchMock;

    const request = mockRequest({
      method: "POST",
      query: { surface: "sidebar" },
      headers: { "conversation-id": "conversation-1" },
      body: { messages: [{ role: "user", content: "report" }] },
    });
    const response = mockResponse();

    await handler(request, response);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://vss-agent:8000/chat/stream",
      expect.objectContaining({ method: "POST" }),
    );
    const body = response.writes.join("");
    expect(body).toContain("intermediate_data:");
    expect(body).toContain("error_data:");
    expect(body).toContain("body timeout");
  });

  it("rewrites Node fetch's 'terminated' into a user-facing stream-drop message", async () => {
    process.env.VSS_CHAT_BACKEND_SIDEBAR = "http://vss-agent:8000/chat/stream";
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            throw new TypeError("terminated");
          },
          releaseLock: () => {},
        }),
      },
    });

    const request = mockRequest({
      method: "POST",
      query: { surface: "sidebar" },
      body: { messages: [{ role: "user", content: "report" }] },
    });
    const response = mockResponse();

    await handler(request, response);

    expect(response.writes.join("")).toContain(
      "The agent connection closed before the response completed.",
    );
    expect(response.writes.join("")).not.toContain('"terminated"');
  });

  it("starts error_data on a new line when the last upstream chunk has no trailing newline", async () => {
    process.env.VSS_CHAT_BACKEND_SIDEBAR = "http://vss-agent:8000/chat/stream";
    let reads = 0;
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            if (reads++ === 0) {
              return {
                done: false,
                value: Buffer.from(
                  'data: {"choices":[{"delta":{"content":"par',
                ),
              };
            }
            throw new Error("body timeout");
          },
          releaseLock: () => {},
        }),
      },
    });

    const request = mockRequest({
      method: "POST",
      query: { surface: "sidebar" },
      body: { messages: [{ role: "user", content: "report" }] },
    });
    const response = mockResponse();

    await handler(request, response);

    const body = response.writes.join("");
    expect(body).toContain('data: {"choices":[{"delta":{"content":"par');
    expect(body).toContain("\nerror_data:");
    expect(body).not.toMatch(/parerror_data:/);
  });
});
