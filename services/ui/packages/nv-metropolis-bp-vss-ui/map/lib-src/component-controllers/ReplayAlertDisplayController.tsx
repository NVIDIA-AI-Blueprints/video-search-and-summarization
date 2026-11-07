// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";

import AlertsPanel from "../components/AlertsPanel/AlertsPanel";
import { EventCard } from "common/warehouse";
import { FramesUrl, Utils } from "common/warehouse";

/**
 * Manages the content to be shown in the Alerts Panel during alert playback mode
 */
const ReplayAlertDisplayController = (props) => {
  // Determine the event type based on analyticsModule.id
  let title = "Selected Event";
  if (props.uiState.selectedAnomaly.analyticsModule) {
    if (props.uiState.selectedAnomaly.analyticsModule.id === "TripEvent") {
      title = "Selected Tripwire Event";
    } else if (props.uiState.selectedAnomaly.analyticsModule.id === "ROIEvent") {
      title = "Selected ROI Event";
    }
  }

  return (
    <AlertsPanel
      title={title}
      left="0.6%"
      top="6%"
      height="94%"
      width="20%"
    >
      <EventCard
        {...{
          endpoints: {
            framesUrl: FramesUrl,
            mediaServerBaseUrl: Utils.getMediaServiceBaseUrl(
              props.metadata.sensorsMap.get(
                props.uiState.selectedAnomaly.sensor.id
              )
            ),
          },
          data: props.uiState.selectedAnomaly,
          sensorConfig: props.metadata.sensorsMap.get(
            props.uiState.selectedAnomaly.sensor.id
          ),
          highlighted: true,
          isRemoveDashCV: props.appConfig.isRemoveDashCV,
          onClick: props.onClickReplayEvent,
        }}
      />
    </AlertsPanel>
  );
};

export default connect((state) => ({
  appConfig: state.appConfig,
  uiState: state.uiState,
  metadata: state.metadata,
}))(ReplayAlertDisplayController);
