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
import PropTypes from "prop-types";
import update from "immutability-helper";
import { withAlert } from "react-alert";
import { Loader, Button } from "semantic-ui-react";

import ImageLabeler from "../image_drawing/ImageLabeler";
import SensorLengthInput from "../cartesian_calibration/SensorLengthInput";
import CalibrationButton from "../calibration/CalibrationButton";
import DrawingManager from "../common/managers/DrawingManager";
import { colors, cartCalibId,  cartRoiId } from "../common/utils";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";

import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import { flipY } from "../common/mathUtils";
import CalibrationStats from "../calibration/CalibrationStats";
import NewCartImageInput from "../image_drawing/NewCartImageInput";
import { isFirstVertexMinimum } from "../../utils/vertex";
import "./CartesianStyles.css";

/**
 * App displaying the loaded image and map information side-by-side.
 * Enables the drawing of corresponding calibration polygons.
 */
class CartesianApp extends Component {
  constructor(props) {
    super(props);

    const { labels } = this.props;

    const toggles = {};
    labels.map(label => (toggles[label.id] = true));

    this.state = {
      imgCrop: {
        invertImXPad: 0,
        invertImYPad: 0,
        invertImWidth: 0,
        invertImHeight: 0
      },
      edgeLengths: this.props.edgeLengths,
      edgeValidation: this.props.edgeValidation,
      hasHomography: this.props.isCalibrated,
      selected: null,
      toggles,
      cropChangeModal: false,

    };

    this.handleSelected = this.handleSelected.bind(this);
    this.handleCalibClick = this.handleCalibClick.bind(this);
    this.handleSaveClick = this.handleSaveClick.bind(this);
    this.handleAcceptCalib = this.handleAcceptCalib.bind(this);
    this.updateEdgeLengths = this.updateEdgeLengths.bind(this);
    this.renderCropModal = this.renderCropModal.bind(this)
    this.toggleModal = this.toggleModal.bind(this);


  }

  /**
   * Checks if the figures or map shapes have changed, and save the changes to
   * the backend.
   * @param {object} prevProps Previous props before the update
   */
  componentDidUpdate(prevProps, prevState) {
    const { onLabelChange, figures } = this.props;
    const { edgeLengths } = this.state;
    this.updateEdgeLengths();

    if (
      prevProps.figures !== figures ||
      prevState.edgeLengths !== edgeLengths
    ) {
      console.log("label changed")
      onLabelChange({
        imageLabelData: flipY(figures, this.props.height),
        edgeLengths,
        isCalibrated: false,
        isValidated: false
      });
    }
  }


  /**
 * Toggle the modal showing the Warped Image module.
 */
   toggleModal() {
    const { cropChangeModal } = this.state;
    this.setState({ cropChangeModal: !cropChangeModal });
  }

  /**
   * Update the edge lengths on figures change or on user input event
   * @param {event} e click event, currently unused
   * @param {object} data Contains the content (index) and input valur
   */
  updateEdgeLengths(e, data) {
    const { edgeLengths, edgeValidation } = this.state;
    const re = /^[+-]?([0-9]*[.])?[0-9]+$/;

    // Check if event
    if (!!data) {
      // const value = data.value
      const value = parseFloat(data.value);
      const index = Number(data.content.split(",")[0]);
      const col = String(data.content.split(",")[1]);

      let newLengths = [...edgeLengths];
      let newValidation = [...edgeValidation];
      newValidation[index] = re.test(value);
      newLengths[index][col] = value;
      this.setState({ edgeLengths: newLengths, edgeValidation: newValidation });
      return;
    }
    // Check if figures have changed
    const { figures } = this.props;
    if (figures[cartCalibId][0]) {
      const numEdges = figures[cartCalibId][0].points.length;
      if (numEdges !== edgeLengths.length) {
        let newLengths = [];
        let newValidation = [];
        for (let i = 0; i < (numEdges); i++) {
          if (i < edgeLengths.length) {
            newLengths.push(edgeLengths[i]);
            newValidation.push(re.test(edgeLengths[i]));
            console.log("change valid", newValidation)

          } else {
            newLengths.push({});
            newValidation.push(true);
          }
        }
        this.setState({
          edgeLengths: newLengths,
          edgeValidation: newValidation
        });
      }
      return;
    } else if (edgeLengths.length > 0) {
      this.setState({ edgeLengths: [], edgeValidation: [] });
      return;
    }
  }



