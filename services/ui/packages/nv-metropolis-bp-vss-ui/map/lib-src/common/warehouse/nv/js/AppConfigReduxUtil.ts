// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "@reduxjs/toolkit";

/**
 * Redux Util to add Application config to redux store
 */

const slice = createSlice({
  name: "appConfig",
  initialState: {},
  reducers: {
    updateAppConfig: function (state, action) {
      return action.payload;
    },
  },
});

const AppConfigReduxUtil = {
  slice: slice,
};

export default AppConfigReduxUtil;
