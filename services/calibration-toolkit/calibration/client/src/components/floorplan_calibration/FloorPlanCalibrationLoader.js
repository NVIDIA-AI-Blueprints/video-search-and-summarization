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
import React, { Component } from "react";
import { Loader } from "semantic-ui-react";
import {
  addPointY, flipPointY
} from "../common/mathUtils";
import { floorPlanCalibId, floorPlanCalibMapId, roiId, tripDirId, tripwireId } from "../common/utils";
import FloorPlanCalibrationApp from "./FloorPlanCalibrationApp";
import "./FloorPlanStyles.css";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";
/**
 * Loader to load all map and image data required for calibrating a sensor. A
 * loader takes no props, except for the sensorId passed through the URL.
 */
export default class FloorPlanCalibrationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      sensor: null,
      isCalibrated: false,
      isLoaded: false,
      error: null,
      imageUrl: null,
      floorPlanImageUrl: null,
      loadingHomography: false,
      homography: null,
      mapLabelData: {
        // [floorPlanCalibId]: [],
        // [roiId]: [],
        // [tripwireId]: [],
        // [tripDirId]: [],
        imagesLabels: [
          {
            type: "polygon",
            name: "Calibration",
            id: floorPlanCalibMapId,
            limit: true,
            draw: true
          },
          {
            type: "polygon",
            name: "ROI",
            id: roiId,
            limit: false,
            draw: true
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: tripwireId,
            limit: false,
            draw: true
          },
          {
            type: "polyline",
            name: "Direction",
            id: tripDirId,
            limit: false,
            draw: true
          }

        ],
        imageLabelData: {
          [floorPlanCalibMapId]: [],
          [roiId]: [],
          [tripwireId]: [],
          [tripDirId]: []
        }
      },
      mapCenter: {},
      mapZoom: null,
      labels: [
        {
          type: "polygon",
          name: "Calibration",
          id: floorPlanCalibId,
          limit: true,
          draw: true
        }
        // ,
        // {
        //   type: "polygon",
        //   name: "Calibration",
        //   id: floorPlanCalibMapId,
        //   limit: false,
        //   draw: false
        // },
        // {
        //   type: "polygon",
        //   name: "ROI",
        //   id: roiId,
        //   limit: false,
        //   draw: false
        // },
        // {
        //   type: "polyline",
        //   name: "Tripwire",
        //   id: tripwireId,
        //   limit: false,
        //   draw: false
        // },
        // {
        //   type: "polyline",
        //   name: "Direction",
        //   id: tripDirId,
        //   limit: false,
        //   draw: false
        // }

      ],
      drawLabels:[
        {
          type: "polygon",
          name: "Calibration",
          id: floorPlanCalibId,
          limit: true
        },
        {
          type: "polygon",
          name: "Calibration",
          id: floorPlanCalibMapId,
          limit: false
        },
        {
          type: "polygon",
          name: "ROI",
          id: roiId,
          limit: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: tripwireId,
          limit: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: tripDirId,
          limit: false
        }
      ],
      mapLabels: [

        {
          type: "polygon",
          name: "Calibration",
          id: floorPlanCalibMapId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: roiId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: tripwireId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Direction",
          id: tripDirId,
          limit: false,
          draw: true
        }

      ],
      imageLabelData: {
        // [floorPlanCalibId]: [],
        [floorPlanCalibMapId]: [],
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
    this.fixHeight = this.fixHeight.bind(this)
    this.fixPolygon = this.fixPolygon.bind(this)

    this.goBack = this.goBack.bind(this);
  }

  /**
   * Run loadPage function when the component mounts.
   */
  componentDidMount() {
    this.loadPage();
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
        const { city, project, isCalibrated, mapAPIKey, floorPlanImHeight } = sensor;
        console.log ("fix p", sensor.polygon)
        const sensorPolygon = JSON.parse(sensor.sensorPolygon);
        const gisPolygon = JSON.parse(sensor.gisPolygon);
        const roiPolygon = JSON.parse(sensor.roiPolygon);
        const tripwireLines = JSON.parse(sensor.tripwireLines);
        const tripDirLines = JSON.parse(sensor.tripDirLines);
        console.log ("fpcL", sensorPolygon, gisPolygon)
        let newSensorData = {
          [floorPlanCalibId]: sensorPolygon,
          [floorPlanCalibMapId]: gisPolygon,
          [roiId]: roiPolygon,
          [tripwireId]: tripwireLines,
          [tripDirId]: tripDirLines
        };
        const newMapData = {
          imagesLabels: [
            {
              type: "polygon",
              name: "Calibration",
              id: floorPlanCalibMapId,
              limit: true,
              draw: true,
              height: floorPlanImHeight
            },
            {
              type: "polygon",
              name: "ROI",
              id: roiId,
              limit: false,
              draw: true,
              height: floorPlanImHeight

            },
            {
              type: "polyline",
              name: "Tripwire",
              id: tripwireId,
              limit: false,
              draw: true,
              height: floorPlanImHeight

            },
            {
              type: "polyline",
              name: "Direction",
              id: tripDirId,
              limit: false,
              draw: true,
              height: floorPlanImHeight

            }

          ],
          imageLabelData: {
            [floorPlanCalibMapId]: gisPolygon,
            [roiId]: roiPolygon,
            [tripwireId]: tripwireLines,
            [tripDirId]: tripDirLines
          }
        };
        let drawLabels = [];
        drawLabels.push(
          {
            type: "polygon",
            name: "Calibration",
            id: floorPlanCalibId,
            limit: true,
            draw: true,
            height: sensor.height
          },
          {
            type: "polygon",
            name: "Floorplan Calibration",
            id: floorPlanCalibMapId,
            limit: false,
            draw: true,
            height: floorPlanImHeight
          },
          {
            type: "polygon",
            name: "ROI",
            id: roiId,
            limit: false,
            draw: true,
            height: floorPlanImHeight
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: tripwireId,
            limit: false,
            draw: true,
            height: floorPlanImHeight
          },
          {
            type: "polyline",
            name: "Direction",
            id: tripDirId,
            limit: false,
            draw: true,
            height: floorPlanImHeight
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
          floorPlanImageUrl: getMediaUrl(sensor.floorPlanImageUrl),
          mapCenter,
          homography,
          mapZoom,
          cityId: city,
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
   * get homography polygon
   * @param {json} polygon
   * @param {matrix} M
   * @returns {Array}
   */
   fixPolygon(polygon,fpImHeight,height){
      // console.log("height", this.state.height, this.state.invImHeight)
    // console.log("invertImHeight",this.props, this.state)

    // const height = (this.state.sensor.height)
    // const fpImHeight = (this.state.sensor.floorPlanImHeight)
    // console.log("fix p,", this.state, this.props, height, fpImHeight)

    // console.log("invadfjal;f", this.state.invImHeight,invertImXPad, invertImYPad)
    const polygonArray = []
    // const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure,index) => {
      // console.log ("fix p fig", figure, index)
      const newShapePoints = figure.points.map( point =>{
        // console.log ("fix p", point)
        // console.log("hpt",this.state.height)
        // console.log("ght",point)
        const flipOrigPoint = addPointY(point, (height-fpImHeight))
        // console.log ("fix p fop", flipOrigPoint)
        // const flippedPoint = flipPointY(flipOrigPoint, fpImHeight)
        // console.log ("fix p fp", flippedPoint)

        return flipOrigPoint;

      });
      // console.log ("polygon", figure.id)
      polygonArray.push({
        id: figure.id,
        points: newShapePoints,
        type: figure.type,
        color: figure.color
      })
    });

    return polygonArray
  }

 /**
   * get homography polygon
   * @param {json} polygon
   * @param {matrix} M
   * @returns {Array}
   */
  fixHeight(polygon,height){
    // console.log("height", this.state.height, this.state.invImHeight)
    // console.log("invertImHeight",this.props, this.state)

    // const height = (this.state.sensor.height)
    // const fpImHeight = (this.state.sensor.floorPlanImHeight)
    // console.log("fix p,", this.state, this.props, height, fpImHeight)

    // console.log("invadfjal;f", this.state.invImHeight,invertImXPad, invertImYPad)
    const polygonArray = []
    // const category = Object.keys(polygon);
    // const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure,index) => {
      // console.log ("fix p fig", figure)
      const newShapePoints = figure.points.map( point =>{
        // console.log ("fix p", point, height)
        // console.log("hpt",this.state.height)
        // console.log("ght",point)
        const flipOrigPoint = flipPointY(point, height)
        // console.log ("fix p fop", flipOrigPoint)
        // const flippedPoint = flipPointY(flipOrigPoint, fpImHeight)
        // console.log ("fix p fp", flippedPoint)

        return flipOrigPoint;

      });
      // console.log ("polygon", figure.id)
      polygonArray.push({
        id: figure.id,
        points: newShapePoints,
        type: figure.type,
        color: figure.color
      })
    });

    return polygonArray
  }

  /**
   * Push updates to the backend.
   * @param {object} labelData Contains information to be sent to the backend:
   */
  async pushUpdate(labelData) {
    console.log("output", labelData)
    const { sensorId } = this.props.match.params;
    console.log ( "output", this.state.sensor.height,this.state.sensor.floorPlanImHeight )
    const sensorPolygon = JSON.stringify((labelData.imageLabelData[floorPlanCalibId]));
    console.log("output", sensorPolygon)
    const gisPolygon = JSON.stringify((labelData.mapLabelData[floorPlanCalibMapId]));
    const roiPolygon = JSON.stringify((labelData.mapLabelData[roiId]  ));
    const tripwireLines = JSON.stringify((labelData.mapLabelData[tripwireId]));
    const tripDirLines = JSON.stringify((labelData.mapLabelData[tripDirId]));
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
    const {  projectId } = this.state;
    let path = `/projects/mtmc/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      // state: { prevPath: "Sensors" }
    });
  };

  render() {
    const { sensorId } = DOMPurify.sanitize(this.props.match.params);
    const {
      mapAPIKey,
      sensor,
      mapLabelData,
      mapCenter,
      labels,
      mapLabels,
      drawLabels,
      imageLabelData,
      error,
      isLoaded,
      imageUrl,
      floorPlanImageUrl,
      homography,
      loadingHomography,
      mapZoom,
      isCalibrated
    } = this.state;
    console.log("fpcl", drawLabels[1].height)
    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    } else if (!imageUrl || !floorPlanImageUrl) {
      return (
        <div>
          <h2>Error: Please upload image for calibration.</h2>
        </div>
      );
    }

    return (
      <FloorPlanCalibrationApp
        data-testid="CalibLoaderApp"
        sensorId={sensor.sensorId}
        id={sensorId}
        type={"sensors"}
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabelData={mapLabelData.imageLabelData}
        mapLabels={mapLabels}
        imageUrl={imageUrl}
        floorPlanImageUrl={floorPlanImageUrl}
        labels={labels}
        drawLabels = {drawLabels}
        labelData={imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        waitForHomography={this.waitForHomography}
        loadingHomography={loadingHomography}
        homography={homography}
        isCalibrated={isCalibrated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        fpHeight={drawLabels[1].height}
      />
    );
  }
}