  /**
   * Save calibration image data and map data to backend database.
   */
  handleSaveClick() {
    const { onLabelChange, figures, alert } = this.props;

    onLabelChange({
      imageLabelData: flipY(figures, this.props.height)
    });
    alert.success("Polygon shapes saved.");
  }

  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {

    if (selected === this.state.selected) return;
    const { pushState } = this.props;

    if (!selected) {
      this.setState({ selected });
      pushState(
        state => ({
          unfinishedFigure: null
        }),
        () => this.setState({ selected })
      );
      return;
    }

    const { labels } = this.props;

    const labelIdx = labels.findIndex(label => label.id === selected);
    const type = labels[labelIdx].type;
    const color = colors[labelIdx];

    pushState(
      state => ({
        unfinishedFigure: {
          id: selected,
          color,
          type,
          points: [],
          class: labelIdx
        }
      }),
      () => this.setState({ selected })
    );

  }

  /**
   * Render the coordinate modal based on the state of the current map.
   */
  renderCropModal() {
    const { cropChangeModal } = this.state;
    const { updateValImView, reloadPage, id } = this.props;

    let imgCrop;
    if ( this.state.imgCrop.invertImHeight === 0 &&
        this.state.imgCrop.invertImWidth === 0 &&
        this.state.imgCrop.invertImXPad === 0 &&
        this.state.imgCrop.invertImYPad === 0    ) {

          imgCrop = this.props.imgCrop;
    } else {
      imgCrop = this.state.imgCrop;
    }
    // fix handlecalibclick to save crop data
    return (
      <NewCartImageInput
        modalShow={cropChangeModal}
        id={id}
        // sensor={sensor}
        imgCrop={imgCrop}
        onSubmit={updateValImView}
        reloadPage={reloadPage}
        onClose={this.toggleModal}
      />
    );
  }
  /**
   * Handle response to clicking calibrate.
   * Checks if calibration polygons are drawn, contain at least 8 points, and
   * contain the same number of points.
   * Checks if the ROI polygon is drawn.
   * Requests backend to calculate the homography matrix
   */
  async handleCalibClick() {
    const { alert } = this.props;
    if (this.props.figures[cartCalibId].length === 0) {
      alert.error("Error: image polygon not drawn.");
      return;
    }
    else if (this.props.figures[cartRoiId].length === 0){
      alert.error("Error: ROI Polygon not drawn.");
      return;
    }
    const imagePolygon = this.props.figures[cartCalibId][0].points;
    const minPoints = 4;
    const isBottomLeft = isFirstVertexMinimum(imagePolygon)

    if (imagePolygon.length !== minPoints) {
      alert.error(`Error: polygons must have ${minPoints} vertices only`);
      return;
    } else if (!isBottomLeft) {
    alert.error("Error: image polygon does not have first point as most Bottom Left");
    return;
    }

    await this.props.waitForHomography();
    alert.success("Homography matrix calculated.");
    this.forceUpdate();
    this.setState({ hasHomography: true });
  }

  /**
   * Handle response to clicking accept calibration.
   * Saves the map and image data and sets the isCalibrated boolean to true.
   */
  async handleAcceptCalib() {
    const { onLabelChange, figures, mapShapes, alert } = this.props;

    await this.props.waitForHomographyImage();
    alert.success("Homography Image created.")

    onLabelChange({
      imageLabelData: flipY(figures, this.props.height),
      mapLabelData: mapShapes,
      isCalibrated: true
    });

    this.forceUpdate();
    alert.success("Sensor has been calibrated");
    this.props.goBack();
  }

