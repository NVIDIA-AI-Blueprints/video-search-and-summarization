// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "@reduxjs/toolkit";

/**
 * Utilities for generating reusable Redux based API for a LogsConsole operation.
 * Note: Familiarity with Redux, React-redux is assumed.
 * Usage: Wrap target components with any of the returned connect functions, and
 *   add returned reducer while configuring store.
 *
 * @param {string} name LogsConsole unique key
 * @return {object} {
 *   connect,  // React-redux connect similar
 *   connectDispatchOnly,  // React-redux connect similar
 *   reducer,  // to configure store
 *   middleware,  // to configure store
 * }
 *
 */
const LogsConsoleReduxUtil = (name) => {
    // LogsConsole Slice
  const slice = createSlice({
    name: name,
    initialState: { logList: [] },
    reducers: Object.fromEntries([
      [
        "addLogTo" + name,
        function (state, action) {
          state.logList.push(action.payload);
        },
      ],
      [
        "clearLogsFrom" + name,
        function (state) {
          state.logList = [];
        },
      ],
    ]),
  });

  return {
    slice: slice,
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({
        serializableCheck: {
          // avoid non serializable console errors since logList is not serializable
          // https://redux-toolkit.js.org/usage/usage-guide#working-with-non-serializable-data
          ignoredPaths: ["logList"],
        },
      }),
  };
};

export default LogsConsoleReduxUtil;
