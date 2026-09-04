// SPDX-License-Identifier: MIT

import {
  agentChatBridgeHandler,
  isAgentAdapterConfigured,
} from "../../utils/server/agentChatBridge";
import { chatApiHandler } from "@nemo-agent-toolkit/ui/server";
import type { NextApiRequest, NextApiResponse } from "next";

export const config = {
  api: {
    bodyParser: {
      sizeLimit: "5mb",
    },
  },
};

export default async function chatHandler(
  req: NextApiRequest,
  res: NextApiResponse
): Promise<void> {
  if (isAgentAdapterConfigured()) {
    await agentChatBridgeHandler(req, res);
    return;
  }
  await chatApiHandler(req, res);
}
