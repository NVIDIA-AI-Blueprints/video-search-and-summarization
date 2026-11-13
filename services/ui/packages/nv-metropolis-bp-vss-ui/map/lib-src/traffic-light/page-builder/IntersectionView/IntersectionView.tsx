// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import {
  Widget,
  AppConstants,
  ReduxUtils,
  Utils,
  LoaderDisplay,
  connect,
} from "common/warehouse";

import AlertsPanel from "../../components/AlertsPanel/AlertsPanel";
import UiStateReduxUtil from "../../utils/UiStateReduxUtil";

import classes from "./IntersectionView.module.css";
import CameraVideoPresenterWrapper from "../../components/CameraVideoPresenterWrapper";

class IntersectionView extends React.Component {
  constructor(props) {
    super(props);

    this.state = {
      showVideo: true,
    };
  }

  _beginAnomalyAnimation = () => {
    if (this.props.uiState.viewType !== "intersection-level-alert") return;
    if (this.props.uiState.startAnomalyAnimation === true) return;
    this.props.updateUiState({
      startAnomalyAnimation: true,
    });
  };

  render() {
    let cameraVideoPresenterElement;
    let cameraSourceInfo = this.props.metadata.sensorsMap.get(
      this.props.uiState.selectedSensorId
    );
    let cameraVideoPresenterProps = cameraSourceInfo
      ? {
          cameraSourceInfo: cameraSourceInfo,
          uiDelaySeconds: this.props.appConfig.uiDelaySeconds,
          mediaServiceBaseUrl: Utils.getMediaServiceBaseUrl(cameraSourceInfo),
          endpointPtsBaseUrl: AppConstants.API_URL_BASE,
        }
      : null;

    if (cameraVideoPresenterProps) {
      if (this.props.uiState.viewType === "intersection-level-alert") {
        Object.assign(cameraVideoPresenterProps, {
          mode: "recorded",
          replayEventInfo: this.props.uiState.selectedAnomaly,
          onWebRtcStreamingError: this._beginAnomalyAnimation,
          streamActionHandlers: {
            onStart: this._beginAnomalyAnimation,
          },
        });
      } else if (this.props.uiState.viewType === "intersection-level-live") {
        Object.assign(cameraVideoPresenterProps, {
          mode: "live",
        });
      }

      let cameraSourceMedia;
      cameraSourceInfo.attributes &&
        cameraSourceInfo.attributes.forEach((element) => {
          if (element.name === "source") {
            cameraSourceMedia = element.value;
          }
        });

      if (
        cameraSourceMedia &&
        (cameraSourceMedia === "nvstreamer" ||
          cameraSourceMedia === "vst" ||
          cameraSourceMedia !== "mms")
      ) {
        cameraVideoPresenterElement = (
          <CameraVideoPresenterWrapper {...cameraVideoPresenterProps} />
        );
      } else {
        console.warn(
          cameraSourceInfo.id + " has media category unsupported in MDX"
        );
      }
    }

    const showStartingReplayOverlay =
      this.props.uiState.viewType === "intersection-level-alert" &&
      this.props.uiState.selectedAnomaly &&
      this.props.uiState.startAnomalyAnimation !== true;

    this._hasReplayVideo = Boolean(cameraVideoPresenterElement);

    return (
      <div className={classes.container}>
        <Widget
          left=".5vw"
          top="4.5vw"
          height="16.875vw"
          width="15vw"
          showShadow={true}
        >
          <AlertsPanel
            panelTitle="Alerts"
            panelStyle="red"
            alertClickHandler={() => {
              if (this.props.uiState.viewType === "intersection-level-alert") {
                this.setState({ showVideo: false });
                this._replayEventTimeoutId = setTimeout(
                  () => this.setState({ showVideo: true }),
                  100
                );
              }
              this.props.alertClickHandler &&
                this.props.alertClickHandler(
                  this.props.uiState.selectedAnomaly
                );
            }}
          />
        </Widget>
        {this.state.showVideo && cameraVideoPresenterElement}
        {showStartingReplayOverlay && (
          <div
            className={classes.replayOverlay}
            data-testid="starting-event-replay"
          >
            <LoaderDisplay
              type={LoaderDisplay.TYPE.LOADING}
              size={LoaderDisplay.SIZE.MEDIUM}
              userMessage="Starting event replay"
              backgroundOpacity={0}
            />
          </div>
        )}
      </div>
    );
  }

  componentDidUpdate() {
    if (
      this.props.uiState.viewType === "intersection-level-alert" &&
      this.props.uiState.selectedAnomaly &&
      this.props.uiState.startAnomalyAnimation !== true &&
      !this._hasReplayVideo
    ) {
      this._beginAnomalyAnimation();
    }
  }

  componentWillUnmount() {
    clearTimeout(this._replayEventTimeoutId);
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(UiStateReduxUtil.slice.actions)
)(IntersectionView);
