// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import { isEqual } from "lodash";
// TODO: Check if adapter min is really needed to avoid errors in webrtcstreamer
//       like it happened in past
import "webrtc-streamer/html/libs/adapter.min.js";
import WebRtcStreamer from "./webrtcstreamer";

import VideoElement from "../VideoElement/VideoElement";
import WebrtcVideoTimeline from "./WebrtcVideoTimeline";

import Styles from "./WebrtcVideo.module.css";

/**
 * Video Element using WebRTC protocol for streaming.
 *
 * Notes:
 *
 * HTML5 Video Element can be obtained by videoElement.video.
 */
export default class WebrtcVideo extends React.Component {
  state = {
    error: null,
    currentTimeMs: undefined,
    isTimelineSeekable: false,
  };

  assignEventHandlersFromProps = (props) => {
    // Clear any previous invoked interval tasks
    if (this.setRtcEventHandlersTimerId) {
      clearInterval(this.setRtcEventHandlersTimerId);
    }
    if (!props.rtcPeerConnectionEventHandlers) return;

    // Since WebRtcStreamer.connect creates RTCPeerConnection asynchronously
    // and the class doesn't give a convenient way to attach handlers to the
    // created RTCPeerConnection, we wait till the connection is made before
    // assigning handlers and try for definite times

    let MAX_TRIES = 100;
    let tryCount = 0;
    this.setRtcEventHandlersTimerId = setInterval(() => {
      tryCount++;
      if (!this.ws.pc) {
        console.warn(
          "No RTCPeerConnection to assign event handlers to! Will try again..."
        );
        // If tried maximum times then stop
        if (tryCount >= MAX_TRIES) {
          clearInterval(this.setRtcEventHandlersTimerId);
          console.warn(
            "Failed to assign event handlers to RTCPeerConnection - Max tries reached"
          );
        }
        return;
      }

      // Since we can now set the handlers to a non null RTCPeerConnection,
      // cancel the timer
      clearInterval(this.setRtcEventHandlersTimerId);

      this.ws.pc.onconnectionstatechange =
        props.rtcPeerConnectionEventHandlers.onconnectionstatechange;
      this.ws.pc.ondatachannel =
        props.rtcPeerConnectionEventHandlers.ondatachannel;
      this.ws.pc.onicecandidate =
        props.rtcPeerConnectionEventHandlers.onicecandidate;
      this.ws.pc.oniceconnectionstatechange =
        props.rtcPeerConnectionEventHandlers.oniceconnectionstatechange;
      this.ws.pc.onicegatheringstatechange =
        props.rtcPeerConnectionEventHandlers.onicegatheringstatechange;
      this.ws.pc.onidentityresult =
        props.rtcPeerConnectionEventHandlers.onidentityresult;
      this.ws.pc.onnegotiationneeded =
        props.rtcPeerConnectionEventHandlers.onnegotiationneeded;
      this.ws.pc.onremovestream =
        props.rtcPeerConnectionEventHandlers.onremovestream;
      this.ws.pc.onsignalingstatechange =
        props.rtcPeerConnectionEventHandlers.onsignalingstatechange;
      this.ws.pc.ontrack = props.rtcPeerConnectionEventHandlers.ontrack;

      if (this.props.onRtcPeerConnectionCreated)
        this.props.onRtcPeerConnectionCreated(this.ws.pc);
    }, 10);
    // TODO: Set max number of tries for setting event handlers based on error state of connection
  };

