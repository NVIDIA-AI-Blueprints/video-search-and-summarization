// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "../../common/warehouse/shell/js/WarehouseStore";

/**
 * Store util for current UI state (view type, selected intersection, start time).
 */

const slice = createSlice({
  name: "uiState",
  initialState: {
    // Possible values:
    //   city-level-live
    //   intersection-level-live
    //   intersection-level-alert
    //   sensor-level-clustering
    viewType: "city-level-live",
    startAnomalyAnimation: false,
    anomalyReplaySeq: 0,
    selectedSensorId: undefined,
    selectedAnomaly: undefined,
    selectedCorridor: undefined,
    selectedIntersection: undefined,
    appStartTimeMs: null,
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
