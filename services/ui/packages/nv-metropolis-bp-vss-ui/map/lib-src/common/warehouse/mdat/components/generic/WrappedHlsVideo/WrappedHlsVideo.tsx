// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import Hls from "hls.js";

import HlsVideo from "../HlsVideo/HlsVideo";

/**
 * Wraps HlsVideo Elemenit that consumes and plays a Hls format video.
 * Implements Application specific logic by adding listeners to the
 * underlying HlsVideo Element.
 */
export default class WrappedHlsVideo extends React.Component {
  initializeHlsVideo = () => {
    if (
      !this.hlsVideoElement ||
      !this.hlsVideoElement.hls ||
      !this.hlsVideoElement.videoElement ||
      !this.hlsVideoElement.videoElement.video
    )
      return;

    this.videoEnded = false;

    this.lastRecordedCurrentAppEpochTimeMs = Date.now();

    this.hlsVideoElement.hls.on(Hls.Events.MANIFEST_PARSED, (event, data) => {
      this.lastRecordedCurrentAppEpochTimeMs = Date.now();
    });

    // After the first render operation the video element
    // hlsVideoElement.videoElement.video should already be initialized.
    // Assign the listeners for video element events here
    this.hlsVideoElement.videoElement.video.addEventListener(
      "playing",
      (event) => {
        this.videoEnded = false;
      }
    );

    this.hlsVideoElement.videoElement.video.addEventListener(
      "timeupdate",
      (event) => {
        if (
          !this.hlsVideoElement ||
          !this.hlsVideoElement.videoElement ||
          !this.hlsVideoElement.videoElement.video
        )
          return;
        if (this.videoEnded === true) return;

        const expectedCurrentVideoTimeSeconds =
          (Date.now() - this.lastRecordedCurrentAppEpochTimeMs) / 1000;
        // Allowed error = 100ms. If current video time is within 100ms of the expected time
        // then let the playback go on as it is without trying to change the time
        if (
          this.hlsVideoElement.videoElement.video.currentTime + 0.1 <
          expectedCurrentVideoTimeSeconds
        ) {
          this.hlsVideoElement.videoElement.video.currentTime =
            expectedCurrentVideoTimeSeconds;
        }
      }
    );

    this.hlsVideoElement.videoElement.video.addEventListener(
      "ended",
      (event) => {
        this.videoEnded = true;
      }
    );
  };

  componentDidMount() {
    this.initializeHlsVideo();
  }

  shouldComponentUpdate(nextProps, nextState) {
    if (this.props.videoLink === nextProps.videoLink) {
      return false;
    }

    // reinitialize contained HlsVideo
    this.initializeHlsVideo();
    return true;
  }

  render() {
    return (
      <HlsVideo
        ref={(ref) => (this.hlsVideoElement = ref)}
        videoLink={this.props.videoLink}
        htmlVideoTagOptions={this.props.htmlVideoTagOptions}
        videoControls={this.props.videoControls}
        htmlMediaElementEventHandlers={this.props.htmlMediaElementEventHandlers}
        hlsEventListeners={this.props.hlsEventListeners}
      />
    );
  }
}

WrappedHlsVideo.propTypes = {
  /**
   * Hls video url
   */
  videoLink: PropTypes.string.isRequired,
  /**
   * VideoElement props
   */
  htmlVideoTagOptions: PropTypes.object.isRequired,
  /**
   * VideoElement props
   */
  videoControls: PropTypes.object,
  /**
   * VideoElement Event Listeners
   */
  htmlMediaElementEventHandlers: PropTypes.object,
  /**
   * Hls event listeners
   */
  hlsEventListeners: PropTypes.object,
};
