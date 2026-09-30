/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */


import axios from "axios";
import { matrix } from "mathjs";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Loader } from "semantic-ui-react";
import { calValId, roiId, tripDirId, tripwireId } from "../common/utils";
import ValidationApp from "./ValidationApp";
import "./ValidationStyles.css";
import DOMPurify from "dompurify";


import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Loader to load all image and map data required for validating the sensor
 * homography matrix calculated in the calibration phase. A loader takes no
 * props, except the sensorId passed through the URL.
 */
class ValidationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      sensorId: null,
      hasHomography: false,
      homography: null,
      imageUrl: null,
      isLoaded: false,
      error: null,
      mapLabelData: {
        [calValId]: [],
        [roiId]: [],
        [tripwireId]: [],
        [tripDirId] : []
      },
      mapCenter: {},
      mapZoom: null,
      imageData: {
        imageLabels: [
          {
            type: "polyline",
            name: "validation",
            id: calValId,
            limit: false,
            draw: true
          },
          {
            type: "polygon",
            name: "ROI",
            id: roiId,
            limit: false,
            draw: false
          }
        ],
        imageLabelData: {
          [calValId]: [],
          [roiId]: [],
          [tripwireId]: [],
          [tripDirId]: []
        }
      },
      projectId: null
    };

    this.handleValidated = this.handleValidated.bind(this);
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
  }

  /**
   * Run the loadPage function on component mount.
   */
  async componentDidMount() {
    this.loadPage();
  }

  /**
   * Load the sensor data required for validating the homography matrix
   * calculated during the calibration phase.
   */
  async loadPage() {
    const { sensorId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const sensor = res.data;
        const { project, sensorId, mapAPIKey } = sensor;
        const roiPolygon = JSON.parse(sensor.roiPolygon);
        const tripwireLines = JSON.parse(sensor.tripwireLines);
        const tripDirLines = JSON.parse(sensor.tripDirLines);

        const mapLabelData = {
          [calValId]: [],
          [roiId]: roiPolygon ,
          [tripwireId]: tripwireLines,
          [tripDirId]: tripDirLines
        };
        const { imageUrl, homography } = sensor;
        let hasHomography;
        if (homography === "") {
          hasHomography = false;
        } else {
          hasHomography = true;
        }
        let drawLabels = [];
        drawLabels.push(
            {
              type: "polygon",
              name: "Calibration",
              id: calValId,
              limit: true,
              draw: true,
              height: sensor.height
            },
            {
              type: "polygon",
              name: "ROI",
              id: roiId,
              limit: false,
              draw: true,
              height: sensor.height
            }
          );
        let mapCenter;
        if (sensor.mapCenter) {
          mapCenter = JSON.parse(sensor.mapCenter);
        } else {
          mapCenter = { lat: sensor.originLat, lng: sensor.originLng };
        }
        const { mapZoom } = sensor;
        this.setState({
          mapAPIKey,
          sensorId,
          mapLabelData,
          hasHomography,
          homography,
          imageUrl: getMediaUrl(imageUrl),
          isLoaded: true,
          mapCenter,
          mapZoom,
          // cityId: city,
          projectId: project,
          drawLabels: drawLabels
        });
      })
      .catch(error => {
        this.setState({
          isLoaded: true,
          error
        });
      });
  }

  /**
   * Push updates to the backend.
   * @param {object} labelData Contains information to be sent to the backend:
   */
  async pushUpdate(labelData) {
    const { sensorId } = this.props.match.params;
    const roiPolygon = JSON.stringify(labelData.mapLabelData[roiId]);
    const tripwireLines = JSON.stringify(labelData.mapLabelData[tripwireId]);
    const tripDirLines = JSON.stringify(labelData.mapLabelData[tripDirId]);

    const isValidated = !!labelData.isValidated;

    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        roiPolygon,
        tripwireLines,
        tripDirLines,
        isValidated
      })
      .then()
      .catch(error => console.error("err", error));
  }

  /**
   * Handle click of validate calibration. Sets the isValidated attrubte in the
   * backend as true.
   */
  async handleValidated() {
    const { sensorId } = this.props.match.params;
    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        isValidated: true
      })
      .then()
      .catch(error => console.error(error));
    this.props.alert.success("Sensor Validated");
  }

  /**
   * Go back to the City page under the Sensors tab.
   */
  goBack = () => {
    const {  projectId } = this.state;
    let path = `/projects/geo/calibrateSensors/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Sensors" }
    });
  };

  render() {
    const cameraId  = DOMPurify.sanitize(this.props.match.params.sensorId);
    const { imageUrl, homography } = this.state;
    const {
      mapAPIKey,
      sensorId,
      hasHomography,
      mapLabelData,
      mapCenter,
      imageData,
      error,
      isLoaded,
      mapZoom,
      drawLabels
    } = this.state;

    if (error) {
      return (
        <div data-testid="ValidCalibLoaderError">Error: {error.message}</div>
      );
    } else if (!isLoaded) {
      return (
        <Loader data-testid="ValidLoaderLoading" active inline="centered" />
      );
    } else if (!imageUrl) {
      return (
        <div>
          <h2>Error: Please upload image for validation.</h2>
        </div>
      );
    } else if (!hasHomography) {
      return (
        <div>
          <h2>Error: Can't validate because sensor is not yet calibrated.</h2>
        </div>
      );
    }

    return (
      <ValidationApp
        sensorId={sensorId}
        id={cameraId}
        type="sensors"
        drawLabels={drawLabels}
        labels={imageData.imageLabels}
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabels={mapLabelData}
        imageUrl={imageUrl}
        labelData={imageData.imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        homography={matrix(JSON.parse(homography))}
        handleValidated={this.handleValidated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
      />
    );
  }
}

export default withAlert()(ValidationLoader);
