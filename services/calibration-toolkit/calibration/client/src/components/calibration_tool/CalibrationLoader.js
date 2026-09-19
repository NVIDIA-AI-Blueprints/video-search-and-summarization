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


import React, { Component } from "react";
import axios from "axios";
import { Loader } from "semantic-ui-react";

import CalibrationApp from "./CalibrationApp";
import { calibId, tripwireId, tripDirId, roiId } from "../common/utils";

import "./CalibrationStyles.css";
import DOMPurify from "dompurify";
import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";
/**
 * Loader to load all map and image data required for calibrating a sensor. A
 * loader takes no props, except for the sensorId passed through the URL.
 */
export default class CalibrationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      sensor: null,
      isCalibrated: false,
      isLoaded: false,
      error: null,
      imageUrl: null,
      loadingHomography: false,
      homography: null,
      mapLabelData: {
        [calibId]: [],
        [roiId]: [],
        [tripwireId]: [],
        [tripDirId]: [],
      },
      mapCenter: {},
      mapZoom: null,
      labels: [
        {
          type: "polygon",
          name: "calibration",
          id: calibId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: roiId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: tripwireId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: tripDirId,
          limit: false,
          draw: false
        }
      ],
      drawLabels: [
        {
          type: "polygon",
          name: "calibration",
          id: calibId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: roiId,
          limit: false,
          draw: true
        }
      ],
      imageLabelData: {
        [calibId]: [],
        [roiId]: [],
        [tripwireId]: [],
        [tripDirId]: [],
      },
      cityId: null,
      projectId: null
    };
    this.loadPage = this.loadPage.bind(this);
    this.pushUpdate = this.pushUpdate.bind(this);
    this.requestHomography = this.requestHomography.bind(this);
    this.waitForHomography = this.waitForHomography.bind(this);
    this.loadHomography = this.loadHomography.bind(this);
    this.goBack = this.goBack.bind(this);
    this.getMapAPI = this.getMapAPI.bind(this)
  }

  /**
   * Run loadPage function when the component mounts.
   */
  componentDidMount() {
    this.loadPage();
  }


  /**
   * Get Map API
   */
  async getMapAPI(projectId){
    // console.log("sensor app map", projectId)
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const project = res.data
        const mapAPIKey = project.mapAPIKey
        // console.log("sensor app api", mapAPIKey)
        this.setState({
          mapAPIKey,
        });
        // return mapAPIKey
      })

  }

  /**
   * Load sensor data from the backend.
   */
  async loadPage() {
    const { sensorId } = this.props.match.params;

    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const sensor = res.data;
        const { city, project, isCalibrated, mapAPIKey } = sensor;
        // this.getMapAPI(project)
        const sensorPolygon = JSON.parse(sensor.sensorPolygon);
        const gisPolygon = JSON.parse(sensor.gisPolygon);
        const roiPolygon = JSON.parse(sensor.roiPolygon);
        const tripwireLines = JSON.parse(sensor.tripwireLines);
        const tripDirLines = JSON.parse(sensor.tripDirLines);
        // console.log("sensor app", project, sensor)
        let newSensorData = {
          [calibId]: sensorPolygon,
          [roiId]: [] ,
          [tripwireId]: [],
          [tripDirId]: [],
        };
        const newMapData = {
          [calibId]: gisPolygon,
          [roiId]: roiPolygon,
          [tripwireId]: tripwireLines,
          [tripDirId]: tripDirLines
        };
        let drawLabels = [];
        drawLabels.push(
            {
              type: "polygon",
              name: "Calibration",
              id: calibId,
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
        let { homography } = sensor;
        if (homography === "") {
          homography = null;
        }

        this.setState({
          mapAPIKey,
          sensor,
          isCalibrated,
          isLoaded: true,
          mapLabelData: newMapData,
          imageLabelData: newSensorData,
          imageUrl: getMediaUrl(sensor.imageUrl),
          mapCenter,
          homography,
          mapZoom,
          cityId: city,
          projectId: project,
          drawLabels: drawLabels,
          calibrationType: sensor.calibrationType
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
    const sensorPolygon = JSON.stringify(labelData.imageLabelData[calibId]);
    const gisPolygon = JSON.stringify(labelData.mapLabelData[calibId]);
    const roiPolygon = JSON.stringify(labelData.mapLabelData[roiId]);
    const tripwireLines = JSON.stringify(labelData.mapLabelData[tripwireId]);
    const tripDirLines = JSON.stringify(labelData.mapLabelData[tripDirId]);
    const isCalibrated = !!labelData.isCalibrated;
    const isValidated = !!labelData.isValidated;

    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        sensorPolygon,
        gisPolygon,
        roiPolygon,
        tripwireLines,
        tripDirLines,
        isCalibrated,
        isValidated
      })
      .then()
      .catch(error => console.error("err", error));
  }

  /**
   * Wrapping the requestHomography and loadHomography functions to ensure
   * sequential calling.
   */
  async waitForHomography() {
    this.setState({ loadingHomography: true });
    await this.requestHomography();
    await this.loadHomography();
    this.setState({ loadingHomography: false });
  }

  /**
   * Load the homography from the backend
   */
  async loadHomography() {
    const { sensorId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const { homography } = res.data;
        this.setState({ homography });
      })
      .catch(error => console.error(error));
  }

  /**
   * Request the calculation of the homography to be done in the backend.
   */
  async requestHomography() {
    const { sensorId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/homography/${sensorId}/`)
      .then()
      .catch(error => console.error(error));
  }

  /**
   * Go back to the Sensors tab in the city page.
   */
  goBack = () => {
    const { cityId, projectId } = this.state;
    let path = `/projects/geo/calibrateSensors/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Sensors" }
    });
  };

  render() {
    const { sensorId } = DOMPurify.sanitize(this.props.match.params);
    const {
      projectId,
      mapAPIKey,
      sensor,
      mapLabelData,
      mapCenter,
      labels,
      imageLabelData,
      error,
      isLoaded,
      imageUrl,
      homography,
      loadingHomography,
      mapZoom,
      drawLabels,
      isCalibrated
    } = this.state;

    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    } else if (!imageUrl) {
      return (
        <div>
          <h2>Error: Please upload image for calibration.</h2>
        </div>
      );
    }

    return (
      <CalibrationApp
        data-testid="CalibLoaderApp"
        sensorId={sensor.sensorId}
        id={sensor.id}
        type={"sensors"}
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabels={mapLabelData}
        imageUrl={imageUrl}
        drawLabels={drawLabels}
        labels={labels}
        labelData={imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        waitForHomography={this.waitForHomography}
        loadingHomography={loadingHomography}
        homography={homography}
        isCalibrated={isCalibrated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        calibrationType={sensor.calibrationType}
      />
    );
  }
}
