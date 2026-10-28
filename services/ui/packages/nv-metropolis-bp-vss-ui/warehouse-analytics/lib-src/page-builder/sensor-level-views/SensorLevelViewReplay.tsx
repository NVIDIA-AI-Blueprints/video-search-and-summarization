// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";

import ReplayPeopleMovementDataVisualizer from "./../../component-controllers/ReplayPeopleMovementDataVisualizer";
import ReplayAlertDisplayController from "./../../component-controllers/ReplayAlertDisplayController";
import CameraVideoPresenterWrapper from "./../../component-controllers/CameraVideoPresenterWrapper";
import { Utils } from "common/warehouse";

class SensorLevelViewReplay extends React.Component {
  state = {
    showVideo: true,
  };

  _hasAnimationStarted = false;

  onSelectReplayedEvent = () => {
    this.replayPeopleMovementDataVisualizerRef &&
      this.replayPeopleMovementDataVisualizerRef.hideAnimationIcon();
    this._hasAnimationStarted = false;
    // Used for replaying the same event again on user's request
    this.setState({ showVideo: false });
    this._replayEventTimeoutId = setTimeout(
      () => this.setState({ showVideo: true }),
      100
    );
  };

  // hlsEventListeners = {
  //   onFatalError: () => {
  //     // on fatal error in Hls playback, start anomaly animation without video
  //     this.replayPeopleMovementDataVisualizerRef &&
  //       this.replayPeopleMovementDataVisualizerRef.startReplayPeopleMovement();
  //   },
  //   onFirstFragmentParsed: () => {
  //     this.replayPeopleMovementDataVisualizerRef &&
  //       this.replayPeopleMovementDataVisualizerRef.startReplayPeopleMovement();
  //   },
  // };

  _startAnimation = () => {
    if (this._hasAnimationStarted) return;

    this._startAnimationTimeoutId = setTimeout(() => {
      this._hasAnimationStarted = true;
      this.replayPeopleMovementDataVisualizerRef &&
        this.replayPeopleMovementDataVisualizerRef.startReplayPeopleMovement();
    }, 3000);
  };

  componentWillUnmount() {
    clearTimeout(this._startAnimationTimeoutId);
    clearTimeout(this._replayEventTimeoutId);
  }

  render() {
    let alertsView, videoView, replayPeopleMovementDataVisualizer;
    let cameraSourceInfo = this.props.metadata.sensorsMap.get(
      this.props.uiState.selectedSensorId
    );
    let eventInfo = Object.assign({}, this.props.uiState.selectedAnomaly);
    // In case of SD app, the original tripwire event can be very
    // short in duration (example: 0.1s, 0.3s or 0.5s). Also, the
    // person's locations supplied in the metadata are few points
    // on both the side of a tripwire. But on UI, animating such a
    // short event appears to be less useful. Therefore, the new
    // smoothLocations array in the metadata is used to get more
    // location points beyond the original locations array.

    // The start index of the original locations array within the bigger
    // smoothLocations array is provided as a string in metadata 'index = X'
    // (X: index)
    let indexLocationsInSmoothLocations = parseInt(
      eventInfo.analyticsModule.description.slice(8)
    );

    // Modify start and end times
    // start and end times are orignially based of the original locations array
    // But after modification of the original locations array, we need to adjust
    // start and end times depending upon how many points are included before
    // and after the original locations array
    let numLocationsPrepended = indexLocationsInSmoothLocations;
    let coordinatesLength = 0;
    if(eventInfo.smoothLocations) {
      coordinatesLength = eventInfo.smoothLocations.coordinates.length;
    } else {
      coordinatesLength = eventInfo.locations.coordinates.length;
    }
    let numLocationsAppended =
      coordinatesLength -
      (indexLocationsInSmoothLocations +
        eventInfo.locations.coordinates.length);

    let perLocationPerSecond =
      eventInfo.timeInterval / eventInfo.locations.coordinates.length;

    let prependDurationMs = perLocationPerSecond * numLocationsPrepended * 1000;
    let appendDurationMs = perLocationPerSecond * numLocationsAppended * 1000;

    eventInfo.timeInterval =
      prependDurationMs / 1000 +
      eventInfo.timeInterval +
      appendDurationMs / 1000;

    eventInfo.timestamp = new Date(
      new Date(eventInfo.timestamp).getTime() - prependDurationMs
    ).toISOString();

    eventInfo.end = new Date(
      new Date(eventInfo.timestamp).getTime() + eventInfo.timeInterval * 1000
    ).toISOString();

    eventInfo.locations = eventInfo.smoothLocations || eventInfo.locations;

    let cameraSourceMedia;
    cameraSourceInfo.attributes.forEach((element) => {
      if (element.name === "source") {
        cameraSourceMedia = element.value;
      }
    });

    if (
      cameraSourceMedia === "nvstreamer" ||
      cameraSourceMedia === "vst" ||
      cameraSourceMedia !== "mms"
    ) {
      // since tripwire events are very short, play only the video for longer and start animation
      // after initial padding duration
      let modifiedEventForVideo = Object.assign({}, eventInfo, {
        timeInterval: eventInfo.timeInterval + 6,
        timestamp: new Date(
          new Date(eventInfo.timestamp).getTime() - 3000
        ).toISOString(),
        end: new Date(new Date(eventInfo.end).getTime() + 3000).toISOString(),
      });

      videoView = (
        <CameraVideoPresenterWrapper
          mode="recorded"
          mediaServiceBaseUrl={Utils.getMediaServiceBaseUrl(cameraSourceInfo)}
          cameraSourceInfo={cameraSourceInfo}
          replayEventInfo={modifiedEventForVideo}
          // hlsEventListeners={this.hlsEventListeners}
          onWebRtcStreamingError={this._startAnimation}
          isRemoveDashCV={this.props.appConfig.isRemoveDashCV}
          streamActionHandlers={{
            onStart: this._startAnimation,
          }}
        />
      );
    } else {
      console.warn(
        cameraSourceInfo.id +
          ' has unsupported media source category - "' +
          cameraSourceMedia +
          '"'
      );
    }

    replayPeopleMovementDataVisualizer = (
      <ReplayPeopleMovementDataVisualizer
        ref={(ref) => (this.replayPeopleMovementDataVisualizerRef = ref)}
        replayEventInfo={eventInfo}
      />
    );

    alertsView = (
      <ReplayAlertDisplayController
        onClickReplayEvent={this.onSelectReplayedEvent.bind(this)}
      />
    );

    return (
      <React.Fragment>
        {this.state.showVideo ? videoView : null}
        {replayPeopleMovementDataVisualizer}
        {alertsView}
      </React.Fragment>
    );
  }
}

export default connect((state) => ({
  appConfig: state.appConfig,
  metadata: state.metadata,
  uiState: state.uiState,
}))(SensorLevelViewReplay);
