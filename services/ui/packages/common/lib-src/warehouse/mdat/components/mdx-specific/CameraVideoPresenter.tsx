// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Axios } from "common/warehouse";
import DashVideo from "../generic/DashVideo/DashVideo";
import { vstHttpOrigin } from "../generic/DashVideo/vstHttpOrigin";
import UserMessage from "../../utils/UserMessage";
import { FramesUrlPtsSuffix } from "../../utils/UrlConstants";


export default class CameraVideoPresenter extends React.Component {
  state = {
    // hlsVideoURL: null,
    mediaStreamerSensorId: undefined,
  };

  // computeHlsStartPts(props) {
  //   this.axiosCancelTokenSource && this.axiosCancelTokenSource.cancel();

  //   this.setState({ hlsVideoURL: null });

  //   this.axiosCancelTokenSource = Axios.CancelToken.source();

  //   if (props.cameraSourceInfo.mmsInfo.protocol === "hls") {
  //     if (props.cameraSourceInfo.mmsInfo.type === "wowza") {
  //       // Construct hls video url

  //       let currentTimestamp;

  //       if (props.mode === 'recorded') {
  //         currentTimestamp = props.replayEventInfo.start;
  //       } else if (props.mode === 'live') {
  //         currentTimestamp = new Date(Date.now() -
  //                                     (props.uiDelaySeconds ?
  //                                      props.uiDelaySeconds * 1000 :
  //                                      0)
  //                                    ).toISOString();
  //       }

  //       // get the PTS time for Wowza VOD video
  //       Axios.get(props.endpointPts, {
  //         params: {
  //           sensorId: props.cameraSourceInfo.id,
  //           timestamp: currentTimestamp,
  //         },
  //         cancelToken: this.axiosCancelTokenSource.token
  //       }).then((res) => {
  //         let hlsVideoURL = props.cameraSourceInfo.videoURL;

  //         if (res.data.pts === null || res.data.pts === undefined) {
  //           console.error('Request for computed Wowza HLS Video PTS for camera ' +
  //                         props.cameraSourceInfo.id +
  //                         ' returned: ' + res.data.pts);
  //           // re-render based on bad Hls Url state
  //           this.setState({ hlsVideoURL: null });
  //           // Since this is fatal HLS Wowza playback error,
  //           // run HLS Fatal Error playback if applicable
  //           props.hlsEventListeners &&
  //                props.hlsEventListeners.onFatalError &&
  //                props.hlsEventListeners.onFatalError();

  //           return;
  //         }

  //         if (props.mode === 'live') {
  //           this.setState({ hlsVideoURL: hlsVideoURL + '?wowzaplaystart=' + res.data.pts});
  //         } else if (props.mode === 'recorded') {
  //           let wowzaPlayDurationMs = Math.floor(props.replayEventInfo.timeInterval * 1000);
  //           this.setState({
  //             hlsVideoURL: hlsVideoURL +
  //                          '?wowzaplaystart=' + res.data.pts +
  //                          '&wowzaplayduration=' + wowzaPlayDurationMs,
  //           });
  //         }
  //       }).catch((thrown) => {
  //         if (Axios.isCancel(thrown)) {
  //           console.log('Request for computed Video PTS for camera:' + props.cameraSourceInfo.id +
  //                       ' canceled - ', thrown.message);
  //         } else {
  //           console.error('Something wrong with Query to request computed Video PTS for camera:' +
  //                         props.cameraSourceInfo.id + ' ' + thrown.message);
  //         }
  //       });
  //     }
  //   }
  // }

