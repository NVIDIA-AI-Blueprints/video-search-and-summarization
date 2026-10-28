// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Axios } from "common/warehouse";
import React from "react";

import TopView from "./TopView";
import UserMessage from "../../utils/UserMessage";
import LoaderDisplay from "../generic/LoaderDisplay/LoaderDisplay";
import { MdxPlaceArray2String, DeriveMdxSensorRegion } from "../../utils/Utils";

export default class CameraView extends React.PureComponent {
  state = {
    // loading -> undefined, error -> null
    cameraViewImage: undefined,
  };

  getImageMetadata = async (cameraConfig) => {
    // Get the region-level place string (not the full sensor place)
    const [absoluteRegionName] = DeriveMdxSensorRegion(cameraConfig);
    const placeString = absoluteRegionName || MdxPlaceArray2String(cameraConfig.place);

    // First try to get metadata by place
    const getMetadataByPlace = async () => {
      let getCallPurposeStr = `Fetch Image Metadata for place: ${placeString}`;
      let params = new URLSearchParams();
      params.append("place", placeString);

      this.axiosCancelTokenSourceImageMetadataByPlace = Axios.CancelToken.source();

      try {
        const res = await Axios.get(this.props.endpointGetCameraImagesMetadata, {
          params: params,
          cancelToken: this.axiosCancelTokenSourceImageMetadataByPlace.token,
        });
        const metadata = res.data;
        // Check if plan-view exists in imageMetadata array
        const planViewExists = metadata.imageMetadata?.some(
          (item) => item.view === "plan-view"
        );
        if (planViewExists) {
          return "plan-view";
        }
        return null; // plan-view not found, try sensorId
      } catch (thrown) {
        if (Axios.isCancel(thrown)) {
          throw thrown;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.GENERIC,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurposeStr,
          thrown.response?.data?.error || thrown.response?.data?.error_message || thrown.message
        );
        console.error(userMessage.getLongString());
        return null; // Failed to get metadata by place, try sensorId
      }
    };

    // Then try to get metadata by sensorId
    const getMetadataBySensorId = async () => {
      let getCallPurposeStr = `Fetch Image Metadata for camera: ${cameraConfig.id}`;
      let params = new URLSearchParams();
      params.append("sensorId", cameraConfig.id);

      this.axiosCancelTokenSourceImageMetadata = Axios.CancelToken.source();

      try {
        const res = await Axios.get(this.props.endpointGetCameraImagesMetadata, {
          params: params,
          cancelToken: this.axiosCancelTokenSourceImageMetadata.token,
        });
        const metadata = res.data;
        // Check if warped-camera-view exists in imageMetadata array
        const warpedCameraViewExists = metadata.imageMetadata?.some(
          (item) => item.view === "warped-camera-view"
        );
        if (warpedCameraViewExists) {
          return "warped-camera-view";
        }
        return null; // warped-camera-view not found
      } catch (thrown) {
        if (Axios.isCancel(thrown)) {
          throw thrown;
        }
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.GENERIC,
          UserMessage.MESSAGE_LEVEL.ERROR,
          getCallPurposeStr,
          thrown.response?.data?.error || thrown.response?.data?.error_message || thrown.message
        );
        console.error(userMessage.getLongString());
        return null; // Failed to get metadata by sensorId
      }
    };

    // First try by place
    const viewTypeByPlace = await getMetadataByPlace();
    if (viewTypeByPlace) {
      return { viewType: viewTypeByPlace, placeString };
    }

    // plan-view not found, try sensorId
    const viewTypeBySensorId = await getMetadataBySensorId();
    if (viewTypeBySensorId) {
      return { viewType: viewTypeBySensorId, sensorId: cameraConfig.id };
    }

