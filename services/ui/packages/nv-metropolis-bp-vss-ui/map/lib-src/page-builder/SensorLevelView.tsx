// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";

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
          <span
            className={`${CssClasses.liveBadge} ${CssClasses.replayBadge}`}
            role="button"
            tabIndex={0}
            onClick={this.onClickButtonBackToLive.bind(this)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                this.onClickButtonBackToLive();
              }
            }}
          >
            Go Back Live
          </span>
        </span>
      );

      viewContent = <SensorLevelViewReplay />;
    } else if (this.props.uiState.viewType === "sensor-level-live") {
      buttonBackToLive = (
        <span className={CssClasses.headerControl}>
          <span className={CssClasses.liveBadge}>
            <span className={CssClasses.liveDot} aria-hidden="true" />
            Live
          </span>
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
