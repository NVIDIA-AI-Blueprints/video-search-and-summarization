// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { LogsConsoleReduxUtil } from "common/warehouse/mdat";
import { AppConstants } from "./UrlConstants";

const AppLogsConsoleReduxUtil = LogsConsoleReduxUtil(
  AppConstants.APP_LOGS_CONSOLE_NAME
);

export default AppLogsConsoleReduxUtil;
