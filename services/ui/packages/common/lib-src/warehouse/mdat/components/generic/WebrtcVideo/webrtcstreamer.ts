// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
// Modified version of webrtcstreamer.js from https://github.com/mpromonet/webrtc-streamer

import serialize from "serialize-javascript";
import { v4 as uuidv4 } from 'uuid';

const RewriteSdp = (sdp, newServerSettings) => {
  const { webrtcMaxBitrate, webrtcMinBitrate, webrtcStartBitrate } =
    newServerSettings;
  const sdpStringFind = "a=fmtp:(.*) (.*)";
  let sdpStringReplace = null;
  sdpStringReplace =
    "a=fmtp:$1 $2;x-google-max-bitrate=" +
    webrtcMaxBitrate +
    ";x-google-min-bitrate=" +
    webrtcMinBitrate +
    ";x-google-start-bitrate=" +
    webrtcStartBitrate;
  let newSDP = sdp.sdp.toString();
  newSDP = newSDP.replace(new RegExp(sdpStringFind, "g"), sdpStringReplace);
  sdp.sdp = newSDP;
  console.debug("using modified sdp: ", sdp);
  return sdp;
};

const checkVstError = (vstResponseJson) => {
  if (vstResponseJson.error_code || vstResponseJson.error == true) {
    throw new Error(vstResponseJson.error_message);
  }
};

