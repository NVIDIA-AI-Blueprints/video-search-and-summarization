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

import CartesianApp from "./CartesianApp";
import { cartCalibId, cartRoiId, cartTripDirId, cartTripwireId } from "../common/utils";

import "./CartesianStyles.css";
// import NewSensorModal from "../modals/NewSensorModal";
import { withAlert } from "react-alert";
import DOMPurify from "dompurify";
import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Loader to load all map and image data required for calibrating a sensor. A
 * loader takes no props, except for the sensorId passed through the URL.
 */
class CartesianLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      sensor: null,
      isCalibrated: false,
      isValidated: false,
      isLoaded: false,
      error: null,
      imageUrl: null,
      loadingHomography: false,
      loadingHomographyImage: false,
      homography: null,
      labels: [
        {
          type: "polygon",
          name: "Calibration",
          id: cartCalibId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: cartRoiId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: cartTripwireId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Direction",
          id: cartTripDirId,
          limit: false,
          draw: true
        }
      ],
      drawLabels: [
        {
          type: "polygon",
          name: "Calibration",
          id: cartCalibId,
          limit: true,
          draw: true
        },
        {
          type: "polygon",
          name: "ROI",
          id: cartRoiId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Tripwire",
          id: cartTripwireId,
          limit: false,
          draw: true
        },
        {
          type: "polyline",
          name: "Direction",
          id: cartTripDirId,
          limit: false,
          draw: true
        }
      ],
      imageLabelData: {
        [cartCalibId]: [],
        [cartRoiId]: [],
        [cartTripwireId]: [],
        [cartTripDirId]: []

      },
      edgeLengths: [
        {
          xcoord:[],
          ycoord:[]
        }
      ],
      edgeValidation: [],
      projectId: null,
      imgCrop: {}
    };
    this.loadPage = this.loadPage.bind(this);
    this.pushUpdate = this.pushUpdate.bind(this);
    this.requestHomography = this.requestHomography.bind(this);
    this.waitForHomography = this.waitForHomography.bind(this);
    this.requestHomographyImage = this.requestHomographyImage.bind(this);
    this.waitForHomographyImage = this.waitForHomographyImage.bind(this);
    this.updateValImView = this.updateValImView.bind(this);

    this.loadHomography = this.loadHomography.bind(this);
    this.loadHomographyImage = this.loadHomographyImage.bind(this);

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
        console.log("sensor1", sensor)
        const {  project, isCalibrated } = sensor;
        const sensorPolygon = JSON.parse(sensor.sensorPolygon);
        const roiPolygon = JSON.parse(sensor.roiPolygon);
        const tripwireLines = JSON.parse(sensor.tripwireLines);
        const tripDirLines = JSON.parse(sensor.tripDirLines);
        const edgeLengths = JSON.parse(sensor.edgeLengths);
        
        // Initialize image dimensions
        const img = new Image();
        img.src = getMediaUrl(sensor.imageUrl);
        img.onload = () => {
          // Always ensure invertImHeight matches the actual image height
          if (!sensor.invertImHeight || sensor.invertImHeight !== img.height) {
            sensor.invertImHeight = img.height;
            sensor.invertImWidth = img.width;
            sensor.invertImXPad = 0;
            sensor.invertImYPad = 0;
            
            // Update the sensor with correct image dimensions
            axios.patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
              invertImHeight: img.height,
              invertImWidth: img.width,
              invertImXPad: 0,
              invertImYPad: 0
            }).then(() => {
              // Only update state after dimensions are properly set
              this.setState({
                sensor,
                isCalibrated,
                edgeLengths,
                edgeValidation,
                isLoaded: true,
                imageLabelData: newSensorData,
                imageUrl: getMediaUrl(sensor.imageUrl),
                homography,
                projectId: project,
                drawLabels: drawLabels
              });
            }).catch(error => console.error("Error setting initial image dimensions:", error));
          }
        };

        const re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
        const edgeValidation = edgeLengths.map(length => {
          const xcoord = length.lng
          const ycoord = length.lat
          return (re.test(xcoord) && re.test(ycoord)) ;
        });
        let newSensorData = {
            [cartCalibId]: sensorPolygon,
            [cartRoiId]:  roiPolygon,
            [cartTripwireId]: tripwireLines,
            [cartTripDirId]: tripDirLines
          };
        let drawLabels = [];
        drawLabels.push(
            {
              type: "polygon",
              name: "Calibration",
              id: cartCalibId,
              limit: true,
              draw: true,
              height: sensor.height
            },
            {
              type: "polygon",
              name: "ROI",
              id: cartRoiId,
              limit: false,
              draw: true,
              height: sensor.height
            },
            {
              type: "polyline",
              name: "Tripwire",
              id: cartTripwireId,
              limit: false,
              draw: true,
              height: sensor.height
            },
            {
              type: "polyline",
              name: "Direction",
              id: cartTripDirId,
              limit: false,
              draw: true,
              height: sensor.height
            }
          );
        let { homography } = sensor;
        if (homography === "") {
          homography = null;
        }
        this.setState({
          sensor,
          isCalibrated,
          edgeLengths,
          edgeValidation,
          isLoaded: true,
          imageLabelData: newSensorData,
          imageUrl: getMediaUrl(sensor.imageUrl),
          homography,
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
    const sensorPolygon = JSON.stringify(labelData.imageLabelData[cartCalibId]);
    const roiPolygon = JSON.stringify(labelData.imageLabelData[cartRoiId]);
    const tripwireLines = JSON.stringify(labelData.imageLabelData[cartTripwireId]);
    const tripDirLines = JSON.stringify(labelData.imageLabelData[cartTripDirId]);

    // creating floats does not work for image re-creation
    // const floatEdgeLengths = []
    // let edgeLengths = ""
    // if (labelData.edgeLengths) {
    //   labelData.edgeLengths.forEach(edge => {
    //     console.log("edge", edge)
    //     const newEdge = {
    //       lng: parseFloat(edge.lng),
    //       lat: parseFloat(edge.lat)

    //     }

    //     console.log("12edge", newEdge)
    //     floatEdgeLengths.push(newEdge)
    //     edgeLengths = JSON.stringify(floatEdgeLengths);

    //   });
    // }
    // else {
    //     edgeLengths = JSON.stringify(labelData.edgeLengths)
    // }
    // console.log("123edge",labelData.edgeLengths)
    const edgeLengths = JSON.stringify(labelData.edgeLengths)
    const isCalibrated = !!labelData.isCalibrated;
    const isValidated = !!labelData.isValidated;

    // console.log("sensorOnly", this.props.sensorOnly)

    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, {
        sensorPolygon,
        roiPolygon,
        tripwireLines,
        tripDirLines,
        edgeLengths,
        isCalibrated,
        isValidated,
        // sensorOnly
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
        const { invertImageUrl } = res.data;
        this.setState({ invertImageUrl });
      })
      .catch(error => console.error(error));
  }

  /**
   * Request the calculation of the homography to be done in the backend.
   */
  async requestHomographyImage() {
    const { sensorId } = this.props.match.params;
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
 * Handle updating homography and updating homography image
 */
  async updateValImView(imgCrop) {
    //need to make sure new homography items are set
    const {invertImXPad, invertImYPad, invertImWidth, invertImHeight }= imgCrop
    console.log(this.props)
    const { sensorId } = this.props.match.params;
    console.log("push", imgCrop)
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
    // await this.waitForHomography()
    // await this.waitForHomographyImage()

    this.props.alert.success("Updated Images")
    this.forceUpdate()

  }



  /**
   * Go back to the Sensors tab in the city page.
   */
  goBack = () => {
    const {  projectId } = this.state;
    let path = `/projects/cartesian/calibrateSensors/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Sensors" }
    });
  };

  render() {
    const { sensorId } = DOMPurify.sanitize(this.props.match.params);
    const {
      sensor,
      labels,
      drawLabels,
      imageLabelData,
      edgeLengths,
      edgeValidation,
      error,
      isLoaded,
      imageUrl,
      homography,
      loadingHomography,
      loadingHomographyImage,
      isCalibrated,
      // isValidated
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
    const imgCrop = {
      invertImXPad: sensor.invertImXPad,
      invertImYPad: sensor.invertImYPad,
      invertImWidth: sensor.invertImWidth,
      invertImHeight: sensor.invertImHeight
    }
    return (
      <CartesianApp
        data-testid="CartesianApp"
        sensorId={sensor.sensorId}
        sensorName={sensor.sensorId}
        id={sensorId}
        type={"sensors"}
        imageUrl={imageUrl}
        imgCrop={imgCrop}
        drawLabels={drawLabels}
        labels={labels}
        labelData={imageLabelData}
        edgeLengths={edgeLengths}
        edgeValidation={edgeValidation}
        onLabelChange={this.pushUpdate.bind(this)}
        waitForHomography={this.waitForHomography}
        waitForHomographyImage={this.waitForHomographyImage}
        loadingHomography={loadingHomography}
        loadingHomographyImage={loadingHomographyImage}
        homography={homography}
        isCalibrated={isCalibrated}
        reloadPage={this.loadPage}
        goBack={this.goBack}
        updateValImView={this.updateValImView}
        calibrationType={sensor.calibrationType}
      />
    );
  }
}

export default withAlert()(CartesianLoader);