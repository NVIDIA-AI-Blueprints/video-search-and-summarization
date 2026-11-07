// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";

import LivePeopleMovementDataVisualizer from "./../../component-controllers/LivePeopleMovementDataVisualizer";
import SensorKPIDisplayer from "./../../component-controllers/SensorKPIDisplayer";
import TripwireAlertsDisplayController from "./../../component-controllers/LiveTripwireAlertsDisplayController";
import ROIAlertsDisplayController from "./../../component-controllers/LiveROIAlertsDisplayController";
import CameraVideoPresenterWrapper from "./../../component-controllers/CameraVideoPresenterWrapper";
import { AppConstants, Utils } from "common/warehouse";

class SensorLevelViewLive extends React.Component {
  state = {
    showVideo: true,
  };

  componentDidMount() {
    let videoRefreshIntervalMin = this.props.appConfig
      .liveSyncVideoRefreshIntervalMin
      ? this.props.appConfig.liveSyncVideoRefreshIntervalMin
      : 3;
    this._videoRefreshIntervalId = setInterval(
      () => {
        this.setState({ showVideo: false });
        this._videoRefreshTimerId = setTimeout(() => {
          this.setState({ showVideo: true });
        }, 100);
      },
      videoRefreshIntervalMin * 60 * 1000
    );
  }

  render() {
    let videoView,
      kpiView,
      livePeopleMovementDataVisualizer,
      alertsView,
      roiAlertsView,
      cameraInfo = this.props.metadata.sensorsMap.get(
        this.props.uiState.selectedSensorId
      );

    let cameraSourceMedia;
    cameraInfo.attributes.forEach((element) => {
      if (element.name === "source") {
        cameraSourceMedia = element.value;
      }
    });

    if (this.state.showVideo) {
      if (
        cameraSourceMedia === "nvstreamer" ||
        cameraSourceMedia === "vst" ||
        cameraSourceMedia === "mms"
      ) {
        let videoStartTimeMs = -1;
        videoView = (
          <CameraVideoPresenterWrapper
            mode="live"
            mediaServiceBaseUrl={Utils.getMediaServiceBaseUrl(cameraInfo)}
            endpointPtsBaseUrl={AppConstants.API_URL_BASE}
            cameraSourceInfo={cameraInfo}
            uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
            onWebRtcStreamingError={() => {
              this._livePeopleMovementVisualizerRef &&
                this._livePeopleMovementVisualizerRef.startRequestCyclePeopleDataForSensor();
            }}
            isRemoveDashCV={this.props.appConfig.isRemoveDashCV}
            streamActionHandlers={{
              onStart: (startTimeMs) => {
                if (
                  cameraSourceMedia === "vst" ||
                  cameraSourceMedia === "mms"
                ) {
                  videoStartTimeMs = startTimeMs;
                }
                this._livePeopleMovementVisualizerRef &&
                  this._livePeopleMovementVisualizerRef.startRequestCyclePeopleDataForSensor(
                    videoStartTimeMs
                  );
              },
            }}
          />
        );
      } else {
        console.warn(
          cameraInfo.id +
            ' has unsupported media source category - "' +
            cameraSourceMedia +
            '"'
        );
      }
    }

    kpiView = <SensorKPIDisplayer />;

    if (this.state.showVideo) {
      livePeopleMovementDataVisualizer = (
        <LivePeopleMovementDataVisualizer
          ref={(ref) => (this._livePeopleMovementVisualizerRef = ref)}
        />
      );
    }

    alertsView = (
      <TripwireAlertsDisplayController
        onSelectAlert={this.props.onRequestToReplayAlertEvent}
        left="0.6%"
        top="6%"
        height="40%"
        width="20%"
      />
    );

    roiAlertsView = (
      <ROIAlertsDisplayController
        onSelectAlert={this.props.onRequestToReplayAlertEvent}
        left="0.6%"
        top="47%"
        height="calc(53% - 3.25rem)"
        width="20%"
      />
    );

    return (
      <React.Fragment>
        {videoView}
        {kpiView}
        {livePeopleMovementDataVisualizer}
        {alertsView}
        {roiAlertsView}
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    clearTimeout(this._videoRefreshTimerId);
    clearInterval(this._videoRefreshIntervalId);
  }
}

export default connect((state) => ({
  appConfig: state.appConfig,
  metadata: state.metadata,
  uiState: state.uiState,
}))(SensorLevelViewLive);