  webrtcStreamerConnect = (props) => {
    if (!window.RTCPeerConnection) {
      this.setState({ error: "WebRTC not supported on this browser" });
      return;
    }

    this.ws && this.ws.disconnect();
    this.ws = null;
    this.setState({
      error: null,
      isTimelineSeekable: false,
    });

    let streamActionHandlers = Object.assign({}, props.streamActionHandlers);

    let preStartStreamAction;
    if (streamActionHandlers.onStart)
      preStartStreamAction = streamActionHandlers.onStart;
    let prePauseStreamAction;
    if (streamActionHandlers.onPause)
      prePauseStreamAction = streamActionHandlers.onPause;
    let preResumeStreamAction;
    if (streamActionHandlers.onResume)
      preResumeStreamAction = streamActionHandlers.onResume;
    let preEndStreamAction;
    if (streamActionHandlers.onEnd)
      preEndStreamAction = streamActionHandlers.onEnd;
    let preTimeUpdateEndStreamAction;
    if (streamActionHandlers.onTimeUpdate)
      preTimeUpdateEndStreamAction = streamActionHandlers.onTimeUpdate;
    let preNotReceivingFramesStreamAction;
    if (streamActionHandlers.onNotReceivingFrames)
      preNotReceivingFramesStreamAction =
        streamActionHandlers.onNotReceivingFrames;
    let preReceivingFramesStreamAction;
    if (streamActionHandlers.onReceivingFrames)
      preReceivingFramesStreamAction = streamActionHandlers.onReceivingFrames;

    streamActionHandlers.onStart = (startTimeMs) => {
      this.setState({ isTimelineSeekable: true });

      this.videoElement &&
        this.videoElement.switchPlayPauseBtn(
          this.videoElement.VIDEO_CONTROL_PAUSE_LABEL
        );
      this.videoElement &&
        this.videoElement.setState({ coverMsg: "", showLoader: false });

      preStartStreamAction && preStartStreamAction(startTimeMs);
    };
    streamActionHandlers.onPause = () => {
      this.videoElement &&
        this.videoElement.switchPlayPauseBtn(
          this.videoElement.VIDEO_CONTROL_PLAY_LABEL
        );
      this.videoElement && this.videoElement.setState({ coverMsg: "PAUSED" });

      prePauseStreamAction && prePauseStreamAction();
    };
    streamActionHandlers.onResume = () => {
      this.videoElement &&
        this.videoElement.switchPlayPauseBtn(
          this.videoElement.VIDEO_CONTROL_PAUSE_LABEL
        );
      this.videoElement && this.videoElement.setState({ coverMsg: "" });

      preResumeStreamAction && preResumeStreamAction();
    };
    streamActionHandlers.onEnd = () => {
      this.videoElement &&
        this.videoElement.switchPlayPauseBtn(
          this.videoElement.VIDEO_CONTROL_PLAY_LABEL
        );
      this.videoElement && this.videoElement.setState({ coverMsg: "ENDED" });

      preEndStreamAction && preEndStreamAction();
    };
    streamActionHandlers.onTimeUpdate = (timeMs) => {
      this.setState({ currentTimeMs: timeMs });

      preTimeUpdateEndStreamAction && preTimeUpdateEndStreamAction(timeMs);
    };
    streamActionHandlers.onNotReceivingFrames = () => {
      this.videoElement &&
        this.videoElement.setState({ coverMsg: "NOT RECEIVING FRAMES" });

      preNotReceivingFramesStreamAction && preNotReceivingFramesStreamAction();
    };
    streamActionHandlers.onReceivingFrames = () => {
      this.videoElement &&
        this.videoElement.state.coverMsg === "NOT RECEIVING FRAMES" &&
        this.videoElement.setState({ coverMsg: "" });

      preReceivingFramesStreamAction && preReceivingFramesStreamAction();
    };

    this.videoElement &&
      this.videoElement.setState({ showLoader: true, ended: false });

    if (this.videoElement) {
      this.ws = new WebRtcStreamer(
        this.videoElement.video,
        props.videoConnectionInfo.serverUrl,
        ((error) => {
          this.ws && this.ws.disconnect();
          this.ws = null;
          this.setState({ error: "Unable to play WebRTC video" });
          this.videoElement && this.videoElement.setState({ coverMsg: "ERRED" });
          props.onWebRtcStreamingError && props.onWebRtcStreamingError(error);
        }).bind(this),
        streamActionHandlers
      );

      this.ws.connect(props.videoConnectionInfo.videoParams);
    }

    this.assignEventHandlersFromProps(props);
  };

  onVideoPlayRequest = () => {
    this.ws && this.ws.resume();
  };

  onVideoPauseRequest = () => {
    this.ws && this.ws.pause();
  };