  render() {
    const {
      id,
      sensorId,
      imageUrl,
      labels,
      pushState,
      popState,
      figures,
      unfinishedFigure,
      homography,
      showImageMarkers
    } = this.props;

    //const calibrationType = true
    const {
      edgeLengths,
      edgeValidation,
      selected,
      toggles,
      hasHomography
    } = this.state;

    const managerProps = {
      title: `Metropolis Sensor Calibration Tool - ${sensorId}`,
      selected,
      onSelect: this.handleSelected,
      toggles,
      onToggle: label => {
        this.setState({
          toggles: update(toggles, {
            [label.id]: { $set: !toggles[label.id] }
          })
        });
      },
      clearImageData: this.props.clearImageData,
      toggleImageMarkers: this.props.toggleImageMarkers,
      showMapMarkers: this.props.showImageMarkers
    };
    return (
      <div>
        {hasHomography ? (
          <div className="Cartesian Calibration Manager">
            <h2>Review Calibration Statistics</h2>
            <h3>Click Edit to Adjust the Polygons</h3>
          </div>
        ) : (
          <div className="cartesianManager">
            <DrawingManager labels={labels} {...managerProps} />
          </div>
        )}
        <div className="cartesianDrawing">
          <div className="cartesianImage">
            <ImageLabeler
              imageUrl={imageUrl}
              toggles={toggles}
              labels={labels}
              pushState={pushState}
              popState={popState}
              figures={figures}
              unfinishedFigure={unfinishedFigure}
              showImageMarkers={showImageMarkers}
              unselectDrawing={this.handleSelected}
              hasHomography={hasHomography}
            />
          </div>
          <div className="cartesianTable">
            <SensorLengthInput
              id={id}
              edgeLengths={edgeLengths}
              edgeValidation={edgeValidation}
              updateEdgeLengths={this.updateEdgeLengths}
              numEdges={
                figures[cartCalibId][0] ? figures[cartCalibId][0].points.length : 0
              }
              hasHomography={hasHomography}
            />
          </div>
        </div>
        <Button
          onClick={() => {
            this.props.goBack();
          }}
          color="purple"
          floated="right"
        >
          Close
        </Button>
        <Button floated="left" color="purple" onClick={this.toggleModal} disabled={hasHomography}>
            Change Warped Image Crop
        </Button>
        {this.renderCropModal()}
        {this.props.loadingHomography ? (
          <Loader active inline="centered" />
        ) : (
          <CalibrationButton
            hasHomography={hasHomography}
            onCalibClick={this.handleCalibClick}
            onEditClick={() => this.setState({ hasHomography: false })}
          />
        )}
        {hasHomography ? (
          <CalibrationStats
            height={this.props.height}
            homography={homography}
            imagePoints={figures[cartCalibId][0].points}
            mapPoints={edgeLengths}
            onAcceptCalib={this.handleAcceptCalib}
            calibrationType={calibrationType}
          />
        ) : null}
      </div>
    );
  }
}

export default withAlert()(withLoadImageData(withImageHistory((CartesianApp))));

CartesianApp.propTypes = {
  /** Sensor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Label categories available for drawing in the image or google map */
  labels: PropTypes.arrayOf(PropTypes.object),
  /** Label categories available for drawing in the image or google map */
  drawLabels: PropTypes.arrayOf(PropTypes.object),
  /** URL of image to use for sensor calibration */
  imageUrl: PropTypes.string.isRequired,
  /** Existing image shapes to load into the image */
  labelData: PropTypes.object.isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  /**
   * Function that requests and waits for the backend to return a homography
   * matrix
   */
  waitForHomography: PropTypes.func.isRequired,
  /** Boolean value to indicate if the homography is currently being loaded */
  loadingHomography: PropTypes.bool.isRequired,
  /**
   * Function that requests and waits for the backend to return a homography
   * inverted image
   */
  waitForHomographyImage: PropTypes.func.isRequired,
  /** Padding coordinates and Crop size to load warped image*/
  imgCrop: PropTypes.object.isRequired,
  /**
   * String representation of the homography matrix that can be parsed by
   * JSON.parse
   */
  homography: PropTypes.string,
  /** Indicator if the sensor is currently calibrated or not */
  isCalibrated: PropTypes.bool.isRequired,
  /** Indicator if the sensor is currently calibrated or not */
  // isValidated: PropTypes.bool.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired,
  /** Function to update the Validation Warped Image crop */
  updateValImView: PropTypes.func.isRequired,
};
