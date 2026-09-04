// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0

import type { NextApiRequest, NextApiResponse } from "next";

import {
  agentChatBridgeHandler,
  isAgentAdapterConfigured,
} from "../../utils/server/agentChatBridge";
import vssChatHandler from "./vss-chat";

export const config = {
  api: {
    bodyParser: {
      sizeLimit: "5mb",
    },
  },
};

/**
 * Compatibility endpoint retained for existing deployments and clients.
 *
 * External-agent deployments bridge the embedded #1980 adapter into the
 * legacy text stream. Other deployments use the toolkit-free chat-SSE proxy.
 */
export default async function chatHandler(
  req: NextApiRequest,
  res: NextApiResponse,
): Promise<void> {
  if (isAgentAdapterConfigured()) {
    await agentChatBridgeHandler(req, res);
    return;
  }
  await vssChatHandler(req, res);
}