const WebRtcStreamer = (function () {
  /**
   * Interface with WebRTC-streamer API
   * @constructor
   * @param {string} videoElement - id of the video element tag
   * @param {string} srvurl -  url of webrtc-streamer
   * @param {function} onErrorCallback -  called on WebRTC streaming error
   * @param {number} logLevel -  1 = info logs (default is 0)
   */
  const WebRtcStreamer = function WebRtcStreamer(
    videoElement,
    srvurl,
    onErrorCallback,
    streamActionHandlers,
    logLevel = 0
  ) {
    if (typeof videoElement === "string") {
      this.videoElement = document.getElementById(videoElement);
    } else {
      this.videoElement = videoElement;
    }
    this.srvurl = srvurl;
    this.pc = null;
    this._getIceCandidatePollIntervalId = null;
    this.candidateSet = new Set();

    this.pcOptions = { optional: [{ DtlsSrtpKeyAgreement: true }] };

    this.mediaConstraints = {
      offerToReceiveAudio: true,
      offerToReceiveVideo: true,
    };

    this.iceServers = null;
    this.earlyCandidates = [];
    this.logLevel = logLevel;
    this.onErrorCallback = onErrorCallback;
    this._streamActionHandlers = streamActionHandlers;
    // Stream States: NOT_STARTED, PLAYING, PAUSED, ENDED, ERRED
    this._streamState = "NOT_STARTED";
    this._mediaSessionId = undefined;
    this._currentTimeMs = undefined;
    this._isSeeking = false;
    this._fpsInvalidTryCount = 0;
    this._isReceivingFrames = true;
  };

  WebRtcStreamer.prototype._setMediaSessionId = function (id) {
    this._mediaSessionId = id;
  };

  // TODO: Move this out to a separate function outside of the class
  // same as checkVstError but for http errors
  WebRtcStreamer.prototype._handleHttpErrors = async function (response) {
    if (!response.ok) {
      // deep copy the response so that the original response is not mutated
      // and can be used again to extract the text
      const copiedResponse = response.clone();

      const vstResponseJson = JSON.parse(await copiedResponse.text());
      checkVstError(vstResponseJson);

      if (response.error_message) {
        throw new Error(response.error_message);
      }
      if (response.statusText) {
        throw new Error(response.statusText);
      }
    }

    return response;
  };

  /**
   * Connect a WebRTC Stream to videoElement
   * @param {object} videoParams - server stream start API call params for WebRTC video stream
   * @param {string} stream  -  local stream to send
   */
  WebRtcStreamer.prototype.connect = function (videoParams, localstream) {
    this._streamState = "NOT_STARTED";
    this._fpsInvalidTryCount = 0;
    this._isReceivingFrames = true;
    let newVideoParams = Object.assign({}, videoParams);
    this._initVideoParams = videoParams;

    // getIceServers is not already received
    if (!this.iceServers) {
      this.logLevel !== 0 && console.log("Get IceServers");

      fetch(`${this.srvurl}/iceServers`)
        .then(this._handleHttpErrors)
        .then((response) => response.text())
        .then((response) =>
          this.onReceiveGetIceServers.call(
            this,
            JSON.parse(response),
            newVideoParams,
            localstream
          )
        )
        .catch((error) => this.onError("GET IceServers " + error));
    } else {
      this.onReceiveGetIceServers(this.iceServers, newVideoParams, localstream);
    }
  };

  /**
   * Disconnect a WebRTC Stream and clear videoElement source
   */
  WebRtcStreamer.prototype.disconnect = function () {
    clearInterval(this._getIceCandidatePollIntervalId);
    clearTimeout(this._autoplayPauseCallTimeoutId);
    this._stopStatusPolling();
    if (this.videoElement) {
      this.videoElement.src = "";
    }
    if (this.pc) {
      if (this._mediaSessionId) {
        fetch(`${this.srvurl}/stream/stop`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            streamId: this._initVideoParams.streamId,
          },
          body: serialize({
            peerId: this.pc.peerid.toString(),
            mediaSessionId: this._mediaSessionId,
          }),
        })
          .then(this._handleHttpErrors)
          .catch((error) => this.onError("stream/stop " + error));
      }

      try {
        this.pc.close();
      } catch (e) {
        this.logLevel !== 0 &&
          console.error(
            "Failure close peer connection for peerId: " +
              this.pc.peerid +
              ", streamId: " +
              this._initVideoParams.streamId +
              ", error: " +
              e.message
          );
      }

      this.pc = null;
    }
  };

  /**
   * Seek a WebRTC Stream
   */
  WebRtcStreamer.prototype.seek = function (newTimeMs) {
    // Due to VST bug https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/4336841
    // disallow seek while ongoing seek operation on server else server errors out
    if (this._isSeeking) {
      console.warn(
        "[WebRtcStreamer] Ignoring new seek request while already ongoing previous seek"
      );
      return;
    }

    if (this._streamState === "ERRED" || this._streamState === "NOT_STARTED") {
      console.warn(
        "[WebRtcStreamer] Ignoring any action on erred/not-started stream"
      );
      return;
    }

    if (this.pc && this._mediaSessionId && this._currentTimeMs) {
      if (this._currentTimeMs === newTimeMs) {
        return;
      }

      const preSeekState = this._streamState;
      // determine seeking forward or backward
      const action =
        this._currentTimeMs - newTimeMs > 0 ? "seekBackward" : "seekForward";

      this._isSeeking = true;

      fetch(`${this.srvurl}/stream/seek`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          streamId: this._initVideoParams.streamId,
        },
        body: serialize({
          peerId: this.pc.peerid.toString(),
          mediaSessionId: this._mediaSessionId,
          action: action,
          value: new Date(newTimeMs).toISOString(),
        }),
      })
        .then(this._handleHttpErrors)
        .then(() => {
          // (due to VST bug https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/4331362)
          // TODO: remove once bug fixed
          // if seek success then pause
          preSeekState === "PAUSED" && this.pause();
          preSeekState === "PLAYING" && this.resume();
        })
        .catch((error) => this.onError("stream/seek " + error))
        .finally(() => {
          // TODO: remove once bug https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/4336841 resolved
          // too soon if a two seek requests are sent, server errors out.
          // By experimenting, seen that two seek requests should be atleast 500ms
          // apart for the server to not error out.
          this._seekingFlagFlipTimeoutId = setTimeout(
            () => (this._isSeeking = false),
            500
          );
        });
    }
  };

  /**
   * Pause a WebRTC Stream
   */
  WebRtcStreamer.prototype.pause = function () {
    if (this._streamState === "ERRED") {
      console.warn("[WebRtcStreamer] Ignoring any action on erred stream");
      return;
    }

    if (this._streamState === "NOT_STARTED" || this._streamState === "ENDED") {
      console.warn(
        "[WebRtcStreamer] Ignoring request to pause an ended/not-started stream"
      );
      return;
    }

    if (this.pc && this._mediaSessionId) {
      fetch(`${this.srvurl}/stream/pause`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          streamId: this._initVideoParams.streamId,
        },
        body: serialize({
          peerId: this.pc.peerid.toString(),
          mediaSessionId: this._mediaSessionId,
        }),
      })
        .then(this._handleHttpErrors)
        .catch((error) => this.onError("stream/pause " + error));
    }
  };

  /**
   * Resume a WebRTC Stream
   */
  WebRtcStreamer.prototype.resume = function () {
    if (this._streamState === "ERRED") {
      console.warn("[WebRtcStreamer] Ignoring any action on erred stream");
      return;
    }

    if (this.pc && this._mediaSessionId) {
      fetch(`${this.srvurl}/stream/resume`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          streamId: this._initVideoParams.streamId,
        },
        body: serialize({
          peerId: this.pc.peerid.toString(),
          mediaSessionId: this._mediaSessionId,
        }),
      })
        .then(this._handleHttpErrors)
        .catch((error) => this.onError("stream/resume " + error));
    }
  };

  /**
   * Keep checking status of a connected stream
   */
  WebRtcStreamer.prototype._startStatusPolling = function () {
    this._startStatusPollingIntervalTimerId = setInterval(() => {
      if (!this.pc || !this._mediaSessionId || this._streamState === "ERRED") {
        return;
      }

      if (this.videoElement) {
        if (!document.body.contains(this.videoElement)) {
          this.onError(
            "Video element no longer exists in DOM, disconnecting stream"
          );
          return;
        }
        if (
          this.videoElement.srcObject &&
          this.originalStream &&
          this.videoElement.srcObject !== this.originalStream
        ) {
          this.onError("Stream source has changed, disconnecting stream");
          return;
        }
      }

      fetch(
        `${
          this.srvurl
        }/stream/query?peerId=${this.pc.peerid.toString()}&metadata=false`,
        {
          headers: {
            streamId: this._initVideoParams.streamId,
          },
        }
      )
        .then(this._handleHttpErrors)
        .then((response) => response.text())
        .then((response) => {
          this._currentTimeMs = JSON.parse(response).ts;
          this._streamActionHandlers &&
            this._streamActionHandlers.onTimeUpdate &&
            this._streamActionHandlers.onTimeUpdate(this._currentTimeMs);
        })
        .catch((error) => this.onError("stream/query " + error));

      fetch(
        `${
          this.srvurl
        }/stream/status?peerId=${this.pc.peerid.toString()}&mediaSessionId=${
          this._mediaSessionId
        }`,
        {
          headers: {
            streamId: this._initVideoParams.streamId,
          },
        }
      )
        .then((response) => response.text())
        .then((response) => {
          let res = JSON.parse(response);
          if (res.error == true || res.error_code) {
            let errMsg = "Streaming Erred";
            if (res.error_code && res.error_message) {
              errMsg += " : " + res.error_message;
            }
            throw new Error(errMsg);
          } else {
            res.state && this._processStreamState(res.state);
          }
        })
        .catch((error) => {
          this.onError("stream/status " + error);
        });

      if (this._streamState === "PLAYING") {
        // check FPS here in receiver when stream playing from server side
        if (this._fpsInvalidTryCount > 5) {
          // past 5 attempts (= 2.5sec) showed no fps
          if (this._isReceivingFrames) {
            this._isReceivingFrames = false;
            this._streamActionHandlers &&
              this._streamActionHandlers.onNotReceivingFrames &&
              this._streamActionHandlers.onNotReceivingFrames();
          }
        }

        this._checkVideoFps();
      } else {
        this._fpsInvalidTryCount = 0;
        this._isReceivingFrames = true;
      }
    }, 500);
  };

  /**
   * Check video FPS by collecting stats from peer connection
   */
  WebRtcStreamer.prototype._checkVideoFps = function () {
    this.pc.getStats(null).then((stats) => {
      stats.forEach((report) => this._processVideoStats(report));
    });
  };

  /**
   * Process a single video stats report to track FPS
   */
  WebRtcStreamer.prototype._processVideoStats = function (report) {
    if (report.type === "inbound-rtp" && report.kind === "video") {
      if (report.framesPerSecond) {
        this._fpsInvalidTryCount = 0;
        if (!this._isReceivingFrames) {
          this._isReceivingFrames = true;
          this._streamActionHandlers &&
            this._streamActionHandlers.onReceivingFrames &&
            this._streamActionHandlers.onReceivingFrames();
        }
      } else {
        this._fpsInvalidTryCount++;
      }
    }
  };

  /**
   * Keep checking status of a connected stream
   */
  WebRtcStreamer.prototype._stopStatusPolling = function () {
    clearInterval(this._startStatusPollingIntervalTimerId);
  };

  /**
   * Handle NOT_PLAYING state transition
   */
  WebRtcStreamer.prototype._handleNotPlayingState = function () {
    if (
      this._streamState === "NOT_STARTED" ||
      this._streamState === "ENDED"
    ) {
      // no change from server
      return;
    }
    if (this._streamState !== "NOT_STARTED") {
      // EOS as per https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/3419695
      this._streamState = "ENDED";
      this._streamActionHandlers &&
        this._streamActionHandlers.onEnd &&
        this._streamActionHandlers.onEnd();
      this.videoElement.style.opacity = "0.75";
    }
  };

  /**
   * Handle transition to PLAYING from NOT_STARTED state
   */
  WebRtcStreamer.prototype._handlePlayingFromNotStarted = function () {
    if (
      (this._currentTimeMs !== undefined && this._currentTimeMs <= 0) ||
      this._currentTimeMs === undefined
    ) {
      // if invalid current stream time from the server, then don't treat the state as started
      return;
    }

    // started
    this.videoElement.style.opacity = "1.0";
    this._streamState = "PLAYING";

    // handle no autoplay
    if (this._initVideoParams && this._initVideoParams.autoPlay === false) {
      this.pause();
    }

    fetch(
      `${
        this.srvurl
      }/stream/query?peerId=${this.pc.peerid.toString()}&metadata=false`,
      {
        headers: {
          streamId: this._initVideoParams.streamId,
        },
      }
    )
      .then(this._handleHttpErrors)
      .then((response) => response.text())
      .then((response) => {
        this._streamActionHandlers &&
          this._streamActionHandlers.onStart &&
          this._streamActionHandlers.onStart(JSON.parse(response).ts);
      })
      .catch((error) => this.onError("stream/query " + error));
  };

  /**
   * Handle transition to PLAYING from PAUSED or ENDED state
   */
  WebRtcStreamer.prototype._handlePlayingResume = function () {
    this.videoElement.style.opacity = "1.0";
    this._streamState = "PLAYING";
    this._streamActionHandlers &&
      this._streamActionHandlers.onResume &&
      this._streamActionHandlers.onResume();
  };

  /**
   * Handle PLAYING state transition
   */
  WebRtcStreamer.prototype._handlePlayingState = function () {
    if (this._streamState === "PLAYING") {
      return;
    }
    if (this._streamState === "NOT_STARTED") {
      this._handlePlayingFromNotStarted();
      return;
    }
    if (this._streamState === "PAUSED" || this._streamState === "ENDED") {
      this._handlePlayingResume();
    }
  };

  /**
   * Handle PAUSED state transition
   */
  WebRtcStreamer.prototype._handlePausedState = function () {
    if (this._streamState === "NOT_STARTED") {
      // stream didnt start even once yet. ignore this paused
      return;
    }
    if (this._streamState !== "PAUSED") {
      // just paused
      this.videoElement.style.opacity = "0.85";
      this._streamState = "PAUSED";
      this._streamActionHandlers &&
        this._streamActionHandlers.onPause &&
        this._streamActionHandlers.onPause();
    }
  };

  /**
   * Process status to update state
   */
  WebRtcStreamer.prototype._processStreamState = function (newState) {
    if (this._streamState === "ERRED") {
      console.log(
        "[WebRtcStreamer] erred stream. " +
          "Ignoring new state (" +
          newState +
          ") from server."
      );
      return;
    }

    if (newState === "NOT_PLAYING") {
      this._handleNotPlayingState();
    } else if (newState === "PLAYING") {
      this._handlePlayingState();
    } else if (newState === "PAUSED") {
      this._handlePausedState();
    } else {
      console.warn("WebRTC Streaming unrecognized state: " + newState);
    }
  };

  /**
   * Find suitable video resolution height from VST streams metadata
   */
  WebRtcStreamer.prototype._findSuitableResolutionHeight = function (
    streamsResponse,
    settingsResponse,
    streamId
  ) {
    let suitableHeight;
    if (!streamsResponse || !Array.isArray(streamsResponse)) {
      return suitableHeight;
    }

    streamsResponse.forEach((stream) => {
      if (!(streamId in stream)) return;
      if (!Array.isArray(stream[streamId])) return;

      stream[streamId].forEach((streamMetadata) => {
        if (!streamMetadata.isMain) return;

        const match = streamMetadata.metadata.resolution.match(/^\d+x(\d+)$/);
        if (match) {
          suitableHeight = match[1];
          return;
        }

        console.warn(
          `Invalid resolution for stream:${streamId}: ` +
            `'${streamMetadata.metadata.resolution}' ` +
            `will be ignored. Will try to use default resolution.`
        );
        suitableHeight = this._getDefaultResolutionHeight(settingsResponse, streamId);
      });
    });

    return suitableHeight;
  };

  /**
   * Get default resolution height from settings response
   */
  WebRtcStreamer.prototype._getDefaultResolutionHeight = function (
    settingsResponse,
    streamId
  ) {
    if (
      !settingsResponse ||
      !settingsResponse.webrtc_video_quality_tunning ||
      Object.keys(settingsResponse.webrtc_video_quality_tunning).length === 0
    ) {
      return undefined;
    }

    const defaultResolutionHeight = Object.keys(
      settingsResponse.webrtc_video_quality_tunning
    )[0];
    const heightMatch = defaultResolutionHeight.match(/resolution_(\d+)/);
    if (heightMatch) {
      console.warn(
        "Using default resolution:",
        defaultResolutionHeight,
        "for stream:",
        streamId
      );
      return heightMatch[1];
    }
    return undefined;
  };

  /**
   * Fetch configuration and streams, then start the stream session
   */
  WebRtcStreamer.prototype._fetchConfigAndStartStream = function (
    sessionDescription,
    videoParams
  ) {
    const bind = this;
    fetch(`${this.srvurl}/configuration`)
      .then(this._handleHttpErrors)
      .then((settingsResponse) => settingsResponse.json())
      .then((settingsResponse) => {
        bind._fetchStreamsAndStartSession(settingsResponse, sessionDescription, videoParams);
      })
      .catch((error) => bind.onError("configuration " + error));
  };

  /**
   * Fetch streams metadata and start stream session
   */
  WebRtcStreamer.prototype._fetchStreamsAndStartSession = function (
    settingsResponse,
    sessionDescription,
    videoParams
  ) {
    const bind = this;
    fetch(`${this.srvurl}/streams`)
      .then(this._handleHttpErrors)
      .then((streamsResponse) => streamsResponse.json())
      .then((streamsResponse) => {
        const height = bind._findSuitableResolutionHeight(
          streamsResponse,
          settingsResponse,
          videoParams.streamId
        );

        let updatedSessionDescription = sessionDescription;
        const qualityConfig = settingsResponse &&
          settingsResponse.webrtc_video_quality_tunning &&
          settingsResponse.webrtc_video_quality_tunning[`resolution_${height}`];

        if (!height || !qualityConfig) {
          console.warn(
            `No suitable stream resolution height found for stream: ${videoParams.streamId}`
          );
        } else {
          updatedSessionDescription = RewriteSdp(sessionDescription, {
            webrtcMinBitrate: qualityConfig.bitrate_range[0],
            webrtcMaxBitrate: qualityConfig.bitrate_range[1],
            webrtcStartBitrate: qualityConfig.bitrate_start,
          });
        }

        bind._setLocalDescriptionAndStartStream(updatedSessionDescription, videoParams);
      })
      .catch((error) => bind.onError("streams metadata" + error));
  };

  /**
   * Set local description and start the stream
   */
  WebRtcStreamer.prototype._setLocalDescriptionAndStartStream = function (
    sessionDescription,
    videoParams
  ) {
    const bind = this;
    this.pc.setLocalDescription(
      sessionDescription,
      function () {
        if (bind._streamState === "ERRED") {
          console.warn("[WebRtcStreamer] Ignoring any action on erred stream");
          return;
        }
        if (bind._streamState !== "NOT_STARTED") {
          console.warn(
            "[WebRtcStreamer] Ignoring request to start an already started stream"
          );
          return;
        }

        Object.assign(videoParams, {
          peerId: bind.pc.peerid.toString(),
          sessionDescription: sessionDescription,
        });

        fetch(`${bind.srvurl}/stream/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: serialize(videoParams),
        })
          .then(bind._handleHttpErrors)
          .then((response) => response.text())
          .then((response) => {
            const jsonResponse = JSON.parse(response);
            bind._setMediaSessionId.call(bind, jsonResponse.mediaSessionId);
            bind._startStatusPolling.call(bind);
            return bind.onReceiveCall.call(bind, jsonResponse);
          })
          .catch((error) => bind.onError("stream/start " + error));
      },
      function (error) {
        bind.logLevel !== 0 &&
          console.log("setLocalDescription error:" + JSON.stringify(error));
      }
    );
  };

  /*
   * GetIceServers callback
   */
  WebRtcStreamer.prototype.onReceiveGetIceServers = function (
    iceServers,
    videoParams,
    stream
  ) {
    this.iceServers = iceServers;
    this.pcConfig = iceServers || { iceServers: [] };
    try {
      this.createPeerConnection();

      if (stream) {
        this.pc.addStream(stream);
      }

      // clear early candidates
      this.earlyCandidates.length = 0;
      this.candidateSet.clear();

      // create Offer
      const bind = this;
      this.pc.createOffer(this.mediaConstraints).then(
        function (sessionDescription) {
          bind.logLevel !== 0 &&
            console.log("Create offer:" + JSON.stringify(sessionDescription));

          bind._fetchConfigAndStartStream(sessionDescription, videoParams);
        },
        function (error) {
          bind.logLevel !== 0 &&
            console.log("Create offer error:" + JSON.stringify(error));
        }
      );
    } catch (e) {
      this.onError("connect error: " + e);
    }
  };

  WebRtcStreamer.prototype.getIceCandidate = function () {
    fetch(`${this.srvurl}/iceCandidate?peerId=${this.pc.peerid.toString()}`, {
      headers: {
        streamId: this._initVideoParams.streamId,
      },
    })
      .then(this._handleHttpErrors)
      .then((response) => response.text())
      .then((response) =>
        this.onReceiveCandidate.call(this, JSON.parse(response))
      )
      .catch((error) => this.onError("GET IceCandidate " + error));
  };

  /*
   * create RTCPeerConnection
   */
  WebRtcStreamer.prototype.createPeerConnection = function () {
    this.logLevel !== 0 &&
      console.log(
        "createPeerConnection  config: " +
          JSON.stringify(this.pcConfig) +
          " option:" +
          JSON.stringify(this.pcOptions)
      );
    this.pc = new RTCPeerConnection(this.pcConfig, this.pcOptions);
    const pc = this.pc;
    pc.peerid = uuidv4();

    const bind = this;
    pc.onicecandidate = function (evt) {
      bind.onIceCandidate.call(bind, evt);
    };
    pc.onaddstream = function (evt) {
      bind.onAddStream.call(bind, evt);
    };
    pc.oniceconnectionstatechange = function (evt) {
      bind.logLevel !== 0 &&
        console.log(
          "oniceconnectionstatechange  state: " + pc.iceConnectionState
        );
      if (bind.videoElement) {
        if (pc.iceConnectionState === "connected") {
          clearInterval(bind._getIceCandidatePollIntervalId);
          bind.videoElement.style.opacity = "1.0";
        } else if (pc.iceConnectionState === "disconnected") {
          clearInterval(bind._getIceCandidatePollIntervalId);
          bind.videoElement.style.opacity = "0.75";
        } else if (
          pc.iceConnectionState === "failed" ||
          pc.iceConnectionState === "closed"
        ) {
          clearInterval(bind._getIceCandidatePollIntervalId);
          bind.videoElement.style.opacity = "0.75";
        } else if (pc.iceConnectionState === "new") {
          bind.getIceCandidate.call(bind);
        }
      }
    };
    // pc.ondatachannel = function(evt) {
    // 	bind.logLevel !== 0 && console.log("remote datachannel created:"+JSON.stringify(evt));

    // 	evt.channel.onopen = function () {
    // 		bind.logLevel !== 0 && console.log("remote datachannel open");
    // 		this.send("remote channel openned");
    // 	}
    // 	evt.channel.onmessage = function (event) {
    // 		bind.logLevel !== 0 && console.log("remote datachannel recv:"+JSON.stringify(event.data));
    // 	}
    // }
    pc.onicegatheringstatechange = function () {
      if (pc.iceGatheringState === "complete") {
        const recvs = pc.getReceivers();

        recvs.forEach((recv) => {
          if (recv.track && recv.track.kind === "video") {
            bind.logLevel !== 0 &&
              console.log(
                "codecs:" + JSON.stringify(recv.getParameters().codecs)
              );
          }
        });
      }
    };

    // try {
    // 	var dataChannel = pc.createDataChannel("ClientDataChannel");
    // 	dataChannel.onopen = function() {
    // 		bind.logLevel !== 0 && console.log("local datachannel open");
    // 		this.send("local channel openned");
    // 	}
    // 	dataChannel.onmessage = function(evt) {
    // 		bind.logLevel !== 0 && console.log("local datachannel recv:"+JSON.stringify(evt.data));
    // 	}
    // } catch (e) {
    // 	this.logLevel !== 0 && console.log("Cannor create datachannel error: " + e);
    // }

    this.logLevel !== 0 &&
      console.log(
        "Created RTCPeerConnnection with config: " +
          JSON.stringify(this.pcConfig) +
          "option:" +
          JSON.stringify(this.pcOptions)
      );
    return pc;
  };

  /*
   * RTCPeerConnection IceCandidate callback
   */
  WebRtcStreamer.prototype.onIceCandidate = function (event) {
    if (event.candidate) {
      if (this.pc && this.pc.currentRemoteDescription) {
        this.addIceCandidate(this.pc.peerid, event.candidate);
      } else {
        this.earlyCandidates.push(event.candidate);
      }
    } else {
      this.logLevel !== 0 && console.log("End of candidates.");
    }
  };

  WebRtcStreamer.prototype.addIceCandidate = function (peerid, candidate) {
    fetch(`${this.srvurl}/iceCandidate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        peerId: peerid.toString(),
        candidate: candidate,
      }),
    })
      .then(this._handleHttpErrors)
      .then((response) => response.text())
      .then((response) => {
        this.logLevel !== 0 &&
          console.log("ADD IceCandidate ok:" + JSON.parse(response));
      })
      .catch((error) => this.onError("ADD IceCandidate " + error));
  };

  /*
   * RTCPeerConnection AddTrack callback
   */
  WebRtcStreamer.prototype.onAddStream = function (event) {
    this.logLevel !== 0 &&
      console.log("Remote track added:" + JSON.stringify(event));

    this.videoElement.srcObject = event.stream;
    this.originalStream = event.stream; // Store the original stream
    const promise = this.videoElement.play();
    if (promise !== undefined) {
      promise.catch((error) => {
        console.warn("video element play error: " + error);
      });
    }
  };

  /*
   * AJAX /call callback
   */
  WebRtcStreamer.prototype.onReceiveCall = function (dataJson) {
    const bind = this;
    this.logLevel !== 0 && console.log("offer: " + JSON.stringify(dataJson));
    const descr = new RTCSessionDescription(dataJson);
    this.pc &&
      this.pc.setRemoteDescription(
        descr,
        function () {
          bind.logLevel !== 0 && console.log("setRemoteDescription ok");
          while (bind.earlyCandidates.length) {
            const candidate = bind.earlyCandidates.shift();
            bind.addIceCandidate.call(bind, bind.pc.peerid, candidate);
          }

          bind.getIceCandidate.call(bind);
          clearInterval(bind._getIceCandidatePollIntervalId);
          bind._getIceCandidatePollIntervalId = setInterval(() => {
            bind.getIceCandidate.call(bind);
          }, 500);
        },
        function (error) {
          bind.logLevel !== 0 &&
            console.log("setRemoteDescription error:" + JSON.stringify(error));
          clearInterval(bind._getIceCandidatePollIntervalId);
        }
      );
  };

  /*
   * AJAX /getIceCandidate callback
   */
  WebRtcStreamer.prototype.onReceiveCandidate = function (dataJson) {
    this.logLevel !== 0 &&
      console.log("candidate: " + JSON.stringify(dataJson));
    if (dataJson) {
      for (let i = 0; i < dataJson.length; i++) {
        if (!this.candidateSet.has(JSON.stringify(dataJson[i]))) {
          this.candidateSet.add(JSON.stringify(dataJson[i]));
          const candidate = new RTCIceCandidate(dataJson[i]);
          this.logLevel !== 0 &&
            console.log("Adding ICE candidate :" + JSON.stringify(candidate));
          const bind = this;
          this.pc.addIceCandidate(
            candidate,
            function () {
              bind.logLevel !== 0 && console.log("addIceCandidate OK");
            },
            function (error) {
              bind.logLevel !== 0 &&
                console.log("addIceCandidate error:" + JSON.stringify(error));
            }
          );
        }
      }
      this.pc.addIceCandidate();
    }
  };

  /*
   * AJAX callback for Error
   */
  WebRtcStreamer.prototype.onError = function (status) {
    clearTimeout(this._seekingFlagFlipTimeoutId);
    clearTimeout(this._autoplayPauseCallTimeoutId);
    this._stopStatusPolling();
    clearInterval(this._getIceCandidatePollIntervalId);
    console.error(
      `[WebRtcStreamer] (streamId: ${this._initVideoParams.streamId}) onError: ${status}`
    );
    this._streamState !== "ERRED" && this.disconnect();
    this._streamState = "ERRED";
    this._isSeeking = false;
    this._isReceivingFrames = false;
    this.onErrorCallback && this.onErrorCallback(status);
  };

  return WebRtcStreamer;
})();

export default WebRtcStreamer;
