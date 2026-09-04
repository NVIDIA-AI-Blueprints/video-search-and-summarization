// SPDX-License-Identifier: MIT

import Home from "../../components/Home";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";

jest.mock("next/dynamic", () => ({
  __esModule: true,
  default: (loader: () => Promise<unknown>) => {
    const source = loader.toString();

    if (source.includes("NemoAgentToolkitApp")) {
      return ({
        onAnswerCompleteWithContent,
      }: {
        onAnswerCompleteWithContent?: (answer: string) => void;
      }) => (
        <button
          type="button"
          data-testid="deliver-search-result"
          onClick={() =>
            onAnswerCompleteWithContent?.('{"data":[{"id":"retained-hit"}]}')
          }
        >
          Deliver search result
        </button>
      );
    }

    if (source.includes("SearchComponent")) {
      return ({
        registerChatAnswerHandler,
      }: {
        registerChatAnswerHandler: (
          handler: (answer: string) => boolean
        ) => () => void;
      }) => {
        const [answer, setAnswer] = React.useState("");

        React.useEffect(
          () =>
            registerChatAnswerHandler((nextAnswer) => {
              setAnswer(nextAnswer);
              return true;
            }),
          [registerChatAnswerHandler]
        );

        return <div data-testid="search-result-state">{answer}</div>;
      };
    }

    return () => null;
  },
}));

jest.mock("next-runtime-env", () => ({
  env: (key: string) => process.env[key],
}));

jest.mock(
  "@nemo-agent-toolkit/ui",
  () => ({
    RuntimeConfigProvider: ({ children }: { children: React.ReactNode }) =>
      children,
    ChatSidebarContent: () => null,
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
  ] as const;

  beforeEach(() => {
    sessionStorage.clear();
    process.env.NEXT_PUBLIC_ENABLE_CHAT_TAB = "true";
    process.env.NEXT_PUBLIC_ENABLE_SEARCH_TAB = "true";
    process.env.NEXT_PUBLIC_ENABLE_ALERTS_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_MAP_TAB = "false";
    process.env.NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB = "false";
  });

  afterEach(() => {
    for (const variable of featureVariables) delete process.env[variable];
  });

  it("retains an agent search result when leaving the full-page Chat tab", () => {
    render(<Home />);

    fireEvent.click(screen.getByTestId("deliver-search-result"));
    expect(screen.getByTestId("search-result-state")).toHaveTextContent(
      "retained-hit"
    );

    fireEvent.click(screen.getByTestId("sidebar-tab-search"));

    expect(screen.getByTestId("search-result-state")).toHaveTextContent(
      "retained-hit"
    );
  });
});