  componentDidMount() {
    // Start video streaming
    this.webrtcStreamerConnect(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      isEqual(
        this.props.htmlVideoTagOptions,
        nextProps.htmlVideoTagOptions
      ) &&
      // isEqual(this.props.htmlMediaElementEventHandlers, nextProps.htmlMediaElementEventHandlers) &&
      // isEqual(this.props.onWebRtcStreamingError, nextProps.onWebRtcStreamingError) &&
      // isEqual(this.props.streamActionHandlers, nextProps.streamActionHandlers) &&
      isEqual(
        this.props.videoConnectionInfo,
        nextProps.videoConnectionInfo
      ) &&
      isEqual(this.props.videoControls, nextProps.videoControls)
    ) {
      // isEqual(this.props.rtcPeerConnectionEventHandlers, nextProps.rtcPeerConnectionEventHandlers) &&
      // isEqual(this.props.onRtcPeerConnectionCreated, nextProps.onRtcPeerConnectionCreated)) {
      return;
    }

    this.webrtcStreamerConnect(nextProps);
  }

  render() {
    if (this.state.error) return <div>{this.state.error}</div>;

    const propsIsTimelineSeekable =
      this.props.isTimelineSeekable !== undefined
        ? this.props.isTimelineSeekable
        : true;

    return (
      <div className={Styles.main}>
        <div className={Styles.videoElementContainer}>
          <VideoElement
            ref={(ref) => (this.videoElement = ref)}
            htmlVideoTagOptions={this.props.htmlVideoTagOptions}
            videoControls={this.props.videoControls}
            htmlMediaElementEventHandlers={
              this.props.htmlMediaElementEventHandlers
            }
            controlsActionsHandlers={{
              onClickPlay: this.onVideoPlayRequest.bind(this),
              onClickPause: this.onVideoPauseRequest.bind(this),
            }}
          />
        </div>
        {/* Timeline only available for VST based sensor source recorded videos where time is UTC */}
        {new Date(
          this.props.videoConnectionInfo.videoParams.startTime
        ).toString() !== "Invalid Date" &&
          new Date(
            this.props.videoConnectionInfo.videoParams.endTime
          ).toString() !== "Invalid Date" &&
          this.state.currentTimeMs && (
            <div className={Styles.timelineContainer}>
              <WebrtcVideoTimeline
                seekable={
                  propsIsTimelineSeekable && this.state.isTimelineSeekable
                }
                startMs={new Date(
                  this.props.videoConnectionInfo.videoParams.startTime
                ).getTime()}
                endMs={new Date(
                  this.props.videoConnectionInfo.videoParams.endTime
                ).getTime()}
                currentTimeMs={this.state.currentTimeMs}
                onSeekEnd={(timeMs) => {
                  this.ws.seek(timeMs);
                }}
              />
            </div>
          )}
      </div>
    );
  }

  componentWillUnmount() {
    clearInterval(this.setRtcEventHandlersTimerId);
    this.videoElement && this.videoElement.setState({ coverMsg: "ENDED" });
    this.ws && this.ws.disconnect();
    this.ws = null;
  }
}

WebrtcVideo.propTypes = {
  /**
   * Info Object containing webrtc server connection info for the video
   *
   * {serverUrl:, videoParams:}
   *
   * Check params for WebRtcStreamer and WebRtcStreamer.connect in webrtcstreamer.js
   * @ https://github.com/mpromonet/webrtc-streamer-html
   */
  videoConnectionInfo: PropTypes.object.isRequired,
  /**
   * Object containing callbacks for RTCPeerConnection event handlers as described under
   * https://developer.mozilla.org/en-US/docs/Web/API/RTCPeerConnection
   * Example: { 'onaddstream': function, 'ondatachannel': function ... }
   */
  rtcPeerConnectionEventHandlers: PropTypes.object,
  /**
   * VideoElement props
   */
  htmlVideoTagOptions: PropTypes.object.isRequired,
  /**
   * Callback that executes after WebRtcStreamer.connect successfully
   * creates a new RTCPeerConnection object.
   * Serves as a way to access RTCPeerConnection object created upon
   * connect. User can execute RTCPeerConnection method as described
   * in RTCPeerConnection API Standards.
   *
   * @param {Object} object RTCPeerConnection object
   */
  onRtcPeerConnectionCreated: PropTypes.func,
  /**
   * VideoElement props
   */
  videoControls: PropTypes.object,
  /**
   * Object containing callbacks for HTMLMediaElement event handlers as described under
   * https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement
   */
  htmlMediaElementEventHandlers: PropTypes.object,
  /**
   * Object containing callbacks for WebRTC Stream's Start, Pause, Resume action callbacks
   */
  streamActionHandlers: PropTypes.object,
  /**
   * Callback function upon WebRtc Streaming Error
   */
  onWebRtcStreamingError: PropTypes.func,
};
