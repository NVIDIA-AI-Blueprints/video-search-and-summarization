// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";
import { Chip as Chip } from "common/warehouse/mui";

import { BeautifyNameString } from "common/warehouse";
import {
  PlaceHeader,
  PlaceHeaderContent,
  ReduxUtils,
} from "common/warehouse";

import TrajectoryClusterView from "./sensor-level-views/TrajectoryClusterView";
import SensorLevelViewLive from "./sensor-level-views/SensorLevelViewLive";
import SensorLevelViewReplay from "./sensor-level-views/SensorLevelViewReplay";
import UiStateReduxUtil from "./../utils/UiStateReduxUtil";
import { joinPlacePath } from "../utils/joinPlacePath";
import CssClasses from "./SensorLevelView.module.css";

// Keyframe animation for the live indicator
const liveIndicatorAnimation = `
@keyframes livePulse {
  0% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.3);
    opacity: 0.5;
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}
`;

// Inject animation styles
if (typeof document !== 'undefined') {
  const styleSheet = document.createElement("style");
  styleSheet.textContent = liveIndicatorAnimation;
  if (!document.head.querySelector('style[data-live-indicator]')) {
    styleSheet.setAttribute('data-live-indicator', 'true');
    document.head.appendChild(styleSheet);
  }
}

let CssLiveIcon = {
  display: "inline-block",
  width: "8px",
  height: "8px",
  borderRadius: "50%",
  backgroundColor: "#ffffff",
  marginRight: "6px",
  animation: "livePulse 1.5s ease-in-out infinite",
  boxShadow: "0 0 12px rgba(255, 255, 255, 0.95), 0 0 6px rgba(255, 255, 255, 1)",
};

class SensorLevelView extends React.PureComponent {
  onClickButtonBackToLive = () => {
    this.props.updateUiState({
      viewType: "sensor-level-live",
    });
  };

  onRequestToReplayAlertEvent = (e, eventInfo) => {
    this.props.updateUiState({
      selectedAnomaly: eventInfo,
      viewType: "sensor-level-alert",
    });
  };

  render() {
    let buttonBackToLive, viewContent;

    if (this.props.uiState.viewType === "sensor-level-alert") {
      buttonBackToLive = (
        <span className={CssClasses.headerControl}>
          <Chip
            onClick={this.onClickButtonBackToLive.bind(this)}
            color="success"
            variant="filled"
            label="Go Back Live"
            className={CssClasses.headerChip}
          />
        </span>
      );

      viewContent = <SensorLevelViewReplay />;
    } else if (this.props.uiState.viewType === "sensor-level-live") {
      buttonBackToLive = (
        <span className={CssClasses.headerControl}>
          <Chip
            color="success"
            variant="filled"
            label={
              <span style={{ display: "flex", alignItems: "center" }}>
                <span style={CssLiveIcon}></span>
                Live
              </span>
            }
            className={CssClasses.headerChip}
            style={{ cursor: "default" }}
          />
        </span>
      );

      viewContent = (
        <SensorLevelViewLive
          onRequestToReplayAlertEvent={this.onRequestToReplayAlertEvent.bind(
            this
          )}
        />
      );
    } else if (this.props.uiState.viewType === "sensor-level-clustering") {
      viewContent = <TrajectoryClusterView />;
    }

    return (
      <React.Fragment>
        <PlaceHeader>
          <PlaceHeaderContent
            uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
            placeName={joinPlacePath(
              BeautifyNameString(this.props.metadata.cityConfig.name),
              BeautifyNameString(this.props.uiState.selectedBuilding),
              BeautifyNameString(this.props.uiState.selectedRoom),
              BeautifyNameString(this.props.uiState.selectedSensorId),
            )}
            onClickTitle={this.props.onClickForBackToSensorsView}
            showTooltip={true}
          >
            {buttonBackToLive}
          </PlaceHeaderContent>
        </PlaceHeader>
        {viewContent}
      </React.Fragment>
    );
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(UiStateReduxUtil.slice.actions)
)(SensorLevelView);
