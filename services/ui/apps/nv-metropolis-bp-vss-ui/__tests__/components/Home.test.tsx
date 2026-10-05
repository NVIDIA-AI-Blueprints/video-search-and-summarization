// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";

import Home from "../../components/Home";

jest.mock("next/dynamic", () => ({
  __esModule: true,
  default: (loader: () => Promise<unknown>) => {
    const source = loader.toString();

    if (source.includes("ChatPanel")) {
      return ({
        onAnswer,
        onAuthFailure,
        endpoint,
        features,
      }: {
        onAnswer?: (answer: string, conversationId: string) => void;
        onAuthFailure?: () => void;
        endpoint?: { surface?: string; headers?: Record<string, string> };
        features?: { hitl?: boolean };
      }) => (
        <>
        {onAuthFailure ? (
          <button type="button" onClick={onAuthFailure}>
            Reject {endpoint?.surface} token
          </button>
        ) : null}
        <button
          type="button"
          data-testid={
            endpoint?.surface === "vss-ui-sidebar"
              ? "deliver-sidebar-answer"
              : "deliver-search-artifact"
          }
          data-hitl-enabled={String(features?.hitl)}
          data-gateway-token={endpoint?.headers?.['X-VSS-Gateway-Token'] ?? ''}
          onClick={() =>
            onAnswer?.('{"data":[{"id":"retained-hit"}]}', "conversation-1")
          }
        >
          Deliver search artifact
        </button>
        </>
      );
    }

    if (source.includes("SearchComponent")) {
      return ({
        registerChatAnswerHandler,
      }: {
        registerChatAnswerHandler: (
          handler: (answer: string) => boolean,
        ) => () => void;
      }) => {
        const [answer, setAnswer] = React.useState("");

        React.useEffect(
          () =>
            registerChatAnswerHandler((nextAnswer) => {
              setAnswer(nextAnswer);
              return true;
            }),
          [registerChatAnswerHandler],
        );

        return <div data-testid="search-artifact-state">{answer}</div>;
      };
    }

    return () => null;
  },
}));

jest.mock("next-runtime-env", () => ({
  env: (key: string) => process.env[key],
}));

jest.mock(
  "@nv-metropolis-bp-vss-ui/chat",
  () => ({
    ConversationList: () => null,
  }),
  { virtual: true }
);

jest.mock("../../hooks/useTheme", () => ({
  useTheme: () => ({
    theme: "light",
    setTheme: jest.fn(),
    toggleTheme: jest.fn(),
    isDark: false,
    isLight: true,
  }),
}));

jest.mock("../../hooks/useAppChatSidebar", () => ({
  useAppChatSidebar: () => ({
    collapsed: true,
    setCollapsed: jest.fn(),
    effectiveWidth: 400,
    handleResizeStart: jest.fn(),
    contentAreaCallbackRef: jest.fn(),
  }),
}));

jest.mock("../../utils/tabChatSidebarConfig", () => ({
  CHAT_SIDEBAR_INSTANCE_STORAGE_PREFIX: "test-sidebar-",
  SIDEBAR_CHAT_ENV_TAB_KEY: "test",
  getChatSidebarEnabled: () => true,
}));

