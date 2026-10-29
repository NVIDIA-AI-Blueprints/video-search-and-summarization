// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from 'react';
import PropTypes from 'prop-types';
import { isEqual } from 'lodash';
import { DashStream } from 'vst-streaming-lib/dash';

import VideoElement from '../VideoElement/VideoElement';
import DashVideoTimeline, { parseReplayWindowMs } from './DashVideoTimeline';

import Styles from './DashVideo.module.css';

/**
 * Generic DASH player using VIOS `DashStream` (vst-streaming-lib).
 * Live sessions omit startTime/endTime. Replay sessions pass both.
 * The seek bar follows playable media: finite duration, else buffered
 * range plus playhead. It does not use the MPD seekable window — dash.js
 * replay is a live-profile MPD whose seekable end is the advertised
 * session, including live-delay headroom the playhead never reaches, and
 * `ended` never fires. When the playhead stalls, the bar collapses to it
 * so the thumb sits at 100%. Callers choose the DASH window; this player
 * does not invent padding.
 */
const PLAYABLE_END_EPSILON_SEC = 0.35;
const PLAYHEAD_STALL_MS = 1000;

export default class DashVideo extends React.Component {
  dash = new DashStream();
  startGeneration = 0;
  replayAnchorMs = Number.NaN;
  boundTimeUpdateVideo = null;
  endWatchdogId = null;
  lastPlayheadSec = Number.NaN;
  lastAdvanceAtMs = 0;

  state = {
    currentTimeMs: undefined,
    timelineEndMs: undefined,
    isTimelineSeekable: false,
  };

  replayWindowMs = (info = this.props.videoConnectionInfo) =>
    parseReplayWindowMs(info?.startTime, info?.endTime);

  rangeEndSec = (ranges) => {
    if (!ranges || !ranges.length) return 0;
    try {
      return ranges.end(ranges.length - 1);
    } catch (_error) {
      return 0;
    }
  };

  timelineEndFromMedia = (video, windowMs) => {
    if (!windowMs) return undefined;
    if (video && Number.isFinite(video.duration) && video.duration > 0) {
      return Math.min(windowMs.endMs, windowMs.startMs + video.duration * 1000);
    }
    const bufferedEnd = this.rangeEndSec(video?.buffered);
    const playheadSec =
      video && Number.isFinite(video.currentTime) ? Math.max(0, video.currentTime) : 0;
    const playableSec = Math.max(playheadSec, bufferedEnd);
    if (playableSec > 0) {
      return Math.min(windowMs.endMs, windowMs.startMs + playableSec * 1000);
    }
    return windowMs.endMs;
  };

  isClipEnded = (video, stalled) => {
    if (!video) return false;
    if (video.ended) return true;
    const t = video.currentTime;
    if (Number.isFinite(video.duration) && video.duration > 0) {
      return t >= video.duration - PLAYABLE_END_EPSILON_SEC;
    }
    if (!this.state.isTimelineSeekable || !(t > 0.5)) return false;
    const bufferedEnd = this.rangeEndSec(video.buffered);
    const remaining = bufferedEnd - t;
    if (bufferedEnd > 0 && t >= bufferedEnd - PLAYABLE_END_EPSILON_SEC) return true;
    if (!stalled) return false;
    // User paused with media still ahead. A frozen live-profile playhead
    // (packager stopped, player sitting on its delay) is not paused.
    if (video.paused && remaining > 1) return false;
    return true;
  };

  clearEndWatchdog = () => {
    if (this.endWatchdogId !== null) {
      window.clearTimeout(this.endWatchdogId);
      this.endWatchdogId = null;
    }
  };

  armEndWatchdog = () => {
    this.clearEndWatchdog();
    this.endWatchdogId = window.setTimeout(() => {
      this.endWatchdogId = null;
      this.syncTimelineFromVideo();
    }, PLAYHEAD_STALL_MS);
  };

  notePlayhead = (t) => {
    if (!Number.isFinite(t)) return;
    const advanced =
      !Number.isFinite(this.lastPlayheadSec) || Math.abs(t - this.lastPlayheadSec) > 0.05;
    if (advanced) {
      this.lastPlayheadSec = t;
      this.lastAdvanceAtMs = Date.now();
      if (this.state.isTimelineSeekable && t > 0.5) {
        this.armEndWatchdog();
      }
      return;
    }
    if (!this.endWatchdogId && this.state.isTimelineSeekable && t > 0.5) {
      this.armEndWatchdog();
    }
  };

  syncTimelineFromVideo = () => {
    const video = this.boundTimeUpdateVideo;
    const windowMs = this.replayWindowMs();
    if (!video || !windowMs || !Number.isFinite(this.replayAnchorMs)) return;
    this.notePlayhead(video.currentTime);
    const stalled =
      Number.isFinite(this.lastAdvanceAtMs) &&
      this.lastAdvanceAtMs > 0 &&
      Date.now() - this.lastAdvanceAtMs >= PLAYHEAD_STALL_MS;
    const playheadMs = Math.max(
      windowMs.startMs,
      this.replayAnchorMs + video.currentTime * 1000,
    );
    const mediaEndMs = this.timelineEndFromMedia(video, windowMs);
    const finished = this.isClipEnded(video, stalled);
    let timelineEndMs;
    let next;
    if (finished) {
      timelineEndMs =
        Number.isFinite(video.duration) && video.duration > 0
          ? windowMs.startMs + video.duration * 1000
          : playheadMs;
      next = timelineEndMs;
      this.clearEndWatchdog();
    } else {
      timelineEndMs = mediaEndMs ?? windowMs.endMs;
      next = Math.min(timelineEndMs, playheadMs);
    }
    this.setState({ currentTimeMs: next, timelineEndMs });
    this.props.streamActionHandlers?.onTimeUpdate?.(next);
  };

