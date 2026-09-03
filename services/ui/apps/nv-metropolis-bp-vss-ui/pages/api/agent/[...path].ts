// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export const config = {
  api: {
    bodyParser: {
      sizeLimit: "5mb",
    },
  },
};

export { embeddedAgentGatewayHandler as default } from "../../../utils/server/agentGatewayRuntime";
