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
import { matrix, multiply } from "mathjs";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Loader } from "semantic-ui-react";
import {
  convertLatLngToXYMatrix,
  convertProjectedPoint,
  flipPointY
} from "../common/mathUtils";
import { calValId, cartRoiId, cartTripDirId, cartTripwireId } from "../common/utils";
import CartesianValidationApp from "./CartesianValidationApp";
import "./CartesianValidationStyles.css";

import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Loader to load all image and map data required for validating the sensor
 * homography matrix calculated in the calibration phase. A loader takes no
 * props, except the sensorId passed through the URL.
 */
class CartesianValidationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      sensorId: null,
      hasHomography: false,
      homography: null,
      imageUrl: null,
      invertImageUrl: null,
      isLoaded: false,
      height: null,
      width: null,
      invImHeight: null,
      invImWidth: null,
      error: null,
      loadingHomographyImage: false,
      mapData: {
        imagesLabels: [
          {
            type: "polyline",
            name: "Validation",
            id: calValId,
            limit: true,
            draw: false
          },
          {
            type: "polygon",
            name: "ROI",
            id: cartRoiId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: cartTripwireId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Direction",
            id: cartTripDirId,
            limit: true,
            draw: false
          }

        ],
        imageLabelData: {
          [calValId]: [],
          [cartRoiId]: [],
          [cartTripwireId]: [],
          [cartTripDirId]: []
        }
      },

      imgCrop: {},
      drawLabels: [
        {
          type: "polygon",
          name: "validation",
          id: calValId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: cartRoiId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: cartTripwireId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: cartTripDirId,
          limit: false,
          draw: false
        }

      ],
      imageData: {
        imageLabels: [
          {
            type: "polyline",
            name: "validation",
            id: calValId,
            limit: true,
            draw: true
          },
          {
            type: "polygon",
            name: "ROI",
            id: cartRoiId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Tripwire",
            id: cartTripwireId,
            limit: false,
            draw: false
          },
          {
            type: "polyline",
            name: "Direction",
            id: cartTripDirId,
            limit: true,
            draw: false
          }

        ],
        imageLabelData: {
          [calValId]: [],
          [cartRoiId]: [],
          [cartTripDirId]: [],
          [cartTripwireId]: []
        }
      },
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
          id: cartRoiId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: cartTripwireId,
          limit: false,
          draw: false
        },
        {
          type: "polyline",
          name: "Direction",
          id: cartTripDirId,
          limit: true,
          draw: false
        }

      ],
      cityId: null,
      projectId: null
    };

    this.handleValidated = this.handleValidated.bind(this);
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
    this.updateValImView = this.updateValImView.bind(this);
    this.waitForHomographyImage = this.waitForHomographyImage.bind(this);
    this.requestHomographyImage = this.requestHomographyImage.bind(this);
    this.loadHomographyImage = this.loadHomographyImage.bind(this)
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
    const setState = this.setState.bind(this);
    this._img.onload =  function() {
      const { height, width } = this;
      // console.log("height,width", height,width)
      setState({ height, width });
    };
    this._img.src = imageUrl
    this.setState({
      width : this._img.width,
      height : this._img.height
    } );
    // console.log("geturl",imageUrl)

    // console.log("getimage", this._img.height, this.state.height)

  }

  /**
   * get inverted Image Url
   * @param {String} imageUrl: url to image
   */
  getInvImageHeight(imageUrl){
    this._img = new Image();
    const setState = this.setState.bind(this);
    this._img.onload =  function() {
      const { height, width } = this;
      console.log("invheight,invwidth", height,width)
      setState({
        invImWidth : width,
        invImHeight : height
      } );
    };
    this._img.src = imageUrl
    this.setState({
      invImWidthidth : this._img.width,
      invImHeight : this._img.height
    } );
    // console.log("getinvimage", this._img.height, this.state.invImHeight)

  }
  /**
   * get homography polygon
   * @param {json} polygon
   * @param {matrix} M
   * @returns {Array}
   */
  getHomographyPolygon(polygon,M){
    // console.log("height", this.state.height, this.state.invImHeight)
    // console.log("invertImHeight",this.props, this.state)

    // const invertImXPad = (this.state.imgCrop.invertImXPad)
    // const invertImYPad = (this.state.imgCrop.invertImYPad)
    // console.log("invadfjal;f", this.state.invImHeight,invertImXPad, invertImYPad)
    const polygonArray = []
    // const category = Object.keys(polygon);
    const homography_ = matrix(JSON.parse(M))

    // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

    Object.values(polygon).map((figure) => {
      // const idx = index
      const newShapePoints = figure.points.map( point =>{
        console.log("hpt",this.state.invImHeight)
        // console.log("ght",point)
        const flippedPoint = flipPointY(point, this.state.invImHeight)
        // console.log("ght1",flippedPoint)
        const matrixPoint = convertLatLngToXYMatrix(flippedPoint, this.state.invImHeight);
        // console.log("xpt",matrixPoint)
        // console.log("h12",homography_)
        const projectedPoint = convertProjectedPoint(
          multiply(homography_, matrixPoint)
        );
        // console.log("ght2",projectedPoint)
        // console.log("asdjfklaj load", this.state.invImHeight)
        // const flipProjPoint = flipPointY(projectedPoint, this.state.invImHeight)
        // console.log("ght3",flipProjPoint)

        // const paddedPoint = padPointY(projectedPoint,0, invertImYPad,this.state.height)

        // console.log(projectedPoint)
        return projectedPoint;

      });
      // console.log ("polygon", figure.id)
      polygonArray.push({
        id: figure.id,
        points: newShapePoints,
        type: figure.type
      })
    });
    // const newShapePoints = polygon[category].points.map(point => {

    //   const matrixPoint = convertLatLngToXYMatrix(point, this.state.height);
    //   const projectedPoint = convertProjectedPoint(
    //     multiply(homography_, matrixPoint)
    //   );
    //   return projectedPoint;
    // });
    // polygonArray.push({
    //   id: polygon.id,
    //   points: newShapePoints,
    //   type: polygon.type,
    // });

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
          const { city, project, sensorId, mapAPIKey, homography } = sensor;
          let hasHomography;
          const imageUrl = getMediaUrl(sensor.imageUrl)
          const invertImageUrl = getMediaUrl(sensor.invertImageUrl)
          this.getImageHeight(imageUrl)
          this.getInvImageHeight(invertImageUrl)
          if (homography === "") {
            hasHomography = false;
          } else {
            hasHomography = true;
          }
          const imgCrop = {
            invertImXPad: sensor.invertImXPad,
            invertImYPad: sensor.invertImYPad,
            invertImWidth: sensor.invertImWidth,
            invertImHeight: sensor.invertImHeight
          }
          this.setState({
            mapAPIKey,
            sensorId,
            hasHomography,
            homography,
            imageUrl,
            invertImageUrl,
            isLoaded: true,
            cityId: city,
            projectId: project,
            imgCrop,
            invImHeight: sensor.invertImHeight

          });

          let cartRoiHomographyPolygon =[]
          const cartRoiPolygon = JSON.parse(sensor.roiPolygon);
          if (cartRoiPolygon.length !== 0){
            cartRoiHomographyPolygon= this.getHomographyPolygon(cartRoiPolygon,homography)
          }
          // console.log("ptasdf", cartRoiHomographyPolygon)
          let cartTripwireHomographyLines = []
          const cartTripwireLines = JSON.parse(sensor.tripwireLines);
          if (cartTripwireLines.length !== 0){
            cartTripwireHomographyLines = this.getHomographyPolygon(cartTripwireLines,homography)
          }
          let cartTripDirHomographyLines = []
          const cartTripDirLines = JSON.parse(sensor.tripDirLines);
          if (cartTripDirLines.length !== 0){
            cartTripDirHomographyLines = this.getHomographyPolygon(cartTripDirLines,homography)
          }


          const mapData =  {
            imagesLabels: [
              {
                type: "polyline",
                name: "validation",
                id: calValId,
                limit: true,
                draw: false,
              },
              {
                type: "polygon",
                name: "ROI",
                id: cartRoiId,
                limit: false,
                draw: false,
              },
              {
                type: "polyline",
                name: "Tripwire",
                id: cartTripwireId,
                limit: false,
                draw: false,
              },
              {
                type: "polyline",
                name: "Direction",
                id: cartTripDirId,
                limit: false,
                draw: false,
              }

            ],
            imageLabelData: {
              [calValId]: [],
              [cartRoiId]: cartRoiHomographyPolygon,
              [cartTripwireId]: cartTripwireHomographyLines,
              [cartTripDirId]: cartTripDirHomographyLines
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
    const roiPolygon = JSON.stringify(labelData.imageLabelData[cartRoiId]);
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


   /**
   * Handle updating homography and updating homography image
   */
    async updateValImView(imgCrop) {
      //need to make sure new homography items are set
      const {invertImXPad, invertImYPad, invertImWidth, invertImHeight }= imgCrop
      const { sensorId } = this.props.match.params;
      // console.log("push", imgCrop)
      await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        // roiPolygon,
        // isValidated,
        invertImXPad,
        invertImYPad,
        invertImWidth,
        invertImHeight
      })
      .then()
      .catch(error => console.error("err", error));
      await this.waitForHomography()
      await this.waitForHomographyImage()

      this.props.alert.success("Homography updated")
      this.forceUpdate()

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
   * Handles Updating the validation calibration image.
   * Wrapping the requestHomographyImage and loadHomographyImage functions to ensure
   * sequential calling.
   */
     async waitForHomographyImage() {

      this.setState({ loadingHomographyImage: true });
      await this.requestHomographyImage();
      await this.loadHomographyImage();

      this.setState({ loadingHomographyImage: false });

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
 * Load the homography from the backend
 */
  async loadHomographyImage() {
    const { sensorId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const invertImageUrl  = getMediaUrl(res.data.invertImageUrl);

        this.setState({ invertImageUrl });
      })
      .catch(error => console.error(error));
  }

  /**
   * Request the calculation of the homography to be done in the backend.
   */
  async requestHomographyImage() {
    const { sensorId } = this.props.match.params;
    //update
    await axios
      .get(`${API_ENDPOINT}/invertImage/${sensorId}/`)
      .then()
      .catch(error => console.error(error));
  }

  /**
  * Request the calculation of the homography to be done in the backend.
  */
  async requestHomography() {
  const { sensorId } = this.props.match.params;
  await axios
    .get(`${API_ENDPOINT}/approxHomography/${sensorId}/`)
    .then()
    .catch(error => console.error(error));
}
  /**
   * Go back to the City page under the Sensors tab.
   */
  goBack = () => {
    const { cityId, projectId } = this.state;
    let path = `/projects/cartesian/calibrateSensors/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Sensors" }
    });
  };

  render() {
    const { cameraId, height } = DOMPurify.sanitize(this.props.match.params);
    const { imageUrl, invertImageUrl, homography } = this.state;
    const {
      mapAPIKey,
      sensorId,
      hasHomography,
      mapData,
      mapCenter,
      imageData,
      drawLabels,
      mapLabels,
      error,
      isLoaded,
      mapZoom,
      imgCrop
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
    // console.log("thisadf", imgCrop)
    console.log("ptsend", imageData.imageLabelData)
    return (
      <CartesianValidationApp
        sensorId={sensorId}
        id={sensorId}
        type="sensors"
        labels={imageData.imageLabels}
        drawLabels={drawLabels}
        API_KEY={mapAPIKey}
        imgCrop={imgCrop}
        mapLabels={mapLabels}
        mapLabelData={mapData.imageLabelData}
        imageUrl={imageUrl}
        invertImageUrl={invertImageUrl}
        labelData={imageData.imageLabelData}
        onLabelChange={this.pushUpdate.bind(this)}
        homography={matrix(JSON.parse(homography))}
        handleValidated={this.handleValidated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        updateValImView={this.updateValImView}
      />
    );
  }
}

export default withAlert()(CartesianValidationLoader);