  getVideoSourceLivePts = (
    props,
    mediaStreamerSensorId,
    fileSourceMetadata
  ) => {
    this.axiosCancelTokenSourceVideoPts = Axios.CancelToken.source();

    let getCallPurpose =
      "Get NvStreamer Live PTS for sensor " + props.cameraSourceInfo.id;

    let currentTimestamp = new Date(
      Date.now() - (props.uiDelaySeconds ? props.uiDelaySeconds * 1000 : 0)
    ).toISOString();

    Axios.get(`${props.endpointPtsBaseUrl}${FramesUrlPtsSuffix}`, {
      params: {
        sensorId: props.cameraSourceInfo.id,
        timestamp: currentTimestamp,
      },
      cancelToken: this.axiosCancelTokenSourceVideoPts.token,
    })
      .then((res) => {
        if (!res.data) {
          throw Error("No data sent from the server");
        }
        if (!res.data.pts) {
          throw Error("No Live pts in data sent from the server");
        }
        this.setState({
          mediaStreamerSensorId: mediaStreamerSensorId,
          fileSourceMetadata: fileSourceMetadata,
          sensorLivePts: res.data.pts,
        });
      })
      .catch((thrown) => {
        if (Axios.isCancel(thrown)) {
          console.warn(getCallPurpose + " : " + thrown.message);
          return;
        }

        this._onWebRtcStreamingError();

        if (!thrown.config) {
          console.error(getCallPurpose + ": " + thrown.message);
          return;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.AXIOS_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurpose,
          thrown
        );
        console.error(userMessage.getLongString());
      });
  };

  getNvStreamerVideoSourceFileMetadata = (props, mediaStreamerSensorId) => {
    this.axiosCancelTokenSourceMediaServiceFileMetadata =
      Axios.CancelToken.source();

    let getCallPurpose =
      "Get Media Service source file metadata for sensor " +
      props.cameraSourceInfo.id;

    Axios.get(props.mediaServiceBaseUrl + "/file/metadata", {
      params: { fileId: mediaStreamerSensorId },
      cancelToken: this.axiosCancelTokenSourceMediaServiceFileMetadata.token,
    })
      .then((res) => {
        if (!res.data) {
          throw Error("No data sent from the server");
        }
        if (!res.data.Duration) {
          throw Error("No file duration in data sent from the server");
        }

        this.getVideoSourceLivePts(props, mediaStreamerSensorId, res.data);
      })
      .catch((thrown) => {
        if (Axios.isCancel(thrown)) {
          console.warn(getCallPurpose + " : " + thrown.message);
          return;
        }

        this._onWebRtcStreamingError();

        if (!thrown.config) {
          console.error(getCallPurpose + ": " + thrown.message);
          return;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.AXIOS_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurpose,
          thrown
        );
        console.error(userMessage.getLongString());
      });
  };

  getMediaServiceDeviceId = (props) => {
    this.axiosCancelTokenSourceMediaServiceDeviceListQuery =
      Axios.CancelToken.source();

    let getMediaServiceDeviceListCallPurpose =
      "Get MediaService deviceId for sensor " + props.cameraSourceInfo.id;

    Axios.get(props.mediaServiceBaseUrl + "/sensor/list", {
      cancelToken: this.axiosCancelTokenSourceMediaServiceDeviceListQuery.token,
    })
      .then((mediaServiceDeviceListResponse) => {
        let mediaStreamerSensorId;

        if (!mediaServiceDeviceListResponse.data) {
          throw Error("No media service device list data sent from the server");
        }

        mediaServiceDeviceListResponse.data.forEach((deviceData) => {
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
        if (Axios.isCancel(thrown)) {
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
          UserMessage.SRC_TYPE.AXIOS_ERROR,
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
    // this.computeHlsStartPts(this.props);
    this.getMediaServiceDeviceId(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    // this.computeHlsStartPts(nextProps);
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
    // this.axiosCancelTokenSource && this.axiosCancelTokenSource.cancel();
    this.axiosCancelTokenSourceMediaServiceDeviceListQuery &&
      this.axiosCancelTokenSourceMediaServiceDeviceListQuery.cancel();
    this.axiosCancelTokenSourceMediaServiceFileMetadata &&
      this.axiosCancelTokenSourceMediaServiceFileMetadata.cancel();
    this.axiosCancelTokenSourceVideoPts &&
      this.axiosCancelTokenSourceVideoPts.cancel();
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
