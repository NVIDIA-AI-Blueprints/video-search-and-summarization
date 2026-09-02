// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

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
