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
  return response;
};

describe("toolkit-free chat proxy", () => {
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

  it.each([
    ["GET", "sidebar"],
    ["POST", "main"],
  ])(
    "returns a clear 503 for %s when no %s backend is configured",
    async (method, surface) => {
      const request = {
        method,
        query: {},
        body: {},
        on: jest.fn(),
      } as unknown as NextApiRequest;
      const response = mockResponse();

      await handler(request, response);

      expect(response.statusCode).toBe(503);
      expect(response.body).toEqual({
        error: `no backend configured for surface: ${surface}`,
      });
    },
  );
});
