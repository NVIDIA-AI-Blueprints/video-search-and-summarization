// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import {
  Fullscreen as FullscreenIcon,
  FullscreenExit as FullscreenExitIcon,
  PauseCircle as PauseCircleIcon,
  PlayCircle as PlayCircleIcon,
} from "common/warehouse/mui-icons";

import CssClasses from "./VideoElement.module.css";
import LoaderDisplay from "../LoaderDisplay/LoaderDisplay";
import UserMessage from "../../../utils/UserMessage";

/**
 * HTML Video Element used by aggregating Video Streaming Protocol
 * classes (WebRTC, Hls etc.) as media content source.
 * This is a customized version of the HTML Video Element.
 *
 * Notes -
 *
 * The Video controls are custom controls rather than default
 * HTML5 video controls. AutoPlay mode is ON by default.
 *
 * HTML5 Video Element can be accessed by video property
 */
export default class VideoElement extends React.PureComponent {
  state = {
    hideVideoControls: false,
    videoControlPlaypauseLabel: this.VIDEO_CONTROL_PLAY_LABEL,
    showLoader: true,
    coverMsg: "",
    isFullscreen: false,
  };

  isAutoPlay() {
    if (typeof this.props.htmlVideoTagOptions.autoPlay === "boolean") {
      return this.props.htmlVideoTagOptions.autoPlay;
    }
    // default is auto play
    return true;
  }

  onClickBtnFullscreen = () => {
    if (
      !this.video ||
      !this.video.parentElement ||
      !this.video.parentElement.parentElement ||
      !this.video.parentElement.parentElement.parentElement
    )
      return;

    if (
      document.fullscreenElement ===
        this.video.parentElement.parentElement.parentElement ||
      document.mozFullScreenElement ===
        this.video.parentElement.parentElement.parentElement ||
      document.webkitFullscreenElement ===
        this.video.parentElement.parentElement.parentElement ||
      document.msFullscreenElement ===
        this.video.parentElement.parentElement.parentElement
    ) {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (document.mozFullScreenElement) {
        /* Firefox */
        document.mozCancelFullScreen();
      } else if (document.webkitFullscreenElement) {
        /* Chrome, Safari and Opera */
        document.webkitExitFullscreen();
      } else if (document.msFullscreenElement) {
        /* IE/Edge */
        document.msExitFullscreen();
      }

      this.setState({ isFullscreen: false });
    } else {
      if (
        this.video.parentElement.parentElement.parentElement.requestFullscreen
      ) {
        this.video.parentElement.parentElement.parentElement.requestFullscreen();
      } else if (
        this.video.parentElement.parentElement.parentElement
          .mozRequestFullScreen
      ) {
        this.video.parentElement.parentElement.parentElement.mozRequestFullScreen(); // Firefox
      } else if (
        this.video.parentElement.parentElement.parentElement
          .webkitRequestFullscreen
      ) {
        this.video.parentElement.parentElement.parentElement.webkitRequestFullscreen(); // Chrome and Safari
      }

      this.setState({ isFullscreen: true });
    }
  };

  onClickBtnPlaypause = () => {
    if (!this.video || !this.videoControlPlaypause) return;

    if (
      this.state.videoControlPlaypauseLabel === this.VIDEO_CONTROL_PLAY_LABEL
    ) {
      this.props.controlsActionsHandlers &&
        this.props.controlsActionsHandlers.onClickPlay &&
        this.props.controlsActionsHandlers.onClickPlay();
      // Play the this.video
      this.video.play();
    } else {
      this.props.controlsActionsHandlers &&
        this.props.controlsActionsHandlers.onClickPause &&
        this.props.controlsActionsHandlers.onClickPause();
      // Pause the this.video
      this.video.pause();
    }
  };

  // Switches play pause button label
  //    param: new Sate: this.VIDEO_CONTROL_PLAY_LABEL || this.VIDEO_CONTROL_PAUSE_LABEL
  switchPlayPauseBtn = (newBtnState) => {
    if (!this.video || !this.props.videoControls.playpause) return;

    if (newBtnState) {
      if (
        newBtnState !== this.VIDEO_CONTROL_PLAY_LABEL &&
        newBtnState !== this.VIDEO_CONTROL_PAUSE_LABEL
      ) {
        // ignore unrecognized new state
        return;
      }
      this.setState({ videoControlPlaypauseLabel: newBtnState });
    } else {
      // if (this.videoControlPlaypause) {
      if (
        this.state.videoControlPlaypauseLabel === this.VIDEO_CONTROL_PLAY_LABEL
      ) {
        // Update the button text to 'Pause'
        this.setState({
          videoControlPlaypauseLabel: this.VIDEO_CONTROL_PAUSE_LABEL,
        });
      } else {
        // Update the button text to 'Play'
        this.setState({
          videoControlPlaypauseLabel: this.VIDEO_CONTROL_PLAY_LABEL,
        });
      }
    }
  };

