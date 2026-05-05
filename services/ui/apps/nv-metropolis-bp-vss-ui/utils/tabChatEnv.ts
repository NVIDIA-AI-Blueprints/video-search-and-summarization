// SPDX-License-Identifier: MIT
/**
 * Reusable helpers for tab-specific Chat sidebar config.
 * Maps NEXT_PUBLIC_${TAB}_CHAT_* env variables (e.g. SEARCH_TAB, ALERTS_TAB) to the shape
 * expected by the Chat package. When a tab-specific var is not set, falls back to main NEXT_PUBLIC_* chat vars.
 *
 * Use tabKey like 'SEARCH_TAB' or 'ALERTS_TAB' so env vars follow:
 * NEXT_PUBLIC_SEARCH_TAB_CHAT_WORKFLOW, NEXT_PUBLIC_ALERTS_TAB_CHAT_WORKFLOW, etc.
 */
import { env } from 'next-runtime-env';

/** Build env key for a tab's chat: NEXT_PUBLIC_${tabKey}_CHAT_${suffix} */
export function tabChatEnvKey(tabKey: string, suffix: string): string {
  return `NEXT_PUBLIC_${tabKey}_CHAT_${suffix}`;
}

function get(tabKey: string, suffix: string, mainKey: string): string {
  const tabKeyFull = tabChatEnvKey(tabKey, suffix);
  const v =
    env(tabKeyFull) ||
    process?.env?.[tabKeyFull as keyof NodeJS.ProcessEnv] ||
    env(mainKey) ||
    process?.env?.[mainKey as keyof NodeJS.ProcessEnv];
  return typeof v === 'string' ? v : '';
}

function getBool(tabKey: string, suffix: string, mainKey: string): boolean {
  return get(tabKey, suffix, mainKey) === 'true';
}

/** Like getBool but default is true; only the string 'false' disables. */
function getBoolDefaultTrue(tabKey: string, suffix: string, mainKey: string): boolean {
  return get(tabKey, suffix, mainKey) !== 'false';
}

/**
 * Workflow name for this tab's chat instance.
 * Env: NEXT_PUBLIC_${tabKey}_CHAT_WORKFLOW (fallback: NEXT_PUBLIC_WORKFLOW).
 */
export function getTabChatWorkflow(
  tabKey: string,
  defaultWorkflowName?: string,
): string {
  return (
    get(tabKey, 'WORKFLOW', 'NEXT_PUBLIC_WORKFLOW') ||
    defaultWorkflowName ||
    'Chat'
  );
}

export type TabChatInitialStateOverride = {
  lightMode?: 'light' | 'dark';
  showChatbar?: boolean;
  chatHistory?: boolean;
  chatCompletionURL?: string;
  webSocketMode?: boolean;
  webSocketURL?: string;
  enableIntermediateSteps?: boolean;
  agentApiUrlBase?: string;
  vstApiUrl?: string;
  customAgentParamsJson?: string;
  chatUploadFileEnabled?: boolean;
  chatUploadFileConfigTemplateJson?: string;
  chatUploadFileMetadataEnabled?: boolean;
  chatUploadFileHiddenMessageTemplate?: string;
  themeChangeButtonEnabled?: boolean;
  interactionModalCancelEnabled?: boolean;
  chatInputMicEnabled?: boolean;
  chatMessageEditEnabled?: boolean;
  chatMessageSpeakerEnabled?: boolean;
  chatMessageCopyEnabled?: boolean;
};

/**
 * Builds initial state override for a tab's embedded chat from
 * NEXT_PUBLIC_${tabKey}_CHAT_* env vars (falling back to main NEXT_PUBLIC_* chat vars).
 */
export function getTabChatInitialStateOverride(
  tabKey: string,
): TabChatInitialStateOverride {
  const lightMode = getBool(tabKey, 'DARK_THEME_DEFAULT', 'NEXT_PUBLIC_DARK_THEME_DEFAULT')
    ? 'dark'
    : 'light';
  const showChatbar = !getBool(
    tabKey,
    'SIDE_CHATBAR_COLLAPSED',
    'NEXT_PUBLIC_SIDE_CHATBAR_COLLAPSED',
  );

  return {
    lightMode,
    showChatbar,
    chatHistory: getBool(
      tabKey,
      'CHAT_HISTORY_DEFAULT_ON',
      'NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON',
    ),
    chatCompletionURL:
      get(tabKey, 'HTTP_CHAT_COMPLETION_URL', 'NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL') ||
      undefined,
    webSocketMode: getBool(
      tabKey,
      'WEB_SOCKET_DEFAULT_ON',
      'NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON',
    ),
    webSocketURL:
      get(tabKey, 'WEBSOCKET_CHAT_COMPLETION_URL', 'NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL') ||
      undefined,
    enableIntermediateSteps: getBool(
      tabKey,
      'ENABLE_INTERMEDIATE_STEPS',
      'NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS',
    ),
    agentApiUrlBase:
      get(tabKey, 'AGENT_API_URL_BASE', 'NEXT_PUBLIC_AGENT_API_URL_BASE') ||
      undefined,
    vstApiUrl:
      get(tabKey, 'VST_API_URL', 'NEXT_PUBLIC_VST_API_URL') ||
      undefined,
    customAgentParamsJson:
      get(tabKey, 'CHAT_API_CUSTOM_AGENT_PARAMS_JSON', 'NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON') ||
      undefined,
    chatUploadFileEnabled: getBool(
      tabKey,
      'CHAT_UPLOAD_FILE_ENABLE',
      'NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE',
    ),
    chatUploadFileConfigTemplateJson:
      get(
        tabKey,
        'CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON',
        'NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON',
      ) || undefined,
    chatUploadFileMetadataEnabled: getBool(
      tabKey,
      'CHAT_UPLOAD_FILE_METADATA_ENABLED',
      'NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED',
    ),
    chatUploadFileHiddenMessageTemplate:
      get(
        tabKey,
        'CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE',
        'NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE',
      ) || undefined,
    themeChangeButtonEnabled: getBoolDefaultTrue(
      tabKey,
      'SHOW_THEME_TOGGLE_BUTTON',
      'NEXT_PUBLIC_SHOW_THEME_TOGGLE_BUTTON',
    ),
    interactionModalCancelEnabled: getBoolDefaultTrue(
      tabKey,
      'INTERACTION_MODAL_CANCEL_ENABLED',
      'NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED',
    ),
    chatInputMicEnabled: getBoolDefaultTrue(
      tabKey,
      'CHAT_INPUT_MIC_ENABLED',
      'NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED',
    ),
    chatMessageEditEnabled: getBoolDefaultTrue(
      tabKey,
      'CHAT_MESSAGE_EDIT_ENABLED',
      'NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED',
    ),
    chatMessageSpeakerEnabled: getBoolDefaultTrue(
      tabKey,
      'CHAT_MESSAGE_SPEAKER_ENABLED',
      'NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED',
    ),
    chatMessageCopyEnabled: getBoolDefaultTrue(
      tabKey,
      'CHAT_MESSAGE_COPY_ENABLED',
      'NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED',
    ),
  };
}
