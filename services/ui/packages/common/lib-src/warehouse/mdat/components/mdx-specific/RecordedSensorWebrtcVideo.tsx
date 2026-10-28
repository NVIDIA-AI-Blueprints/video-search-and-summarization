// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { isAbortError, readJson } from "common/warehouse";
import { PlayDisabled as PlayDisabledIcon } from "common/warehouse/mui-icons";

import { VideoElementDefaultConfig } from "../../utils/Utils";
import LoaderDisplay from "../generic/LoaderDisplay/LoaderDisplay";
import UserMessage from "../../utils/UserMessage";
import WebrtcVideo from "../generic/WebrtcVideo/WebrtcVideo";

// TODO: lot duplicat of CameraVideoPresenter
export default class RecordedSensorWebrtcVideo extends React.PureComponent {
  state = {
    loadState: "loading",
  };

  UNSAFE_componentWillMount() {
    if (
      !this.props.sensorConfig ||
      !this.props.startTime ||
      !this.props.endTime
    ) {
      console.error(
        "RecordedSensorWebrtcVideo: No start/end time or correspondig sensor config passed"
      );
      this.setState({ loadState: "erred" });
      return;
    }

    this.abortControllerMediaServiceDeviceListQuery = new AbortController();

    let getMediaServiceDeviceListCallPurpose =
      "Get MediaService deviceId for sensor " + this.props.sensorConfig.id;

    fetch(`${this.props.endpoints.mediaServerBaseUrl}/sensor/list`, {
      signal: this.abortControllerMediaServiceDeviceListQuery.signal,
    })
      .then((response) => readJson(response))
      .then((deviceList) => {
        let mediaStreamerSensorId;
        if (!deviceList) {
          throw Error("No data sent from the server");
        }
        deviceList.forEach((deviceData) => {
          if (
            deviceData.name.split(".")[0] === this.props.sensorConfig.id &&
            deviceData.state === "online"
          ) {
            mediaStreamerSensorId = deviceData.sensorId;

            let sensorConfig = this.props.sensorConfig;

            // Construct webrtc connection info
            this._webrtcVideoConnectionInfo = {
              serverUrl: `${this.props.endpoints.mediaServerBaseUrl}/replay`,
              videoParams: {
                streamId: mediaStreamerSensorId,
                startTime: this.props.startTime,
                endTime: this.props.endTime,
                options: {
                  quality: "auto",
                  rtptransport: "udp",
                  timeout: 60,
                  overlay: {
                    enabled: this.props.targetObjectIds ? true : false,
                    objectId: this.props.targetObjectIds
                      ? this.props.targetObjectIds
                      : [],
                    color: "red",
                    thickness: 20,
                    needBbox: this.props.targetObjectIds ? true : false,
                  },
                },
              },
            };

            this.setState({ loadState: "loaded" });
          }
        });
        if (!mediaStreamerSensorId) {
          throw Error(
            "Unable to get online media service device ID for sensor " +
              this.props.sensorConfig.id
          );
        }
      })
      .catch((thrown) => {
        this.setState({ loadState: "erred" });

        if (isAbortError(thrown)) {
          console.warn(
            getMediaServiceDeviceListCallPurpose + " : " + thrown.message
          );
          return;
        }
        if (!thrown.config) {
          console.error(
            getMediaServiceDeviceListCallPurpose + ": " + thrown.message
          );
          return;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getMediaServiceDeviceListCallPurpose,
          thrown
        );
        console.error(userMessage.getLongString());
      });
  }

  render() {
    // loading
    let content = (
      <LoaderDisplay
        type={LoaderDisplay.TYPE.LOADING}
        size={LoaderDisplay.SIZE.SMALL}
      />
    );

    if (this.state.loadState === "loaded") {
      content = (
        <WebrtcVideo
          {...this.props}
          ref={(ref) => (this.videoElRef = ref)}
          videoConnectionInfo={this._webrtcVideoConnectionInfo}
          htmlVideoTagOptions={VideoElementDefaultConfig.htmlTagOptions}
          videoControls={{ playpause: true }}
          onWebRtcStreamingError={() => {
            this.setState({
              loadState: "erred",
            });
            this.props.onWebRtcStreamingError &&
              this.props.onWebRtcStreamingError();
          }}
        />
      );
    } else if (this.state.loadState === "erred") {
      content = (
        <React.Fragment>
          <PlayDisabledIcon />
          {"Unable to play Video"}
        </React.Fragment>
      );
    }

    return content;
  }

  componentWillUnmount() {
    this.abortControllerMediaServiceDeviceListQuery &&
      this.abortControllerMediaServiceDeviceListQuery.abort();
  }
}