  addCustomVideoControlHandlers() {
    if (!this.video) return;

    // Hide/Show Video Controls
    this.video.addEventListener(
      "mousemove",
      (e) => {
        clearTimeout(this.hideVideoControlsTimeoutId);
        this.state.hideVideoControls === true &&
          this.setState({ hideVideoControls: false });
        // Display controls for some time
        this.hideVideoControlsTimeoutId = setTimeout(() => {
          this.state.hideVideoControls === false &&
            this.setState({ hideVideoControls: true });
        }, this.HIDE_VIDEO_CONTROLS_TIMEOUT_MS);
      },
      false
    );
    this.video.addEventListener("play", (e) => {
      this.setState({
        videoControlPlaypauseLabel: this.VIDEO_CONTROL_PAUSE_LABEL,
      });
    });

    let updatePlaypauseBtnText2Play = () => {
      this.setState({
        videoControlPlaypauseLabel: this.VIDEO_CONTROL_PLAY_LABEL,
      });
    };

    this.video.addEventListener("pause", updatePlaypauseBtnText2Play);
    this.video.addEventListener("ended", updatePlaypauseBtnText2Play);
  }

  UNSAFE_componentWillMount() {
    this.isAutoPlay()
      ? this.setState({
          videoControlPlaypauseLabel: this.VIDEO_CONTROL_PAUSE_LABEL,
        })
      : this.setState({
          videoControlPlaypauseLabel: this.VIDEO_CONTROL_PLAY_LABEL,
        });
  }

  onPlaying = (e) => {
    !this.state.showLoader && this.setState({ showLoader: false });
  };

  addEventListeners = (prevProps, props) => {
    if (prevProps !== null) {
      this.removeEventListeners(prevProps);
    }

    if (props.htmlMediaElementEventHandlers && this.video) {
      this.video.addEventListener(
        "canplaythrough",
        props.htmlMediaElementEventHandlers.onCanPlayThrough
      );
      this.video.addEventListener(
        "play",
        props.htmlMediaElementEventHandlers.onPlay
      );
      this.video.addEventListener(
        "pause",
        props.htmlMediaElementEventHandlers.onPause
      );
      this.video.addEventListener(
        "playing",
        props.htmlMediaElementEventHandlers.onPlaying
      );
      this.video.addEventListener(
        "loadstart",
        props.htmlMediaElementEventHandlers.onLoadStart
      );
    }

    this.video && this.video.addEventListener("playing", this.onPlaying);
  };

