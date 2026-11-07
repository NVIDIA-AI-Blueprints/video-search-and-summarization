// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import Hls from "hls.js";

import VideoElement from "../VideoElement/VideoElement";

let TIMEOUT_MS = 3000;

/**
 * Video Element that consumes and plays a Hls format video
 *
 * Notes:
 *
 * By default automatically starts loading the video.
 *
 * HTML5 Video Element can be obtained by videoElement.video.
 */
export default class HlsVideo extends React.Component {
  constructor(props) {
    super(props);

    this.state = {
      hasHlsFatalErrored: false,
    };

    this.defaultHlsConfig = {
      autoStartLoad: true,
      debug: false,
      fragLoadingMaxRetry: 0,
      manifestLoadingMaxRetry: 0,
      levelLoadingMaxRetry: 0,
      fragLoadingMaxRetryTimeout: TIMEOUT_MS,
      manifestLoadingMaxRetryTimeout: TIMEOUT_MS,
      levelLoadingMaxRetryTimeout: TIMEOUT_MS,
      fragLoadingTimeOut: TIMEOUT_MS,
      manifestLoadingTimeOut: TIMEOUT_MS,
      levelLoadingTimeOut: TIMEOUT_MS,
      fragLoadingRetryDelay: 0,
      manifestLoadingRetryDelay: 0,
      levelLoadingRetryDelay: 0,
      startFragPrefetch: true,
    };

    this.constructHlsInstanceWithNewProps(props);
  }

  tearDownHlsContext = () => {
    this.hls &&
      this.hls.off(Hls.Events.FRAG_LOADED, this.hlsOnFirstFragmentLoaded);
    this.hls && this.hls.off(Hls.Events.ERROR, this.hlsOnError);
    this.hls && this.hls.detachMedia();
    this.hls && this.hls.destroy();
  };

  constructHlsInstanceWithNewProps = (props) => {
    if (!this.isHlsSupported()) return;

    this.tearDownHlsContext();

    let hlsConfig = Object.assign({}, this.defaultHlsConfig, props.hlsConfig);
    this.hls = new Hls(hlsConfig);

    this.hlsOnError = (event, data) => {
      if (data.fatal === true) {
        console.error(
          "Fatal Error in HLS Video playback for url " +
            props.videoLink +
            ": " +
            data.details
        );
        this.tearDownHlsContext();

        this.setState({ hasHlsFatalErrored: true });

        props.hlsEventListeners &&
          props.hlsEventListeners.onFatalError &&
          props.hlsEventListeners.onFatalError(event, data);
      } else {
        console.warn(
          "Error in HLS Video playback for url " +
            props.videoLink +
            ": " +
            data.details
        );
        props.hlsEventListeners &&
          props.hlsEventListeners.onError &&
          props.hlsEventListeners.onError(event, data);
      }
    };
    this.hls.on(Hls.Events.ERROR, this.hlsOnError);

    this.hlsOnFirstFragmentLoaded = (event, data) => {
      props.hlsEventListeners &&
        props.hlsEventListeners.onFirstFragmentLoaded &&
        props.hlsEventListeners.onFirstFragmentLoaded(event, data);
    };
    this.hls.once(Hls.Events.FRAG_LOADED, this.hlsOnFirstFragmentLoaded);

    this.hlsOnFirstFragmentParsed = (event, data) => {
      props.hlsEventListeners &&
        props.hlsEventListeners.onFirstFragmentParsed &&
        props.hlsEventListeners.onFirstFragmentParsed(event, data);
    };
    this.hls.once(Hls.Events.FRAG_PARSED, this.hlsOnFirstFragmentParsed);

    this.videoElement &&
      this.videoElement.video &&
      this.hls.attachMedia(this.videoElement.video);

    // Start Hls video data loading asap
    this.hls.loadSource(props.videoLink);
  };

  isHlsSupported() {
    if (!Hls.isSupported()) {
      console.error("HLS not supported. Unable to stream video content.");
      return false;
    }
    return true;
  }

  componentDidMount() {
    if (!this.hls) return;

    this.hls.attachMedia(this.videoElement.video);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (!this.isHlsSupported()) return;
    this.constructHlsInstanceWithNewProps(nextProps);
  }

  shouldComponentUpdate(nextProps, nextState) {
    // Don't re-render if props didn't change
    return (
      !(nextProps.htmlVideoTagOptions === this.props.htmlVideoTagOptions) ||
      !(nextProps.videoControls === this.props.videoControls) ||
      !(this.state.hasHlsFatalErrored === nextState.hasHlsFatalErrored)
    );
  }

  render() {
    return (
      <React.Fragment>
        {this.state.hasHlsFatalErrored === true ? (
          <div>{"Can not play Hls video"}</div>
        ) : null}
        <VideoElement
          ref={(ref) => (this.videoElement = ref)}
          htmlVideoTagOptions={this.props.htmlVideoTagOptions}
          videoControls={this.props.videoControls}
          htmlMediaElementEventHandlers={
            this.props.htmlMediaElementEventHandlers
          }
        />
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    this.tearDownHlsContext();
  }
}

HlsVideo.propTypes = {
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
   * Hls Configuration Parameters. If not specified, loads default hls config where -
   *
   * autoStartLoad: true
   *
   * Refer to "Fine Tuning" under Hls.js documentation
   */
  hlsConfig: PropTypes.object,
  /**
   * Hls event listeners
   */
  hlsEventListeners: PropTypes.object,
};
