// SPDX-License-Identifier: MIT
import { env } from 'next-runtime-env';

const VST_API_URL = env('NEXT_PUBLIC_VST_API_URL') || process?.env?.NEXT_PUBLIC_VST_API_URL;
const AGENT_API_URL_BASE = env('NEXT_PUBLIC_AGENT_API_URL_BASE') || process?.env?.NEXT_PUBLIC_AGENT_API_URL_BASE;
const CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON = env('NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON') || process?.env?.NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON;
const ENABLE_ADD_RTSP_BUTTON = env('NEXT_PUBLIC_VIDEO_MANAGEMENT_TAB_ADD_RTSP_ENABLE') || process?.env?.NEXT_PUBLIC_VIDEO_MANAGEMENT_TAB_ADD_RTSP_ENABLE;
const ENABLE_VIDEO_UPLOAD = env('NEXT_PUBLIC_VIDEO_MANAGEMENT_VIDEO_UPLOAD_ENABLE') || process?.env?.NEXT_PUBLIC_VIDEO_MANAGEMENT_VIDEO_UPLOAD_ENABLE;

export async function fetchVideoManagementData() {
  await new Promise(resolve => setTimeout(resolve, 100));

  return {
    systemStatus: 'operational',
    vstApiUrl: VST_API_URL || null,
    agentApiUrl: AGENT_API_URL_BASE || null,
    chatUploadFileConfigTemplateJson: CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON || null,
    enableAddRtspButton: ENABLE_ADD_RTSP_BUTTON !== 'false',
    enableVideoUpload: ENABLE_VIDEO_UPLOAD !== 'false',
  };
}