  removeEventListeners = (props) => {
    if (props.htmlMediaElementEventHandlers && this.video) {
      this.video.removeEventListener(
        "canplaythrough",
        props.htmlMediaElementEventHandlers.onCanPlayThrough
      );
      this.video.removeEventListener(
        "play",
        props.htmlMediaElementEventHandlers.onPlay
      );
      this.video.removeEventListener(
        "pause",
        props.htmlMediaElementEventHandlers.onPause
      );
      this.video.removeEventListener(
        "playing",
        props.htmlMediaElementEventHandlers.onPlaying
      );

      this.video.removeEventListener(
        "loadstart",
        props.htmlMediaElementEventHandlers.onLoadStart
      );
      this.video.removeEventListener(
        "error",
        props.htmlMediaElementEventHandlers.onError
      );
      this.video.removeEventListener("error", this.onError);
    }
  };

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      this.props.htmlMediaElementEventHandlers !==
      nextProps.htmlMediaElementEventHandlers
    ) {
      this.addEventListeners(this.props, nextProps);
    }
  }

  componentDidMount() {
    this.addEventListeners(null, this.props);
    this.addCustomVideoControlHandlers();
    // Hide controls after some time
    this.hideVideoControlsOnMountTimeoutId = setTimeout(() => {
      this.setState({ hideVideoControls: true });
    }, this.HIDE_VIDEO_CONTROLS_TIMEOUT_MS);
  }

  /**
   * Determine control visibility from props
   */
  _getControlVisibility() {
    const videoControls = this.props.videoControls;
    const isFullscreenControl = videoControls?.fullscreen ?? true;
    const isPlaypauseControl = videoControls?.playpause ?? false;

    return { isFullscreenControl, isPlaypauseControl };
  }

  /**
   * Render loader spinner
   */
  _renderLoader() {
    if (!this.state.showLoader) {
      return null;
    }
    return (
      <div className={CssClasses.spinnerContainer}>
        <LoaderDisplay
          type={LoaderDisplay.TYPE.LOADING}
          size={LoaderDisplay.SIZE.SMALL}
          userMessage={
            new UserMessage(
              UserMessage.SRC_TYPE.GENERIC,
              UserMessage.MESSAGE_LEVEL.INFO,
              "Video",
              "Fetching"
            )
          }
        />
      </div>
    );
  }

  /**
   * Render cover message
   */
  _renderCoverMessage() {
    if (this.state.coverMsg === "") {
      return null;
    }
    return (
      <div className={CssClasses.cover}>
        <b>{this.state.coverMsg}</b>
      </div>
    );
  }

  /**
   * Render fullscreen button icon
   */
  _renderFullscreenButton() {
    if (this.state.isFullscreen) {
      return <FullscreenExitIcon />;
    }
    return <FullscreenIcon />;
  }

  /**
   * Render play/pause button icon
   */
  _renderPlayPauseIcon() {
    if (
      this.state.videoControlPlaypauseLabel === this.VIDEO_CONTROL_PLAY_LABEL
    ) {
      return <PlayCircleIcon />;
    }
    return <PauseCircleIcon />;
  }

  /**
   * Render fullscreen button control
   */
  _renderFullscreenButtonControl() {
    return (
      <button
        ref={(ref) => (this.videoControlFullScreen = ref)}
        className={CssClasses.controlsButton}
        type="button"
        onClick={this.onClickBtnFullscreen}
        title={
          this.state.isFullscreen ? "Exit Fullscreen" : "Fullscreen"
        }
      >
        {this._renderFullscreenButton()}
      </button>
    );
  }

  /**
   * Render play/pause button control
   */
  _renderPlayPauseButtonControl() {
    return (
      <button
        ref={(ref) => (this.videoControlPlaypause = ref)}
        className={CssClasses.controlsButton}
        type="button"
        onClick={this.onClickBtnPlaypause}
        title={this.state.videoControlPlaypauseLabel}
      >
        {this._renderPlayPauseIcon()}
      </button>
    );
  }

  /**
   * Render video controls (fullscreen and play/pause buttons)
   */
  _renderVideoControls(isFullscreenControl, isPlaypauseControl) {
    if (this.state.showLoader || this.state.hideVideoControls === true) {
      return null;
    }

    const shouldShowPlayPause =
      this.state.coverMsg !== "ENDED" && isPlaypauseControl === true;

    return (
      <div
        ref={(ref) => (this.videoControls = ref)}
        className={CssClasses.videoControls}
      >
        {isFullscreenControl === true
          ? this._renderFullscreenButtonControl()
          : null}
        {shouldShowPlayPause ? this._renderPlayPauseButtonControl() : null}
      </div>
    );
  }

  render() {
    const { isFullscreenControl, isPlaypauseControl } =
      this._getControlVisibility();

    return (
      <div className={CssClasses.videoContainer}>
        {this._renderLoader()}
        {this._renderCoverMessage()}
        <video
          ref={(ref) => (this.video = ref)}
          className={CssClasses.videoFrame}
          muted
          autoPlay={this.isAutoPlay()}
          height={this.props.htmlVideoTagOptions.height}
          width={this.props.htmlVideoTagOptions.width}
        >
          Your browser does not support the video tag.
        </video>
        {this._renderVideoControls(isFullscreenControl, isPlaypauseControl)}
      </div>
    );
  }

  componentWillUnmount() {
    clearTimeout(this.hideVideoControlsTimeoutId);
    clearTimeout(this.hideVideoControlsOnMountTimeoutId);
    this.removeEventListeners(this.props);
    this.video && this.video.removeEventListener("playing", this.onPlaying);
  }
}

VideoElement.prototype.VIDEO_CONTROL_PLAY_LABEL = "Play";
VideoElement.prototype.VIDEO_CONTROL_PAUSE_LABEL = "Pause";
VideoElement.prototype.HIDE_VIDEO_CONTROLS_TIMEOUT_MS = 3000;

VideoElement.propTypes = {
  /**
   * Object containing optional attributes for the html video tag. Refer to
   * https://www.w3schools.com/tags/tag_video.asp
   * Example: {height:, width: }
   *
   * Note: Only following options are used as of now: height, width, autoPlay.
   */
  htmlVideoTagOptions: PropTypes.object.isRequired,
  /**
   * Object containing information about which custom controls to activate.
   * Available controls are - play/pause, fullscreen.
   * If not provided then fullscreen control is on by default while not others.
   * videoControls = {
   *   fullscreen: true,
   *   playpause: false,
   * }
   */
  videoControls: PropTypes.object,
  /**
   * Object containing callbacks for HTMLMediaElement event handlers as described under
   * https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement
   */
  htmlMediaElementEventHandlers: PropTypes.object,
  /**
   * Object containing callbacks for custom controls actions handlers
   */
  controlsActionsHandlers: PropTypes.object,
};
