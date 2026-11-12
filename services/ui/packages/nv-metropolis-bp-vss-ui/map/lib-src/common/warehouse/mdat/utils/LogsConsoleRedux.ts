// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "../../nv/js/WarehouseStore";

/**
 * Utilities for a LogsConsole slice (log list + add/clear actions).
 *
 * @param {string} name LogsConsole unique key
 * @return {object} { slice }
 */
const LogsConsoleReduxUtil = (name) => {
  const addLog = "addLogTo" + name;
  const clearLogs = "clearLogsFrom" + name;
  const slice = createSlice({
    name: name,
    initialState: { logList: [] },
    reducers: {
      [addLog]: (state, action) => ({
        ...state,
        logList: [...state.logList, action.payload],
      }),
      [clearLogs]: (state) => ({
        ...state,
        logList: [],
      }),
    },
  });

  return {
    slice: slice,
  };
};

export default LogsConsoleReduxUtil;
