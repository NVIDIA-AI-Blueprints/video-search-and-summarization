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
import {
  flipPointY
} from "../common/mathUtils";
import {
  floorPlanRoiId,
  floorPlanTripDirId, floorPlanTripwireId, calValId
} from "../common/utils";
import DOMPurify from "dompurify";

import FloorPlanValidationApp from "./FloorPlanValidationApp";
import "./FloorPlanValidationStyles.css";


import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Loader to load all image and map data required for validating the sensor
 * homography matrix calculated in the calibration phase. A loader takes no
 * props, except the sensorId passed through the URL.
 */
class FloorPlanValidationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      sensorId: null,
      hasHomography: false,
      homography: null,
      imageUrl: null,
      floorPlanImageUrl: null,
      isLoaded: false,
      height: null,
      width: null,
      fpImHeight:null,
      fpImWidth:null,
      // invImHeight: null,
      // invImWidth: null,
      error: null,
      // loadingHomographyImage: false,
      mapData: {
        imagesLabels: [
          {
            type: "polygon",
            name: "Validation",
            id: calValId,
            limit: true,
            draw: false
          },
          {
            type: "polygon",
            name: "ROI",
            id: floorPlanRoiId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: floorPlanTripwireId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Direction",
            id: floorPlanTripDirId,
            limit: true,
            draw: false
          }

        ],
        imageLabelData: {
          [calValId]: [],
          [floorPlanRoiId]: [],
          [floorPlanTripwireId]: [],
          [floorPlanTripDirId]: []
        }
      },

      imgCrop: {},

      imageData: {
        imageLabels: [
          {
            type: "polygon",
            name: "Validation",
            id: calValId,
            limit: true,
            draw: true
          },
          {
            type: "polygon",
            name: "ROI",
            id: floorPlanRoiId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: floorPlanTripwireId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Direction",
            id: floorPlanTripDirId,
            limit: true,
            draw: false
          }

        ],
        imageLabelData: {
          [calValId]: [],
          [floorPlanRoiId]: [],
          [floorPlanTripwireId]: [],
          [floorPlanTripDirId]: []
        }
      },
      cityId: null,
      projectId: null,
      drawLabels:[
        {
          type: "polygon",
          name: "Validation",
          id: calValId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: floorPlanRoiId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: floorPlanTripwireId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: floorPlanTripDirId,
          limit: false,
          draw: false
        }
      ],
      mapLabels: [
        {
          type: "polygon",
          name: "Validation",
          id: calValId,
          limit: true,
          draw: false
        },
        {
          type: "polygon",
          name: "ROI",
          id: floorPlanRoiId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: floorPlanTripwireId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: floorPlanTripDirId,
          limit: true,
          draw: false
        }

      ],
    };

    this.handleValidated = this.handleValidated.bind(this);
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
    // this.updateValImView = this.updateValImView.bind(this);
    // this.waitForHomographyImage = this.waitForHomographyImage.bind(this);
    // this.requestHomographyImage = this.requestHomographyImage.bind(this);
    // this.loadHomographyImage = this.loadHomographyImage.bind(this)
    this.waitForHomography = this.waitForHomography.bind(this);
    this.requestHomography = this.requestHomography.bind(this);
    this.loadHomography = this.loadHomography.bind(this)
  }

  /**
   * Run the loadPage function on component mount.
   */
  async componentDidMount() {
    this.loadPage();
  }

  /**
   * get Image Url
   * @param {String} imageUrl: url to image
   */
  getImageHeight(imageUrl){
    this._img = new Image();
    this._img.src = imageUrl
    // console.log("geturl",imageUrl)
    this.setState({
      width : this._img.width,
      height : this._img.height
    } );
    console.log("getimage", this._img.height)

  }

  /**
   * get inverted Image Url
   * @param {String} imageUrl: url to image
   */
  getInvImageHeight(imageUrl){
    this._img = new Image();
    this._img.src = imageUrl
    // console.log("geturl",imageUrl)
    this.setState({
      fpImWidth : this._img.width,
      fpImHeight : this._img.height
    } );
    console.log("getimage", this._img.height)
  }


  /**
   * get homography polygon
   * @param {json} polygon
   * @param {matrix} M
   * @returns {Array}
   */
  getHomographyPolygon(polygon, M, fpImHeight){

    // console.log ("am i here")
    // const height = (this.state.height)
    // const fpImHeight = (this.state.fpImHeight)
    // console.log("adsf", height, fpImHeight)
    // console.log("invadfjal;f", this.state.invImHeight,invertImXPad, invertImYPad)
    const polygonArray = []
    const category = Object.keys(polygon);
    const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure,index) => {
      const newShapePoints = figure.points.map( point =>{
        // const { homography } = this.props;
        console.log("init", point, fpImHeight)

        const flipOrigPoint = flipPointY(point, fpImHeight)
        return flipOrigPoint;

      });
      // console.log ("polygon", figure.id)
      polygonArray.push({
        id: figure.id,
        points: newShapePoints,
        type: figure.type
      })
    });

    return polygonArray
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
          const { city, project, sensorId, mapAPIKey, homography,
                  floorPlanImHeight, floorPlanImWidth} = sensor;

          const imageUrl = getMediaUrl(sensor.imageUrl)
          const floorPlanImageUrl = getMediaUrl(sensor.floorPlanImageUrl)
          let hasHomography;
          this.getImageHeight(imageUrl)
          this.getInvImageHeight(floorPlanImageUrl)
          if (homography === "") {
            hasHomography = false;
          } else {
            hasHomography = true;
          }
          // const imgCrop = {
          //   invertImXPad: sensor.invertImXPad,
          //   invertImYPad: sensor.invertImYPad,
          //   invertImWidth: sensor.invertImWidth,
          //   invertImHeight: sensor.invertImHeight
          // }
          this.setState({
            mapAPIKey,
            sensorId,
            hasHomography,
            homography,
            imageUrl,
            floorPlanImageUrl,
            isLoaded: true,
            cityId: city,
            projectId: project,
            // imgCrop
            fpImHeight: floorPlanImHeight,
            // fpImWidth: floorPlanImWidth

          });
          console.log(homography)
          console.log("fp initialize, " ,this.state)
          let floorPlanRoiHomographyPolygon =[]
          const floorPlanRoiPolygon = JSON.parse(sensor.roiPolygon);
          if (floorPlanRoiPolygon.length !== 0){
            floorPlanRoiHomographyPolygon= this.getHomographyPolygon(floorPlanRoiPolygon,homography, floorPlanImHeight)
          }
          console.log("ptasdf", floorPlanRoiPolygon, floorPlanRoiHomographyPolygon)
          let floorPlanTripwireHomographyLines = []
          const floorPlanTripwireLines = JSON.parse(sensor.tripwireLines);
          if (floorPlanTripwireLines.length !== 0){
            floorPlanTripwireHomographyLines = this.getHomographyPolygon(floorPlanTripwireLines,homography, floorPlanImHeight)
          }
          let floorPlanTripDirHomographyLines = []
          const floorPlanTripDirLines = JSON.parse(sensor.tripDirLines);
          if (floorPlanTripDirLines.length !== 0){
            floorPlanTripDirHomographyLines = this.getHomographyPolygon(floorPlanTripDirLines,homography, floorPlanImHeight)
          }


          const mapData =  {
            imagesLabels: [
              {
                type: "polygon",
                name: "Validation",
                id: calValId,
                limit: true,
                draw: false,
                height: floorPlanImHeight
              },
              {
                type: "polygon",
                name: "ROI",
                id: floorPlanRoiId,
                limit: false,
                draw: true,
                height: floorPlanImHeight
              },
              {
                type: "polyline",
                name: "Tripwire",
                id: floorPlanTripwireId,
                limit: false,
                draw: false,
                height: floorPlanImHeight
              },
              {
                type: "polyline",
                name: "Direction",
                id: floorPlanTripDirId,
                limit: true,
                draw: false,
                height: floorPlanImHeight
              }
            ],
            imageLabelData: {
              [calValId]: [],
              [floorPlanRoiId]: floorPlanRoiHomographyPolygon,
              [floorPlanTripwireId]: floorPlanTripwireHomographyLines,
              [floorPlanTripDirId]: floorPlanTripDirHomographyLines
            }
          };

          this.setState({ mapData })
          // console.log("ptcheck", this.state.mapData)






      })
      .catch(error => {
        this.setState({
          isLoaded: true,
          error
        });
      });
    this.forceUpdate()

  }

  /**
   * Push roi polygon and validation updates to the backend. Update invert Crop params
   * @param {object} labelData Contains information to be sent to the backend:
   */
  async pushUpdate(labelData) {
    const { sensorId } = this.props.match.params;
    const roiPolygon = JSON.stringify(labelData.imageLabelData[floorPlanRoiId]);
    const isValidated = !!labelData.isValidated;

    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        roiPolygon,
        isValidated,
        // invertImXPad,
        // invertImYPad,
        // invertImWidth,
        // invertImHeight
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


  //  /**
  //  * Handle updating homography and updating homography image
  //  */
  //   async updateValImView(imgCrop) {
  //     //need to make sure new homography items are set
  //     const {invertImXPad, invertImYPad, invertImWidth, invertImHeight }= imgCrop
  //     const { sensorId } = this.props.match.params;
  //     // console.log("push", imgCrop)
  //     await axios
  //     .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
  //       // roiPolygon,
  //       // isValidated,
  //       invertImXPad,
  //       invertImYPad,
  //       invertImWidth,
  //       invertImHeight
  //     })
  //     .then()
  //     .catch(error => console.error("err", error));
  //     await this.waitForHomography()
  //     await this.waitForHomographyImage()

  //     this.props.alert.success("Homography updated")
  //     this.forceUpdate()
    // }

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

  // /**
  //  * Handles Updating the validation calibration image.
  //  * Wrapping the requestHomographyImage and loadHomographyImage functions to ensure
  //  * sequential calling.
  //  */
  //    async waitForHomographyImage() {

  //     this.setState({ loadingHomographyImage: true });
  //     await this.requestHomographyImage();
  //     await this.loadHomographyImage();

  //     this.setState({ loadingHomographyImage: false });

  //   }

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

//  /**
//  * Load the homography from the backend
//  */
//   async loadHomographyImage() {
//     const { sensorId } = this.props.match.params;
//     await axios
//       .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
//       .then(res => {
//         const { invertImageUrl } = res.data;
//         this.setState({ invertImageUrl });
//       })
//       .catch(error => console.error(error));
//   }

  // /**
  //  * Request the calculation of the homography to be done in the backend.
  //  */
  // async requestHomographyImage() {
  //   const { sensorId } = this.props.match.params;
  //   //update
  //   await axios
  //     .get(`${API_ENDPOINT}/invertImage/${sensorId}/`)
  //     .then()
  //     .catch(error => console.error(error));
  // }

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
   * Go back to the City page under the Sensors tab.
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
    const { cameraId, height } = DOMPurify.sanitize(this.props.match.params);
    const { imageUrl, floorPlanImageUrl, homography } = this.state;
    const {
      mapAPIKey,
      sensorId,
      hasHomography,
      mapData,
      mapCenter,
      imageData,
      error,
      isLoaded,
      mapZoom,
      drawLabels,
      mapLabels,
      // imgCrop
      fpImHeight
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
    console.log("thisadf", fpImHeight)
    // console.log("ptsend", mapData.imageLabelData)
    return (
      <FloorPlanValidationApp
        sensorId={sensorId}
        id={sensorId}
        type="sensors"
        labels={imageData.imageLabels}
        drawLabels= {drawLabels}
        API_KEY={mapAPIKey}
        // imgCrop={imgCrop}
        fpImHeight={fpImHeight}
        mapLabels={mapLabels}
        mapLabelData={mapData.imageLabelData}
        imageUrl={imageUrl}
        floorPlanImageUrl={floorPlanImageUrl}
        labelData={imageData.imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        homography={matrix(JSON.parse(homography))}
        handleValidated={this.handleValidated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        // updateValImView={this.updateValImView}
      />
    );
  }
}

export default withAlert()(FloorPlanValidationLoader);
