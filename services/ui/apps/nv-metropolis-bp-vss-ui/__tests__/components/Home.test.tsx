// SPDX-License-Identifier: MIT
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

import Home from "../../components/Home";

jest.mock("next/dynamic", () => ({
  __esModule: true,
  default: (loader: () => Promise<unknown>) => {
    const source = loader.toString();

    if (source.includes("ChatPanel")) {
      return ({
        onAnswer,
        endpoint,
        features,
      }: {
        onAnswer?: (answer: string, conversationId: string) => void;
        endpoint?: { surface?: string; url?: string; transport?: string };
        features?: { hitl?: boolean };
      }) => (
        <button
          type="button"
          data-testid={
            endpoint?.surface === "vss-ui-sidebar"
              ? "deliver-sidebar-answer"
              : "deliver-search-artifact"
          }
          data-hitl-enabled={String(features?.hitl)}
          data-endpoint-url={endpoint?.url}
          data-endpoint-transport={endpoint?.transport}
          onClick={() =>
            onAnswer?.('{"data":[{"id":"retained-hit"}]}', "conversation-1")
          }
        >
          Deliver search artifact
        </button>
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

  it("routes both chat surfaces through the vss-agent SSE proxy by default", () => {
    render(<Home />);

    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-endpoint-url",
      "/api/vss-chat?surface=main",
    );
    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-endpoint-transport",
      "chat-sse",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-endpoint-url",
      "/api/vss-chat?surface=sidebar",
    );
    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-endpoint-transport",
      "chat-sse",
    );
  });

  it("routes both chat surfaces through the external-agent adapter when enabled", () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = "true";

    render(<Home />);

    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-endpoint-url",
      "/api/agent",
    );
    expect(screen.getByTestId("deliver-search-artifact")).toHaveAttribute(
      "data-endpoint-transport",
      "agent-api",
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-endpoint-url",
      "/api/agent",
    );
    expect(screen.getByTestId("deliver-sidebar-answer")).toHaveAttribute(
      "data-endpoint-transport",
      "agent-api",
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

  it("suppresses legacy HITL when the external-agent adapter is enabled", () => {
    process.env.NEXT_PUBLIC_AGENT_ADAPTER_ENABLED = "true";
    process.env.NEXT_PUBLIC_ENABLE_HITL = "true";
    process.env.NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL = "true";

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
});
