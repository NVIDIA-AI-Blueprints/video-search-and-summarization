// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { MsToHMSsssString } from "../../utils/Utils";
import RecordedSensorWebrtcVideo from "./RecordedSensorWebrtcVideo";
import React from "react";

class EventWebrtcVideo extends React.PureComponent {
  render() {
    if (!this.props.sensorConfig) {
      console.error("EventWebrtcVideo: No correspondig sensor config passed");
      return "EventWebrtcVideo: No correspondig sensor config passed";
    }

    let startTime, endTime, targetObjectIds;
    let sensorConfig = this.props.sensorConfig;
    let sensorVideoSource;

    sensorConfig &&
      sensorConfig.attributes.forEach((pair) => {
        if (pair.name === "source") sensorVideoSource = pair.value;
      });

    if (sensorVideoSource === "nvstreamer") {
      let fps = 30;
      sensorConfig.attributes.forEach((pair) => {
        if (pair.name === "fps") fps = parseInt(pair.value);
      });

      let startTimeMs, endTimeMs;
      if (this.props.eventData.type === "mdx-behavior") {
        endTimeMs =
          Math.floor(
            parseInt(this.props.eventData.videoPath.split("-")[1]) / fps
          ) * 1000;
      } else {
        endTimeMs = Math.floor((this.props.eventData.endFrame / fps) * 1000);
      }
      startTimeMs =
        endTimeMs -
        (new Date(this.props.eventData.end).getTime() -
          new Date(this.props.eventData.timestamp).getTime());
      startTime = MsToHMSsssString(startTimeMs);
      endTime = MsToHMSsssString(endTimeMs);
    } else if (sensorVideoSource === "vst" || sensorVideoSource === "mms") {
      startTime = this.props.eventData.timestamp;
      endTime = this.props.eventData.end;
    } else {
      console.error(
        "EventWebrtcVideo: Unrecognized media source (" +
          sensorVideoSource +
          ") for sensor " +
          this.props.sensorConfig.id
      );
      return (
        "EventWebrtcVideo: Unrecognized media source (" +
        sensorVideoSource +
        ") for sensor " +
        this.props.sensorConfig.id
      );
    }

    if (this.props.eventData.object && this.props.eventData.object.id) {
      targetObjectIds = [this.props.eventData.object.id];
    } else if (this.props.eventData.objectId && this.props.eventData.objectId) {
      targetObjectIds = [this.props.eventData.objectId];
    }

    if (!startTime || !endTime) {
      console.error("EventWebrtcVideo: No start or end time for playing video");
      return "No start or end time for playing video";
    }

    return (
      <RecordedSensorWebrtcVideo
        {...this.props}
        ref={(ref) => (this.videoRef = ref)}
        startTime={startTime}
        endTime={endTime}
        targetObjectIds={targetObjectIds}
      />
    );
  }
}

export default EventWebrtcVideo;
