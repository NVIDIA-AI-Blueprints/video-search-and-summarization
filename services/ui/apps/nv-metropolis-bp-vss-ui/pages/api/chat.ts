// SPDX-License-Identifier: MIT

import {
  agentGatewayChatHandler,
  isAgentGatewayConfigured,
} from "../../utils/server/agentGateway";
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
  if (isAgentGatewayConfigured()) {
    await agentGatewayChatHandler(req, res);
    return;
  }
  await chatApiHandler(req, res);
}
