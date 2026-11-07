// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import Styles from "./EventThumbnail.module.css";
import { joinUrl, isAbortError, readJson, readBlob } from "common/warehouse";
import { isEqual } from "lodash";
import { HideImage as HideImageIcon } from "common/warehouse/mui-icons";
import React from "react";
import { Line } from "react-konva";

import CameraImageView from "./CameraImageView";
import UserMessage from "../../utils/UserMessage";
import { MsToHMSsssString } from "../../utils/Utils";
import LoaderDisplay from "../generic/LoaderDisplay/LoaderDisplay";

let NUM_GET_PICTURE_RETRIES = 5;

export default class EventThumbnail extends React.Component {
  state = {
    pictureLoadState: "loading",
    targetObjectMetadata: undefined,
  };

  _updateThumbnail = (props) => {
    this._getPictureTryCount = NUM_GET_PICTURE_RETRIES;

    this.setState({
      pictureLoadState: "loading",
      targetObjectMetadata: undefined,
    });

    if (
      !props.data ||
      !props.sensorConfig ||
      !props.endpoints ||
      !props.endpoints.framesUrl ||
      !props.endpoints.mediaServerBaseUrl
    ) {
      console.error(
        `${this.constructor.name}: No input event data OR sensor config OR required endpoint urls`
      );
      this.setState({ pictureLoadState: "erred" });
      return;
    }

    this.abortControllerFrameMetadata = new AbortController();

    let params;

    if (props.data.timestamp === props.data.end) {
      params = { timestamp: props.data.timestamp };
    } else {
      params = {
        fromTimestamp: props.data.timestamp,
        toTimestamp: props.data.end,
      };
    }
    params["sensorId"] = props.sensorConfig.id;

    fetch(joinUrl(props.endpoints.framesUrl, params), {
      signal: this.abortControllerFrameMetadata?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        const clonedResponse = { data };
        let eventTargetObjectId = null;
        if (props.data.objectId) {
          eventTargetObjectId = props.data.objectId;
        } else if (props.data.info && props.data.info.primaryObjectId) {
          eventTargetObjectId = props.data.info.primaryObjectId;
        } else {
          eventTargetObjectId = props.data.object.id;
        }
        let hasTargetObjectIdMetadata = false;

        if (clonedResponse.data.frames.length === 0) {
          console.error(
            this.constructor.name +
              ": " +
              "No event frames data found for sensor: " +
              props.sensorConfig.id
          );
          this.setState({ pictureLoadState: "erred" });
          return;
        }

        // clonedResponse.data.frames.forEach((frameMetadata) => {
        //   frameMetadata.objects.forEach((objectMetadata) => {
        //     if (
        //       !hasTargetObjectIdMetadata &&
        //       objectMetadata.id.toString() === eventTargetObjectId.toString()
        //     ) {
        //       hasTargetObjectIdMetadata = true;

        //       this.setState({ targetObjectMetadata: objectMetadata });
        //       this._getImageAndData(props, frameMetadata);
        //     }
        //   });
        // });

        let frameList = clonedResponse.data.frames; 
        const frameListWithTargetObject = clonedResponse.data.frames.filter(frameMetadata => {
          let check = false;
          frameMetadata.objects.find(objectMetadata => {
            if (objectMetadata.id.toString() === eventTargetObjectId.toString()) {
              check = true;
              return true;
            }
          })
          return check;
        })
        
        if (frameListWithTargetObject.length > 0) {
          hasTargetObjectIdMetadata = true;
          const middleIndex = Math.floor(frameListWithTargetObject.length / 2);
          const middleFrame = frameListWithTargetObject[middleIndex];
          const targetObject = middleFrame.objects.find(obj => 
            obj.id.toString() === eventTargetObjectId.toString()
          );

          this.setState({ targetObjectMetadata: targetObject });
          this._getImageAndData(props, middleFrame);
        } else {
          if (props.isRequiredThumbnailImage) {
            hasTargetObjectIdMetadata = true;
            this.setState({ targetObjectMetadata: frameList[Math.floor(frameList.length / 2)].objects[0] });
            this._getImageAndData(props, frameList[Math.floor(frameList.length / 2)]);
          }
        }

        if (!hasTargetObjectIdMetadata) {
          console.error(
            this.constructor.name +
              ": Skipping thumbnail display: " +
              "Unable to draw BBox: No event frame with target object (object id: " +
              eventTargetObjectId +
              ") found for sensor: " +
              props.sensorConfig.id
          );
          this.setState({ pictureLoadState: "erred" });
        }
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) return;
        this.setState({ pictureLoadState: "erred" });
        if (!thrown.config) throw thrown;
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          this.constructor.name + ": Fetch frames metadata for event",
          thrown
        );
        console.error(userMessage.getLongString());
      });
  };

  _getImage = (
    props,
    mediaServiceBaseUrl,
    mediaStreamerSensorId,
    frameMetadata
  ) => {
    // Compute time string for picture api call
    let fps, source, timeString;
    props.sensorConfig.attributes &&
      props.sensorConfig.attributes.forEach((pair) => {
        if (pair.name === "fps") fps = parseInt(pair.value);
        if (pair.name === "source") source = pair.value;
      });

    if (!fps) {
      throw Error("No fps in metadata for sensor " + props.sensorConfig.id);
    }

    if (source === "nvstreamer") {
      let nvStreamerCameraPictureTimeMs = Math.floor(
        (frameMetadata.id / fps) * 1000
      );
      timeString = MsToHMSsssString(nvStreamerCameraPictureTimeMs);
    } else if (source === "vst" || source === "mms") {
      timeString = frameMetadata.timestamp;
    } else {
      throw Error(
        "Unknown video source - " +
          source +
          " - for sensor - " +
          props.sensorConfig.id
      );
    }

    this.abortControllerCameraPicture = new AbortController();

    let getCallPurposeStr =
      this.constructor.name +
      ":  " +
      "Fetch Image for deviceId: " +
      mediaStreamerSensorId +
      " (sensor: " +
      props.sensorConfig.id +
      ")" +
      " at time: " +
      timeString;

    fetch(joinUrl(
      `${props.endpoints.mediaServerBaseUrl}/replay/stream/${mediaStreamerSensorId}/picture`,
      { startTime: timeString },
    ), {
      headers: {
        streamId: mediaStreamerSensorId,
      },
      signal: this.abortControllerCameraPicture?.signal,
    })
      .then((response) => readBlob(response))
      .then(async (blob) => {
        this.pictureUrl = window.URL.createObjectURL(blob);
        let htmlImage = new window.Image();
        this._htmlImageOnLoadAbortController = new AbortController();
        htmlImage.addEventListener(
          "load",
          (e) => {
            this.picture = e.target;
            this.setState({
              pictureLoadState: "loaded",
            });
            window.URL.revokeObjectURL(this.pictureUrl);
          },
          { signal: this._htmlImageOnLoadAbortController.signal }
        );
        htmlImage.src = this.pictureUrl;
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          thrown.message &&
            console.warn(getCallPurposeStr + " : " + thrown.message);
          return;
        }

        if (!thrown.config) {
          this.setState({ pictureLoadState: "erred" });
          return;
        }

        const retry = (userMessage) => {
          if (this._getPictureTryCount-- > 0) {
            console.warn(userMessage.getLongString());
            this._retryGetPictureTimeoutId = setTimeout(() => {
              console.log(getCallPurposeStr + "  RETRYING...");
              this._getImage(
                props,
                mediaServiceBaseUrl,
                mediaStreamerSensorId,
                frameMetadata
              );
            }, 400);
          } else {
            this.setState({ pictureLoadState: "erred" });
            console.error(userMessage.getLongString());
          }
        };

        if (thrown.response) {
          try {
            const reader = new FileReader();
            reader.onload = () => {
              try {
                let errorMessage;
                try {
                  const jsonData = JSON.parse(reader.result);
                  errorMessage =
                    jsonData.error != null
                      ? jsonData.error
                      : jsonData.error_message != null
                        ? jsonData.error_message
                        : reader.result;
                } catch {
                  errorMessage =
                    typeof reader.result === "string"
                      ? reader.result
                      : "Unknown error from server";
                }
                const userMessage = new UserMessage(
                  UserMessage.SRC_TYPE.GENERIC,
                  UserMessage.MESSAGE_LEVEL.ERROR,
                  getCallPurposeStr,
                  errorMessage
                );
                retry(userMessage);
              } catch (e) {
                console.warn(getCallPurposeStr, e);
                retry(
                  new UserMessage(
                    UserMessage.SRC_TYPE.GENERIC,
                    UserMessage.MESSAGE_LEVEL.ERROR,
                    getCallPurposeStr,
                    "Failed to load thumbnail"
                  )
                );
              }
            };
            reader.onerror = () => {
              retry(
                new UserMessage(
                  UserMessage.SRC_TYPE.GENERIC,
                  UserMessage.MESSAGE_LEVEL.ERROR,
                  getCallPurposeStr,
                  "Unable to read error (as blob) from server"
                )
              );
            };
            reader.readAsText(thrown.response.data);
          } catch (e) {
            console.warn(getCallPurposeStr, e);
            retry(
              new UserMessage(
                UserMessage.SRC_TYPE.GENERIC,
                UserMessage.MESSAGE_LEVEL.ERROR,
                getCallPurposeStr,
                "Failed to load thumbnail"
              )
            );
          }
        } else {
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.HTTP_ERROR,
            UserMessage.MESSAGE_LEVEL.ERROR,
            getCallPurposeStr,
            thrown
          );
          retry(userMessage);
        }
      });
  };

  _getImageAndData = (props, frameMetadata) => {
    this.abortControllerMediaServiceDeviceListQuery = new AbortController();

    let mediaServiceBaseUrl = props.endpoints.mediaServerBaseUrl;

    let getMediaServiceDeviceListCallPurpose =
      this.constructor.name +
      ": " +
      "Get MediaService deviceId for sensor " +
      props.sensorConfig.id;

    fetch(`${props.endpoints.mediaServerBaseUrl}/sensor/list`, {
      signal: this.abortControllerMediaServiceDeviceListQuery?.signal,
    })
      .then((response) => readJson(response))
      .then((deviceList) => {
        let mediaStreamerSensorId;

        if (!deviceList) {
          throw Error("No data sent from the server");
        }
        deviceList.forEach((deviceData) => {
          let sensorId = props.sensorConfig.id;
          if (props.isRemoveDashCV && sensorId.endsWith("-cv")) {
            sensorId = sensorId.replace("-cv", "");
          }
          if (
            deviceData.name.split(".")[0] === sensorId &&
            deviceData.state === "online"
          ) {
            mediaStreamerSensorId = deviceData.sensorId;
          }
        });
        if (!mediaStreamerSensorId) {
          throw Error(
            "Unable to get online media service device ID for sensor " +
              props.sensorConfig.id
          );
        }

        this._getImage(
          props,
          mediaServiceBaseUrl,
          mediaStreamerSensorId,
          frameMetadata
        );
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          thrown.message &&
            console.warn(
              getMediaServiceDeviceListCallPurpose + " : " + thrown.message
            );
          return;
        }

        this.setState({ pictureLoadState: "erred" });

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

  _cancelGetImageAndData = (message) => {
    window.URL.revokeObjectURL(this.pictureUrl);
    this.abortControllerCameraPicture &&
      this.abortControllerCameraPicture.abort(message);
    this.abortControllerFrameMetadata &&
      this.abortControllerFrameMetadata.abort(message);
    this.abortControllerMediaServiceDeviceListQuery &&
      this.abortControllerMediaServiceDeviceListQuery.abort(message);
    this._htmlImageOnLoadAbortController &&
      this._htmlImageOnLoadAbortController.abort(message);
    clearTimeout(this._retryGetPictureTimeoutId);
  };

  componentDidMount() {
    if (this.props.isVisible) {
      this._updateThumbnail(this.props);
    }
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    // Cancel loading when card becomes hidden (only if still loading)
    if (this.props.isVisible && !nextProps.isVisible) {
      if (this.state.pictureLoadState === "loading") {
        this._cancelGetImageAndData("canceled: card scrolled out of view");
      }
      return;
    }

    // Start loading when card becomes visible (only if not already loaded)
    if (!this.props.isVisible && nextProps.isVisible) {
      if (this.state.pictureLoadState !== "loaded") {
        this._updateThumbnail(nextProps);
      }
      return;
    }

    // Only update if already visible and data changed
    if (
      nextProps.isVisible &&
      !(
        isEqual(this.props.data, nextProps.data) &&
        isEqual(this.props.sensorConfig, nextProps.sensorConfig) &&
        isEqual(this.props.endpoints, nextProps.endpoints)
      )
    ) {
      this._cancelGetImageAndData("canceled: thumbnail to be updated");
      this._updateThumbnail(nextProps);
    }
  }

  render() {
    // loading
    let divContent = (
      <LoaderDisplay
        type={LoaderDisplay.TYPE.LOADING}
        size={LoaderDisplay.SIZE.SMALL}
      />
    );

    if (this.state.pictureLoadState === "loaded") {
      let canvasBboxEl;

      if (
        this.state.targetObjectMetadata &&
        this.viewRef &&
        this.viewRef.image2CanvasCoordinateConverter &&
        this.viewRef.scaleFactor
      ) {
        let convertedBboxes = [];
        let data = this.state.targetObjectMetadata;

        let bboxPoints = [];
        // fill in all corners coordinate pairs
        bboxPoints.push({
          x: data.bbox.leftX,
          y: data.bbox.topY,
        });
        bboxPoints.push({
          x: data.bbox.rightX,
          y: data.bbox.topY,
        });
        bboxPoints.push({
          x: data.bbox.rightX,
          y: data.bbox.bottomY,
        });
        bboxPoints.push({
          x: data.bbox.leftX,
          y: data.bbox.bottomY,
        });

        // convert coordinates to match canvas on UI
        bboxPoints.forEach((point) => {
          convertedBboxes.push(
            this.viewRef.image2CanvasCoordinateConverter.convert(point)
          );
        });
        convertedBboxes.push(convertedBboxes[0]);
        let linePoints = [];
        convertedBboxes.forEach((point) => {
          linePoints.push(point.x);
          linePoints.push(point.y);
        });

        // TODO: remove draw logic once https://jirasw.nvidia.com/browse/VST-227 is done
        canvasBboxEl = (
          <Line
            points={linePoints}
            stroke="#fff"
            strokeWidth={15 * this.viewRef.scaleFactor}
            shadowColor="#000"
            shadowBlur={15 * this.viewRef.scaleFactor}
          />
        );
      }

      divContent = (
        <CameraImageView
          ref={(ref) => (this.viewRef = ref)}
          viewImage={this.picture}
          onResize={() => this.forceUpdate()}
        >
          {canvasBboxEl}
        </CameraImageView>
      );
    } else if (this.state.pictureLoadState === "erred") {
      divContent = (
        <React.Fragment>
          <HideImageIcon />
          {"Image Unavailable"}
        </React.Fragment>
      );
    }

    return <div className={Styles.mainContainer}>{divContent}</div>;
  }

  componentWillUnmount() {
    this._cancelGetImageAndData();
  }
}