    // Neither plan-view nor warped-camera-view found, show error
    let userMessage = new UserMessage(
      UserMessage.SRC_TYPE.GENERIC,
      UserMessage.MESSAGE_LEVEL.ERROR,
      `Fetch Image Metadata for camera: ${cameraConfig.id}`,
      "No valid view type found (neither plan-view nor warped-camera-view)"
    );
    console.error(userMessage.getLongString());
    throw new Error("No valid view type found");
  };

  getCameraViewImage = async (cameraConfig) => {
    this.setState({ cameraViewImage: undefined });
    this.props.onCalibrationViewTypeResolved?.(null);

    // First get the metadata to determine the view type
    let metadataResult;
    try {
      metadataResult = await this.getImageMetadata(cameraConfig);
    } catch (error) {
      // If metadata fetch is cancelled or fails, stop here
      if (Axios.isCancel(error)) return;
      this.setState({ cameraViewImage: null });
      return;
    }

    const { viewType, placeString, sensorId } = metadataResult;
    this.props.onCalibrationViewTypeResolved?.(viewType);
    let getCallPurposeStr = `Fetch Image for camera: ${cameraConfig.id}`;
    let params = new URLSearchParams();
    
    // Use place param for plan-view, sensorId param for warped-camera-view
    if (viewType === "plan-view") {
      params.append("place", placeString);
    } else {
      params.append("sensorId", sensorId);
    }
    params.append("view", viewType);

    this.axiosCancelTokenSourceCameraViewImage = Axios.CancelToken.source();

    Axios.get(this.props.endpointGetCameraViewImage, {
      responseType: "blob",
      params: params,
      cancelToken: this.axiosCancelTokenSourceCameraViewImage.token,
    })
      .then(async (res) => {
        this.pictureUrl = window.URL.createObjectURL(res.data);
        let htmlImage = new window.Image();
        this._htmlImageOnLoadAbortController = new AbortController();
        htmlImage.addEventListener(
          "load",
          (e) => {
            this.setState({ cameraViewImage: e.target });
            window.URL.revokeObjectURL(this.pictureUrl);
          },
          { signal: this._htmlImageOnLoadAbortController.signal }
        );
        htmlImage.src = this.pictureUrl;
      })
      .catch((thrown) => {
        this.setState({ cameraViewImage: null });

        if (Axios.isCancel(thrown)) return;
        if (!thrown.config) throw thrown;

        const reader = new FileReader();
        reader.onload = () => {
          const jsonData = JSON.parse(reader.result);
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.GENERIC,
            UserMessage.MESSAGE_LEVEL.ERROR,
            getCallPurposeStr,
            jsonData.error ? jsonData.error : jsonData.error_message
          );
          console.error(userMessage.getLongString());
        };
        reader.onerror = (error) => {
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.GENERIC,
            UserMessage.MESSAGE_LEVEL.ERROR,
            getCallPurposeStr,
            "Unable to read error (as blob) from server"
          );
          console.error(userMessage.getLongString());
        };
        reader.readAsText(thrown.response.data);
      });
  };

  UNSAFE_componentWillMount() {
    // Get background image from the backend for the sensor
    // Cancel token for axios request for Image for a sensor
    this.getCameraViewImage(this.props.cameraConfig);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (this.props.cameraConfig !== nextProps.cameraConfig) {
      this.getCameraViewImage(nextProps.cameraConfig);
    }
  }

  render() {
    return (
      <React.Fragment>
        {this.state.cameraViewImage === undefined && (
          <LoaderDisplay
            type={LoaderDisplay.TYPE.LOADING}
            size={LoaderDisplay.SIZE.SMALL}
            userMessage="Loading Camera Top View Image for the place"
          />
        )}
        {this.state.cameraViewImage === null && (
          <LoaderDisplay
            type={LoaderDisplay.TYPE.LOAD_FAILURE}
            size={LoaderDisplay.SIZE.SMALL}
            userMessage="Failed to load Top View Image for the place"
          />
        )}
        {this.state.cameraViewImage !== undefined &&
          this.state.cameraViewImage !== null && (
            <TopView
              ref={(ref) => (this.topViewRef = ref)}
              topViewImage={this.state.cameraViewImage}
              onResize={this.props.onResize}
              onRefreshCoordinateConverters={
                this.props.onRefreshCoordinateConverters
              }
              overlayLayerNames={this.props.overlayLayerNames}
              trajectoryLayers={this.props.trajectoryLayers}
            >
              {this.props.children}
            </TopView>
          )}
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    window.URL.revokeObjectURL(this.pictureUrl);
    this.axiosCancelTokenSourceImageMetadataByPlace &&
      this.axiosCancelTokenSourceImageMetadataByPlace.cancel();
    this.axiosCancelTokenSourceImageMetadata &&
      this.axiosCancelTokenSourceImageMetadata.cancel();
    this.axiosCancelTokenSourceCameraViewImage &&
      this.axiosCancelTokenSourceCameraViewImage.cancel();
    this._htmlImageOnLoadAbortController &&
      this._htmlImageOnLoadAbortController.abort();
  }
}

/*
 * Prop types
 *
 * endpointGetCameraImagesMetadata: REST Endpoint to get image metadata
 * endpointGetCameraViewImage: REST Endpoint to get camera view image
 * cameraConfig: Camera's config Json Object
 * onResize: Callback
 * onLoad: Callback
 * onRefreshCoordinateConverters: Callback
 * onCalibrationViewTypeResolved: Optional (viewType: string | null) =>
 *   "plan-view" | "warped-camera-view" when metadata resolves; null while loading or on failure/switch
 */
