// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "../common/warehouse/nv/js/WarehouseStore";

/**
 * Store util for current UI state (view type, selected place, start time).
 */

const slice = createSlice({
  name: "uiState",
  initialState: {
    // Possible values:
    //   city-level-live
    //   building-level-live
    //   room-level-alert
    //   sensor-level-live
    //   sensor-level-clustering
    viewType: "city-level-live",
    selectedSensorId: "",
    selectedAnomaly: {},
    selectedRoom: "",
    appStartTimeMs: null, // Set on app load from config or current time
  },
  reducers: {
    updateUiState: (state, action) => ({
      ...state,
      ...action.payload,
    }),
  },
});

const UiStateReduxUtil = {
  slice: slice,
};

export default UiStateReduxUtil;
