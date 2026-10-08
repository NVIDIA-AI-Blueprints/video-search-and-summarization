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
import { Loader } from "semantic-ui-react";
import {
  addPointY, flipPointY
} from "../common/mathUtils";
import { camPlacementId, camPlacementMapId } from "../common/utils";
import SetupFloorPlanApp from "./SetupFloorPlanApp";
import "./SetupFloorPlanStyles.css";

import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Loader to load all map and image data required for calibrating a sensor. A
 * loader takes no props, except for the sensorId passed through the URL.
 */
export default class SetupFloorPlanLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      projectId: null,
      isCalibrated: false,
      isLoaded: false,
      error: null,
      sensors: [],
      sensorMap: [],
      imageUrl: null,
      floorPlanImageUrl: null,
      loadingHomography: false,
      homography: null,
      coordinates:{},
      mapLabelData: {
        [camPlacementId]: [],
        // [roiId]: [],
        // [tripwireId]: [],
        // [tripDirId]: [],
        imagesLabels: [
          // {
          //   type: "polyline",
          //   name: "Calibration",
          //   id: camPlacementMapId,
          //   limit: true,
          //   draw: true
          // },
          // {
          //   type: "polygon",
          //   name: "ROI",
          //   id: roiId,
          //   limit: false,
          //   draw: true
          // },
          // {
          //   type: "polyline",
          //   name: "Tripwire",
          //   id: tripwireId,
          //   limit: false,
          //   draw: true
          // },
          // {
          //   type: "polyline",
          //   name: "Direction",
          //   id: tripDirId,
          //   limit: true,
          //   draw: true
          // }

        ],
        imageLabelData: {
          [camPlacementMapId]: [],
          // [roiId]: [],
          // [tripwireId]: [],
          // [tripDirId]: []
        }
      },
      mapCenter: {},
      mapZoom: null,
      labels: [
        // {
        //   type: "polyline",
        //   name: "Calibration",
        //   id: camPlacementId,
        //   limit: true,
        //   draw: true
        // }
        // ,
        // {
        //   type: "polygon",
        //   name: "Calibration",
        //   id: camPlacementMapId,
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
        // {
        //   type: "polyline",
        //   name: "Calibration",
        //   id: camPlacementId,
        //   limit: true
        // }
        // {
        //   type: "polygon",
        //   name: "Calibration",
        //   id: camPlacementMapId,
        //   limit: false
        // },
        // {
        //   type: "polygon",
        //   name: "ROI",
        //   id: roiId,
        //   limit: false
        // },
        // {
        //   type: "polyline",
        //   name: "Tripwire",
        //   id: tripwireId,
        //   limit: false
        // },
        // {
        //   type: "polyline",
        //   name: "Direction",
        //   id: tripDirId,
        //   limit: false
        // }
      ],
      mapLabels: [

        // {
        //   type: "polyline",
        //   name: "Calibration",
        //   id: camPlacementMapId,
        //   limit: true,
        //   draw: true
        // }
        // {
        //   type: "polygon",
        //   name: "ROI",
        //   id: roiId,
        //   limit: false,
        //   draw: true
        // },
        // {
        //   type: "polyline",
        //   name: "Tripwire",
        //   id: tripwireId,
        //   limit: false,
        //   draw: true
        // },
        // {
        //   type: "polyline",
        //   name: "Direction",
        //   id: tripDirId,
        //   limit: true,
        //   draw: true
        // }

      ],
      imageLabelData: {
        [camPlacementId]: [],
        // [camPlacementMapId]: [],
        // [roiId]: [],
        // [tripwireId]: [],
        // [tripDirId]: [],
      },
      scaleFactor: 1.0
    };
    this.loadPage = this.loadPage.bind(this);
    this.pushUpdate = this.pushUpdate.bind(this);
    this.updateScaleFactor = this.updateScaleFactor.bind(this)
    // this.requestHomography = this.requestHomography.bind(this);
    // this.waitForHomography = this.waitForHomography.bind(this);
    // this.loadHomography = this.loadHomography.bind(this);
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



  createSensorMap(sensors,floorPlanImHeight){
    let sensorArray = []

    Object.values(sensors).map((sensor,idx) => {
      console.log("sfpl map", sensor)
      sensorArray.push({
          id: sensor.sensorId,
          dbId: sensor.id,
          sensorId: sensor.sensorId,
          type: "point",
          name: sensor.sensorId,
          limit: true,
          draw:true,
          height: floorPlanImHeight

      })

    })
    console.log("sfpl map", sensorArray)

    return sensorArray
  }

  createSensorDataMap(sensors){
    // let sensorData = {}

    let sensorData = new Map();

    Object.values(sensors).map((sensor,idx) => {
      //console.log("sfpl map", sensor, JSON.parse(sensor.coordinates), JSON.parse)
      let sensorId = (typeof sensor.sensorId === 'string' || typeof sensor.sensorId === 'number')? sensor.sensorId : null;
      let coordinates = sensor.coordinates ? JSON.parse(sensor.coordinates) : null;
      sensorData.set(sensorId, coordinates);
      //sensorData[sensor.sensorId] = JSON.parse(sensor.coordinates)

    })
    console.log("sfpl map", sensorData)

    return sensorData
  }


  /**
   * Load sensor data from the backend.
   */
  async loadPage() {
    const { projectId } = this.props.match.params;

    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {

        const project = res.data;
        // console.log("here1", project, project.sensor_set)

        const { mapAPIKey, floorPlanImHeight, floorPlanImageUrl, sensor_set } = project;
        // console.log("here", floorPlanImageUrl, coordinates, sensor_set, floorPlanImHeight)
        //TODO get coordinate information from sensor_set
        // const coordinates = sensors


        // console.log ("fix p", sensor.)
        // const sensorPolygon = JSON.parse(sensor.sensorPolygon);
        // const gisPolygon = JSON.parse(sensor.gisPolygon);
        // const roiPolygon = JSON.parse(sensor.roiPolygon);
        // const tripwireLines = JSON.parse(sensor.tripwireLines);
        // const tripDirLines = JSON.parse(sensor.tripDirLines);
        // console.log ("fpcL", sensorPolygon, gisPolygon)
        // let newSensorData = {
        //   [camPlacementId]: sensorPolygon,
        //   [camPlacementMapId]: gisPolygon,
        //   [roiId]: roiPolygon,
        //   [tripwireId]: tripwireLines,
        //   [tripDirId]: tripDirLines
        // };
        const sensorMap = this.createSensorMap(sensor_set,floorPlanImHeight)
        const sensorLabelData = this.createSensorDataMap(sensor_set)
        const newMapData = {
          imagesLabels: sensorMap,
          imageLabelData: sensorLabelData
        };
        let drawLabels = sensorMap;
        // drawLabels.push(

        //   sensorMap
        // );

        console.log("test",  getMediaUrl(floorPlanImageUrl))
        this.setState({
          mapAPIKey,
          project,
          sensors: sensor_set,
          sensorMap,
          // isCalibrated,
          isLoaded: true,
          mapLabelData: newMapData,
          floorPlanImageUrl: getMediaUrl(floorPlanImageUrl),
          projectId: projectId,
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
    console.log("fix p,", this.state, this.props, height, fpImHeight)

    // console.log("invadfjal;f", this.state.invImHeight,invertImXPad, invertImYPad)
    const polygonArray = []
    const category = Object.keys(polygon);
    // const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure,index) => {
      console.log ("fix p fig", figure)
      const newShapePoints = figure.points.map( point =>{
        console.log ("fix p", point)
        // console.log("hpt",this.state.height)
        // console.log("ght",point)
        const flipOrigPoint = addPointY(point, (height-fpImHeight))
        console.log ("fix p fop", flipOrigPoint)
        const flippedPoint = flipPointY(flipOrigPoint, fpImHeight)
        console.log ("fix p fp", flippedPoint)

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
    const category = Object.keys(polygon);
    // const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure,index) => {
      console.log ("fix p fig", figure)
      const newShapePoints = figure.points.map( point =>{
        console.log ("fix p", point, height)
        // console.log("hpt",this.state.height)
        // console.log("ght",point)
        const flipOrigPoint = flipPointY(point, height)
        console.log ("fix p fop", flipOrigPoint)
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


  async pushScaleFactorData(data){
    const {id, scaleFactor} = data
    await axios
    .patch(`${API_ENDPOINT}/sensors/${id}/`, {
      scaleFactor
    })
    .then()
    .catch(error => console.error("err", error));
  }

  async pushSensorData(data){
    const {id, coordinates} = data
    await axios
    .patch(`${API_ENDPOINT}/sensors/${id}/`, {
      coordinates
    })
    .then()
    .catch(error => console.error("err", error));
  }

  async pushProjectData(id,scaleFactor){
    // const {id, coordinates} =
    await axios
    .patch(`${API_ENDPOINT}/projects/${id}/`, {
      scaleFactor
    })
    .then()
    .catch(error => console.error("err", error));
  }


  /**
   * Push update scale Factor to the backend.
   * @param {object} labelData Contains information to be sent to the backend:
   */
     updateScaleFactor(labelData) {
      console.log("output", this.props)
      const { projectId } = this.props.match.params;
      const {sensors} = this.state
      console.log("output", sensors, )
      // console.log ( "output", this.state.sensor.height,this.state.project.floorPlanImHeight )
      // const coordinates = JSON.stringify((labelData.imageLabelData[camPlacementId]));
      let scaleFactor = 0.0
      sensors.forEach(s =>{

        const id = s.id
        scaleFactor = labelData.scaleFactor;

        this.pushScaleFactorData({id,scaleFactor})

      })
      this.pushProjectData(projectId,scaleFactor)

    }

  /**
   * Push updates to the backend.
   * @param {object} labelData Contains information to be sent to the backend:
   */
  pushUpdate(labelData) {
    console.log("output", this.props)
    const { projectId } = this.props.match.params;
    const {sensors} = this.state
    console.log("output", sensors)
    // console.log ( "output", this.state.sensor.height,this.state.project.floorPlanImHeight )
    const coordinates = JSON.stringify((labelData.imageLabelData[camPlacementId]));


    Object.keys(labelData.mapLabelData).forEach( sensorName => {
      console.log("output p", sensorName)
      if (sensorName !== "__temp"){
        // const sensor = sensors.filter(({sensorId})=> sensorName.includes(sensorId))
        const sensor = sensors.filter(item => item.sensorId === sensorName)

        console.log("output pu", sensor[0])
        const {id, sensorId}  = sensor[0]

        console.log("output pus", sensor, sensorId)
        const coordinates = JSON.stringify((labelData.mapLabelData[sensorId]));
        console.log("output puc", id, coordinates)
        this.pushSensorData({id,coordinates})
      }

      });



  }

  /**
   * Go back to the Sensors tab in the project page.
   */
  goBack = () => {
    const { projectId } = this.state;
    let path = `/projects/mtmc/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      // state: { prevPath: "Sensors" }
    });
  };

  render() {
    const projectId  = DOMPurify.sanitize(this.props.match.params.projectId);
    const {
      mapAPIKey,
      project,
      mapLabelData,
      mapCenter,
      labels,
      drawLabels,
      imageLabelData,
      error,
      isLoaded,
      floorPlanImageUrl,
      mapZoom,
      isCalibrated,
      sensorMap
    } = this.state;
    console.log("sfpl", floorPlanImageUrl, error, this.state)
    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error['message']}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    } else if (!floorPlanImageUrl) {
      return (
        <div>
          <h2>Error: Please upload image for calibration.</h2>
        </div>
      );
    }

    return (
      <SetupFloorPlanApp
        data-testid="CalibLoaderApp"
        sensorId={project.name}
        id={projectId}
        type={"projects"}
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabelData={mapLabelData.imageLabelData}
        mapLabels={sensorMap}
        imageUrl={floorPlanImageUrl}
        labels={labels}
        sensors={sensorMap}
        drawLabels = {drawLabels}
        labelData={imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        isCalibrated={isCalibrated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        updateScaleFactor={this.updateScaleFactor}
      />
    );
  }
}