describe("Home tab lifecycle", () => {
  const featureVariables = [
    "NEXT_PUBLIC_ENABLE_CHAT_TAB",
    "NEXT_PUBLIC_ENABLE_SEARCH_TAB",
    "NEXT_PUBLIC_ENABLE_ALERTS_TAB",
    "NEXT_PUBLIC_ENABLE_DASHBOARD_TAB",
    "NEXT_PUBLIC_ENABLE_MAP_TAB",
    "NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB",
    "NEXT_PUBLIC_AGENT_ADAPTER_ENABLED",
    "NEXT_PUBLIC_ENABLE_HITL",
    "NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL",
  ] as const;

  const originalFetch = global.fetch;

  beforeEach(() => {
    sessionStorage.clear();
    process.env.NEXT_PUBLIC_ENABLE_CHAT_TAB = "true";
    process.env.NEXT_PUBLIC_ENABLE_SEARCH_TAB = "true";
    process.env.NEXT_PUBLIC_ENABLE_ALERTS_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_MAP_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB = "false";
    delete process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED;
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      json: async () => ({}),
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    for (const variable of featureVariables) delete process.env[variable];
    global.fetch = originalFetch;
  });

  it("retains an agent search artifact when leaving the full-page Chat tab", () => {
    render(<Home />);

    fireEvent.click(screen.getByTestId("deliver-search-artifact"));
    expect(screen.getByTestId("search-artifact-state")).toHaveTextContent(
      "retained-hit",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("search-artifact-state")).toHaveTextContent(
      "retained-hit",
    );
  });

  // A sidebar answer used to be followed by a last-search read against a route
  // no deployed agent serves, which surfaced as a 404 on every turn.
  it("issues no follow-up request after a sidebar answer", () => {
    render(<Home />);
    fireEvent.click(screen.getByTestId("sidebar-tab-search"));
    fireEvent.click(screen.getByTestId("deliver-sidebar-answer"));

    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("disables structured HITL on both chat surfaces by default", () => {
    render(<Home />);

    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-hitl-enabled",
      "false",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-hitl-enabled",
      "false",
    );
  });

  it("allows an explicit HITL opt-in independently per surface", () => {
    process.env.NEXT_PUBLIC_ENABLE_HITL = "true";
    process.env.NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL = "false";

    render(<Home />);

    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-hitl-enabled",
      "true",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-hitl-enabled",
      "false",
    );
  });

  it("suppresses legacy HITL when the external-agent adapter is enabled", async () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = "true";
    process.env.NEXT_PUBLIC_ENABLE_HITL = "true";
    process.env.NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL = "true";
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ state: 'connected' }) }) as unknown as typeof fetch;

    render(<Home />);

    expect(await screen.findByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-hitl-enabled",
      "false",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-hitl-enabled",
      "false",
    );
  });

  it('connects both chat surfaces with a browser-entered token', async () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = 'true';
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ state: 'token_required' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ state: 'connected' }) }) as unknown as typeof fetch;

    render(<Home />);
    const field = await screen.findByLabelText('Gateway token');
    fireEvent.change(field, { target: { value: 'browser-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }));

    expect(await screen.findByTestId('deliver-search-artifact')).toHaveAttribute('data-gateway-token', 'browser-token');
    fireEvent.click(screen.getByTestId('sidebar-tab-search'));
    expect(screen.getByTestId('deliver-sidebar-answer')).toHaveAttribute('data-gateway-token', 'browser-token');
    expect(sessionStorage.getItem('vss-nemoclaw-gateway-token')).toBe('browser-token');
    expect(global.fetch).toHaveBeenLastCalledWith('/api/agent/connection', expect.objectContaining({
      headers: { 'X-VSS-Gateway-Token': 'browser-token' },
    }));
  });

  it('keeps chat mounted during a connection recheck and after a gateway failure', async () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = 'true';
    let finishCheck!: (result: { ok: boolean; json: () => Promise<{ state: string }> }) => void;
    const pendingCheck = new Promise<{ ok: boolean; json: () => Promise<{ state: string }> }>(
      (resolve) => { finishCheck = resolve; },
    );
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ state: 'connected' }) })
      .mockReturnValueOnce(pendingCheck) as unknown as typeof fetch;

    render(<Home />);
    expect(await screen.findByTestId('deliver-search-artifact')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(screen.getByTestId('deliver-search-artifact')).toBeInTheDocument();
    await act(async () => {
      finishCheck({ ok: true, json: async () => ({ state: 'unreachable' }) });
      await pendingCheck;
    });
    expect(screen.getByTestId('deliver-search-artifact')).toBeInTheDocument();
    expect(screen.getByText('NemoClaw gateway unavailable')).toBeInTheDocument();
  });

  it('asks for the token again when chat reports it rejected', async () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = 'true';
    sessionStorage.setItem('vss-nemoclaw-gateway-token', 'revoked-token');
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ state: 'connected' }) }) as unknown as typeof fetch;

    render(<Home />);
    expect(await screen.findByTestId('deliver-search-artifact')).toHaveAttribute('data-gateway-token', 'revoked-token');
    fireEvent.click(screen.getByRole('button', { name: 'Reject vss-ui-main token' }));

    expect(screen.getByRole('alert')).toHaveTextContent('The gateway rejected the connection');
    expect(screen.getByLabelText('Gateway token')).toBeInTheDocument();
    expect(screen.queryByTestId('deliver-search-artifact')).not.toBeInTheDocument();
    expect(sessionStorage.getItem('vss-nemoclaw-gateway-token')).toBeNull();
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});