  bindTimeUpdate = (video) => {
    if (!video || this.boundTimeUpdateVideo === video) return;
    this.unbindTimeUpdate();
    video.addEventListener('timeupdate', this.syncTimelineFromVideo);
    video.addEventListener('durationchange', this.syncTimelineFromVideo);
    video.addEventListener('loadedmetadata', this.syncTimelineFromVideo);
    video.addEventListener('ended', this.syncTimelineFromVideo);
    video.addEventListener('pause', this.syncTimelineFromVideo);
    video.addEventListener('waiting', this.syncTimelineFromVideo);
    this.boundTimeUpdateVideo = video;
  };

  unbindTimeUpdate = () => {
    this.clearEndWatchdog();
    if (this.boundTimeUpdateVideo) {
      this.boundTimeUpdateVideo.removeEventListener('timeupdate', this.syncTimelineFromVideo);
      this.boundTimeUpdateVideo.removeEventListener('durationchange', this.syncTimelineFromVideo);
      this.boundTimeUpdateVideo.removeEventListener('loadedmetadata', this.syncTimelineFromVideo);
      this.boundTimeUpdateVideo.removeEventListener('ended', this.syncTimelineFromVideo);
      this.boundTimeUpdateVideo.removeEventListener('pause', this.syncTimelineFromVideo);
      this.boundTimeUpdateVideo.removeEventListener('waiting', this.syncTimelineFromVideo);
    }
    this.boundTimeUpdateVideo = null;
  };

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

    const windowMs = parseReplayWindowMs(info.startTime, info.endTime);
    this.replayAnchorMs = windowMs ? windowMs.startMs : Number.NaN;
    this.clearEndWatchdog();
    this.lastPlayheadSec = Number.NaN;
    this.lastAdvanceAtMs = 0;
    this.setState({
      currentTimeMs: windowMs ? windowMs.startMs : undefined,
      timelineEndMs: windowMs ? windowMs.endMs : undefined,
      isTimelineSeekable: false,
    });
    this.bindTimeUpdate(video);

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
          const seekable =
            props.isTimelineSeekable !== undefined ? props.isTimelineSeekable : true;
          this.setState(
            {
              isTimelineSeekable: Boolean(windowMs) && seekable,
            },
            () => {
              this.syncTimelineFromVideo();
            },
          );
          props.streamActionHandlers?.onStart?.(Date.now());
        },
        onError: (message) => {
          if (generation !== this.startGeneration) return;
          this.setState({ isTimelineSeekable: false });
          this.videoElement?.setState({ coverMsg: message || 'ERRED', showLoader: false });
          props.onWebRtcStreamingError?.(message);
          props.onStreamingError?.(message);
        },
      });
    } catch (error) {
      if (generation !== this.startGeneration) return;
      const message = error instanceof Error ? error.message : String(error);
      this.setState({ isTimelineSeekable: false });
      this.videoElement?.setState({ coverMsg: message || 'Unable to play DASH video', showLoader: false });
      props.onWebRtcStreamingError?.(error);
      props.onStreamingError?.(error);
    }
  };

  seekTo = (timeMs) => {
    const windowMs = this.replayWindowMs();
    if (!windowMs || !this.state.isTimelineSeekable) return;
    const endMs = this.state.timelineEndMs ?? windowMs.endMs;
    const clamped = Math.min(endMs, Math.max(windowMs.startMs, timeMs));
    if (Math.abs(clamped - (this.state.currentTimeMs ?? 0)) < 400) return;
    const video = this.videoElement?.video;
    if (!video || !Number.isFinite(this.replayAnchorMs)) return;
    const offsetSec = (clamped - this.replayAnchorMs) / 1000;
    if (!Number.isFinite(offsetSec) || offsetSec < 0) return;
    video.currentTime = offsetSec;
    this.setState({ currentTimeMs: clamped });
  };

  stopPlayback = async () => {
    this.startGeneration += 1;
    this.clearEndWatchdog();
    this.setState({ isTimelineSeekable: false });
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
    const windowMs = this.replayWindowMs();
    const showTimeline =
      Boolean(windowMs) &&
      this.state.isTimelineSeekable &&
      this.state.currentTimeMs !== undefined &&
      this.state.currentTimeMs !== null;

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
        {showTimeline ? (
          <DashVideoTimeline
            startMs={windowMs.startMs}
            endMs={this.state.timelineEndMs ?? windowMs.endMs}
            currentTimeMs={this.state.currentTimeMs}
            seekable={this.state.isTimelineSeekable}
            onSeekEnd={this.seekTo}
          />
        ) : null}
      </div>
    );
  }

  componentWillUnmount() {
    this.unbindTimeUpdate();
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
  isTimelineSeekable: PropTypes.bool,
};
