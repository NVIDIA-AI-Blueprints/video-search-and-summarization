// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import {
  connect, BeautifyNameString } from "common/map";
import {
  PlaceHeader,
  PlaceHeaderContent,
  PlaceHeaderLiveControl,
  ReduxUtils,
} from "common/map";

import TrajectoryClusterView from "./sensor-level-views/TrajectoryClusterView";
import SensorLevelViewLive from "./sensor-level-views/SensorLevelViewLive";
import SensorLevelViewReplay from "./sensor-level-views/SensorLevelViewReplay";
import UiStateReduxUtil from "./../utils/UiStateReduxUtil";
import { joinPlacePath } from "../utils/joinPlacePath";

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
        <PlaceHeaderLiveControl
          isLive={false}
          onGoBackLive={this.onClickButtonBackToLive}
        />
      );

      viewContent = <SensorLevelViewReplay />;
    } else if (this.props.uiState.viewType === "sensor-level-live") {
      buttonBackToLive = <PlaceHeaderLiveControl isLive={true} />;

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
