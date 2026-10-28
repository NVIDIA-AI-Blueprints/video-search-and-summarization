// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from 'react';
import PropTypes from 'prop-types';
import { isEqual } from 'lodash';
import { DashStream } from 'vst-streaming-lib/dash';

import VideoElement from '../VideoElement/VideoElement';

import Styles from './DashVideo.module.css';

/**
 * Warehouse camera player using VIOS `DashStream` (vst-streaming-lib).
 * Live and replay DASH only; HLS and WebRTC are not used.
 */
export default class DashVideo extends React.Component {
  dash = new DashStream();
  startGeneration = 0;

  startPlayback = async (props, generation = ++this.startGeneration) => {
    this.videoElement?.setState({ showLoader: true, coverMsg: '', ended: false });

    const info = props.videoConnectionInfo;
    if (!info?.endpoint || !info?.streamId) {
      const message = 'DASH video is missing endpoint or streamId';
      this.videoElement?.setState({ coverMsg: message, showLoader: false });
      props.onWebRtcStreamingError?.(message);
      props.onStreamingError?.(message);
      return;
    }

    const video = this.videoElement?.video;
    if (!video) {
      window.requestAnimationFrame(() => {
        if (generation === this.startGeneration) void this.startPlayback(props, generation);
      });
      return;
    }

    try {
      await this.dash.start({
        endpoint: info.endpoint,
        streamId: info.streamId,
        startTime: info.startTime,
        endTime: info.endTime,
        overlay: info.overlay,
        liveDelaySeconds: info.liveDelaySeconds,
        videoElement: video,
        onFirstFrame: () => {
          if (generation !== this.startGeneration) return;
          this.videoElement?.setState({ showLoader: false, coverMsg: '' });
          this.videoElement?.switchPlayPauseBtn(this.videoElement.VIDEO_CONTROL_PAUSE_LABEL);
          props.streamActionHandlers?.onStart?.(Date.now());
        },
        onError: (message) => {
          if (generation !== this.startGeneration) return;
          this.videoElement?.setState({ coverMsg: message || 'ERRED', showLoader: false });
          props.onWebRtcStreamingError?.(message);
          props.onStreamingError?.(message);
        },
      });
    } catch (error) {
      if (generation !== this.startGeneration) return;
      const message = error instanceof Error ? error.message : String(error);
      this.videoElement?.setState({ coverMsg: message || 'Unable to play DASH video', showLoader: false });
      props.onWebRtcStreamingError?.(error);
      props.onStreamingError?.(error);
    }
  };

  stopPlayback = async () => {
    this.startGeneration += 1;
    const info = this.props.videoConnectionInfo;
    await this.dash.stop(info?.endpoint, info?.streamId);
  };

  componentDidMount() {
    void this.startPlayback(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      isEqual(this.props.videoConnectionInfo, nextProps.videoConnectionInfo) &&
      isEqual(this.props.videoControls, nextProps.videoControls)
    ) {
      return;
    }
    void this.startPlayback(nextProps);
  }

  render() {
    return (
      <div className={Styles.main} data-testid="warehouse-dash-video">
        <div className={Styles.videoElementContainer}>
          <VideoElement
            ref={(ref) => {
              this.videoElement = ref;
            }}
            htmlVideoTagOptions={this.props.htmlVideoTagOptions}
            videoControls={this.props.videoControls}
            htmlMediaElementEventHandlers={this.props.htmlMediaElementEventHandlers}
          />
        </div>
      </div>
    );
  }

  componentWillUnmount() {
    this.videoElement?.setState({ coverMsg: 'ENDED' });
    void this.stopPlayback();
  }
}

DashVideo.propTypes = {
  videoConnectionInfo: PropTypes.object.isRequired,
  htmlVideoTagOptions: PropTypes.object.isRequired,
  videoControls: PropTypes.object,
  htmlMediaElementEventHandlers: PropTypes.object,
  streamActionHandlers: PropTypes.object,
  onWebRtcStreamingError: PropTypes.func,
  onStreamingError: PropTypes.func,
};
