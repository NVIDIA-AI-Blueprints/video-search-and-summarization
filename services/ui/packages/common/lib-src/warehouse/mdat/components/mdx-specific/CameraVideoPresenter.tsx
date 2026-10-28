// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { joinUrl, isAbortError, readJson } from "common/warehouse";
import DashVideo from "../generic/DashVideo/DashVideo";
import { vstHttpOrigin } from "../generic/DashVideo/vstHttpOrigin";
import UserMessage from "../../utils/UserMessage";
import { FramesUrlPtsSuffix } from "../../utils/UrlConstants";


export default class CameraVideoPresenter extends React.Component {
  state = {
    mediaStreamerSensorId: undefined,
  };

  getVideoSourceLivePts = (
    props,
    mediaStreamerSensorId,
    fileSourceMetadata
  ) => {
    this.abortControllerVideoPts = new AbortController();

    let getCallPurpose =
      "Get NvStreamer Live PTS for sensor " + props.cameraSourceInfo.id;

    let currentTimestamp = new Date(
      Date.now() - (props.uiDelaySeconds ? props.uiDelaySeconds * 1000 : 0)
    ).toISOString();

    fetch(joinUrl(`${props.endpointPtsBaseUrl}${FramesUrlPtsSuffix}`, {
        sensorId: props.cameraSourceInfo.id,
        timestamp: currentTimestamp,
      }), {
      signal: this.abortControllerVideoPts.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        if (!data) {
          throw Error("No data sent from the server");
        }
        if (!data.pts) {
          throw Error("No Live pts in data sent from the server");
        }
        this.setState({
          mediaStreamerSensorId: mediaStreamerSensorId,
          fileSourceMetadata: fileSourceMetadata,
          sensorLivePts: data.pts,
        });
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          console.warn(getCallPurpose + " : " + thrown.message);
          return;
        }

        this._onWebRtcStreamingError();

        if (!thrown.config) {
          console.error(getCallPurpose + ": " + thrown.message);
          return;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurpose,
          thrown
        );
        console.error(userMessage.getLongString());
      });
  };

  getNvStreamerVideoSourceFileMetadata = (props, mediaStreamerSensorId) => {
    this.abortControllerMediaServiceFileMetadata = new AbortController();

    let getCallPurpose =
      "Get Media Service source file metadata for sensor " +
      props.cameraSourceInfo.id;

    fetch(joinUrl(props.mediaServiceBaseUrl + "/file/metadata", {
        fileId: mediaStreamerSensorId,
      }), {
      signal: this.abortControllerMediaServiceFileMetadata.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        if (!data) {
          throw Error("No data sent from the server");
        }
        if (!data.Duration) {
          throw Error("No file duration in data sent from the server");
        }

        this.getVideoSourceLivePts(props, mediaStreamerSensorId, data);
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          console.warn(getCallPurpose + " : " + thrown.message);
          return;
        }

        this._onWebRtcStreamingError();

        if (!thrown.config) {
          console.error(getCallPurpose + ": " + thrown.message);
          return;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurpose,
          thrown
        );
        console.error(userMessage.getLongString());
      });
  };

  getMediaServiceDeviceId = (props) => {
    this.abortControllerMediaServiceDeviceListQuery = new AbortController();

    let getMediaServiceDeviceListCallPurpose =
      "Get MediaService deviceId for sensor " + props.cameraSourceInfo.id;

    fetch(props.mediaServiceBaseUrl + "/sensor/list", {
      signal: this.abortControllerMediaServiceDeviceListQuery.signal,
    })
      .then((response) => readJson(response))
      .then((deviceList) => {
        let mediaStreamerSensorId;

        if (!deviceList) {
          throw Error("No media service device list data sent from the server");
        }

        deviceList.forEach((deviceData) => {
          let cameraId = props.cameraSourceInfo.id;
          if (props.isRemoveDashCV && cameraId.endsWith("-cv")) {
            cameraId = cameraId.replace("-cv", "");
          }
          if (
            deviceData.name.split(".")[0] === cameraId &&
            deviceData.state === "online"
          ) {
            mediaStreamerSensorId = deviceData.sensorId;

            let cameraMediaSource;

            props.cameraSourceInfo.attributes &&
              props.cameraSourceInfo.attributes.forEach((element) => {
                if (element.name === "source") {
                  cameraMediaSource = element.value;
                }
              });

            if (!cameraMediaSource) {
              throw Error(
                "No media source found for sensor " + props.cameraSourceInfo.id
              );
            }

            this.setState({ mediaStreamerSensorId: mediaStreamerSensorId });
          }
        });
        if (!mediaStreamerSensorId) {
          throw Error(
            "Unable to get an online media service device ID for sensor " +
              props.cameraSourceInfo.id
          );
        }
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          console.warn(
            getMediaServiceDeviceListCallPurpose + " : " + thrown.message
          );
          return;
        }

        this._onWebRtcStreamingError();

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
  };

  _onWebRtcStreamingError = (error) => {
    this.props.onWebRtcStreamingError && this.props.onWebRtcStreamingError(error);
  }

  componentDidMount() {
    this.getMediaServiceDeviceId(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    this.getMediaServiceDeviceId(nextProps);
  }

  shouldComponentUpdate(nextProps, nextState) {
    return (
      this.state.mediaStreamerSensorId !== nextState.mediaStreamerSensorId ||
      this.props.mode !== nextProps.mode ||
      this.props.replayEventInfo?.timestamp !== nextProps.replayEventInfo?.timestamp ||
      this.props.replayEventInfo?.end !== nextProps.replayEventInfo?.end
    );
  }

  /**
   * Extract camera media source from attributes
   */
  _getCameraMediaSource() {
    let cameraMediaSource;
    this.props.cameraSourceInfo.attributes &&
      this.props.cameraSourceInfo.attributes.forEach((element) => {
        if (element.name === "source") {
          cameraMediaSource = element.value;
        }
      });
    return cameraMediaSource;
  }

  /**
   * Live DASH must not send startTime: that selects VST replay, not live.
   * uiDelaySeconds is an analytics offset, not a dash.js liveDelay. A delay
   * larger than the live preroll asks for media from before the session
   * existed and the player never starts.
   */
  _buildLiveDashConnectionInfo() {
    return {
      endpoint: vstHttpOrigin(this.props.mediaServiceBaseUrl),
      streamId: this.state.mediaStreamerSensorId,
    };
  }

  _buildRecordedDashTimeParams() {
    if (this.props.replayEventInfo?.timestamp && this.props.replayEventInfo?.end) {
      return {
        startTime: this.props.replayEventInfo.timestamp,
        endTime: this.props.replayEventInfo.end,
      };
    }
    return null;
  }

  _buildRecordedDashConnectionInfo(timeParams) {
    const objectId =
      this.props.replayEventInfo.info?.primaryObjectId ||
      this.props.replayEventInfo.object?.id;
    return {
      endpoint: vstHttpOrigin(this.props.mediaServiceBaseUrl),
      streamId: this.state.mediaStreamerSensorId,
      startTime: timeParams.startTime,
      endTime: timeParams.endTime,
      overlay: objectId
        ? {
            enabled: true,
            objectId: [objectId],
            color: "red",
            thickness: 20,
            needBbox: true,
          }
        : undefined,
    };
  }

  /**
   * Render live mode video component
   */
  _renderLiveVideo() {
    const cameraMediaSource = this._getCameraMediaSource();
    if (!cameraMediaSource) {
      console.warn(
        "No media source attribute for sensor: " +
          this.props.cameraSourceInfo.id
      );
      return "Video Component Not Loaded";
    }

    if (
      cameraMediaSource !== "nvstreamer" &&
      cameraMediaSource !== "vst" &&
      cameraMediaSource !== "mms"
    ) {
      console.warn(
        "Unrecognized media source (" +
          cameraMediaSource +
          ") for sensor " +
          this.props.cameraSourceInfo.id
      );
      return "Video Component Not Loaded";
    }

    return (
      <DashVideo
        videoConnectionInfo={this._buildLiveDashConnectionInfo()}
        htmlVideoTagOptions={this.props.videoHtmlTagOptions}
        videoControls={{ playpause: false }}
        htmlMediaElementEventHandlers={this.props.htmlMediaElementEventHandlers}
        onWebRtcStreamingError={this._onWebRtcStreamingError.bind(this)}
        streamActionHandlers={this.props.streamActionHandlers}
      />
    );
  }

  /**
   * Render recorded mode video component
   */
  _renderRecordedVideo() {
    const cameraMediaSource = this._getCameraMediaSource();
    if (!cameraMediaSource) {
      console.warn(
        "No media source attribute for sensor: " +
          this.props.cameraSourceInfo.id
      );
      return "Video Component Not Loaded";
    }

    if (
      cameraMediaSource !== "nvstreamer" &&
      cameraMediaSource !== "vst" &&
      cameraMediaSource !== "mms"
    ) {
      console.warn(
        "Unrecognized media source (" +
          cameraMediaSource +
          ") for sensor " +
          this.props.cameraSourceInfo.id
      );
      return "Video Component Not Loaded";
    }

    const timeParams = this._buildRecordedDashTimeParams();
    if (!timeParams?.startTime || !timeParams?.endTime) {
      console.warn("No ISO start or end time for DASH replay");
      return "Video Component Not Loaded";
    }

    return (
      <DashVideo
        videoConnectionInfo={this._buildRecordedDashConnectionInfo(timeParams)}
        htmlVideoTagOptions={this.props.videoHtmlTagOptions}
        videoControls={{ playpause: true }}
        htmlMediaElementEventHandlers={this.props.htmlMediaElementEventHandlers}
        onWebRtcStreamingError={this._onWebRtcStreamingError.bind(this)}
        streamActionHandlers={this.props.streamActionHandlers}
      />
    );
  }

  render() {
    if (!this.state.mediaStreamerSensorId) {
      return "Video Component Not Loaded";
    }

    if (this.props.mode === "live") {
      return this._renderLiveVideo();
    }

    return this._renderRecordedVideo();
  }

  componentWillUnmount() {
    this.abortControllerMediaServiceDeviceListQuery &&
      this.abortControllerMediaServiceDeviceListQuery.abort();
    this.abortControllerMediaServiceFileMetadata &&
      this.abortControllerMediaServiceFileMetadata.abort();
    this.abortControllerVideoPts &&
      this.abortControllerVideoPts.abort();
  }
}

/*
 * Props description
 *
 * videoHtmlTagOptions: The HTML Video Tag options like height, width, controls etc.
 *
 * uiDelaySeconds: Any delay to be introduced in the playback. Used for 'live' mode
 *
 * endpointPts: Pts Endpoint to get start timestamp for hls video being
 *     played by Wowza server in a repeating manner
 *
 * mode: Video playback mode - 'live', 'recorded'
 *
 * cameraSourceInfo: Object containing camera source information like type of video
 *     stream etc
 *
 * replayEventInfo: Object containing replay (metropolis analytics generated anomaly) event info
 *     For 'recorded' mode while playing an anomaly
 *
 * hlsEventListeners: Hls event listeners for HlsVideo Component
 *
 * htmlMediaElementEventHandlers: HTML Video Element Listeners
 */
