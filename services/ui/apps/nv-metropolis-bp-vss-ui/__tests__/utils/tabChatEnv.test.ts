// SPDX-License-Identifier: MIT
import {
  tabChatEnvKey,
  getTabChatWorkflow,
  getTabChatInitialStateOverride,
} from '../../utils/tabChatEnv';
import { setMockEnv, clearMockEnv } from 'next-runtime-env';

beforeEach(() => {
  clearMockEnv();
});

describe('tabChatEnvKey', () => {
  it('builds correct env key', () => {
    expect(tabChatEnvKey('SEARCH_TAB', 'WORKFLOW')).toBe(
      'NEXT_PUBLIC_SEARCH_TAB_CHAT_WORKFLOW',
    );
  });

  it('builds correct env key for alerts', () => {
    expect(tabChatEnvKey('ALERTS_TAB', 'DARK_THEME_DEFAULT')).toBe(
      'NEXT_PUBLIC_ALERTS_TAB_CHAT_DARK_THEME_DEFAULT',
    );
  });
});

describe('getTabChatWorkflow', () => {
  it('returns tab-specific workflow when set', () => {
    setMockEnv('NEXT_PUBLIC_SEARCH_TAB_CHAT_WORKFLOW', 'SearchWorkflow');
    expect(getTabChatWorkflow('SEARCH_TAB')).toBe('SearchWorkflow');
  });

  it('falls back to main NEXT_PUBLIC_WORKFLOW', () => {
    setMockEnv('NEXT_PUBLIC_WORKFLOW', 'MainWorkflow');
    expect(getTabChatWorkflow('SEARCH_TAB')).toBe('MainWorkflow');
  });

  it('falls back to defaultWorkflowName when no env is set', () => {
    expect(getTabChatWorkflow('SEARCH_TAB', 'Search Chat')).toBe('Search Chat');
  });

  it('falls back to "Chat" when nothing is provided', () => {
    expect(getTabChatWorkflow('SEARCH_TAB')).toBe('Chat');
  });

  it('prefers tab-specific over main workflow', () => {
    setMockEnv('NEXT_PUBLIC_SEARCH_TAB_CHAT_WORKFLOW', 'TabSpecific');
    setMockEnv('NEXT_PUBLIC_WORKFLOW', 'MainWorkflow');
    expect(getTabChatWorkflow('SEARCH_TAB')).toBe('TabSpecific');
  });
});

describe('getTabChatInitialStateOverride', () => {
  it('returns defaults when no env vars are set', () => {
    const result = getTabChatInitialStateOverride('SEARCH_TAB');

    expect(result.lightMode).toBe('light');
    expect(result.showChatbar).toBe(true);
    expect(result.chatHistory).toBe(false);
    expect(result.webSocketMode).toBe(false);
    expect(result.enableIntermediateSteps).toBe(false);
    expect(result.chatUploadFileEnabled).toBe(false);
    expect(result.chatUploadFileMetadataEnabled).toBe(false);
    expect(result.chatCompletionURL).toBeUndefined();
    expect(result.webSocketURL).toBeUndefined();
    expect(result.agentApiUrlBase).toBeUndefined();
    expect(result.customAgentParamsJson).toBeUndefined();
  });

  it('reads dark theme from tab-specific env', () => {
    setMockEnv('NEXT_PUBLIC_SEARCH_TAB_CHAT_DARK_THEME_DEFAULT', 'true');
    const result = getTabChatInitialStateOverride('SEARCH_TAB');
    expect(result.lightMode).toBe('dark');
  });

  it('reads chatbar collapsed from tab-specific env', () => {
    setMockEnv('NEXT_PUBLIC_ALERTS_TAB_CHAT_SIDE_CHATBAR_COLLAPSED', 'true');
    const result = getTabChatInitialStateOverride('ALERTS_TAB');
    expect(result.showChatbar).toBe(false);
  });

  it('reads URL fields', () => {
    setMockEnv(
      'NEXT_PUBLIC_SEARCH_TAB_CHAT_HTTP_CHAT_COMPLETION_URL',
      'http://localhost:8080/chat',
    );
    setMockEnv(
      'NEXT_PUBLIC_SEARCH_TAB_CHAT_WEBSOCKET_CHAT_COMPLETION_URL',
      'ws://localhost:8080/ws',
    );
    setMockEnv(
      'NEXT_PUBLIC_SEARCH_TAB_CHAT_AGENT_API_URL_BASE',
      'http://localhost:9090',
    );

    const result = getTabChatInitialStateOverride('SEARCH_TAB');
    expect(result.chatCompletionURL).toBe('http://localhost:8080/chat');
    expect(result.webSocketURL).toBe('ws://localhost:8080/ws');
    expect(result.agentApiUrlBase).toBe('http://localhost:9090');
  });

  it('boolean-default-true fields return true when env is not set', () => {
    const result = getTabChatInitialStateOverride('SEARCH_TAB');
    expect(result.themeChangeButtonEnabled).toBe(true);
    expect(result.interactionModalCancelEnabled).toBe(true);
    expect(result.chatInputMicEnabled).toBe(true);
    expect(result.chatMessageEditEnabled).toBe(true);
    expect(result.chatMessageSpeakerEnabled).toBe(true);
    expect(result.chatMessageCopyEnabled).toBe(true);
  });

  it('boolean-default-true fields return false when env is "false"', () => {
    setMockEnv('NEXT_PUBLIC_SEARCH_TAB_CHAT_SHOW_THEME_TOGGLE_BUTTON', 'false');
    setMockEnv('NEXT_PUBLIC_SEARCH_TAB_CHAT_CHAT_INPUT_MIC_ENABLED', 'false');
    const result = getTabChatInitialStateOverride('SEARCH_TAB');
    expect(result.themeChangeButtonEnabled).toBe(false);
    expect(result.chatInputMicEnabled).toBe(false);
  });

  it('falls back to main env vars when tab-specific are not set', () => {
    setMockEnv('NEXT_PUBLIC_DARK_THEME_DEFAULT', 'true');
    setMockEnv('NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON', 'true');
    const result = getTabChatInitialStateOverride('ALERTS_TAB');
    expect(result.lightMode).toBe('dark');
    expect(result.webSocketMode).toBe(true);
  });
});
