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

import update from "immutability-helper";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Button, Loader } from "semantic-ui-react";
// import MapLabeler from "../image_drawing/MapLabeler";
import CalibrationButton from "../calibration/CalibrationButton";
import CalibrationStats from "../calibration/CalibrationStats";
import DrawingManager from "../common/managers/DrawingManager";
import { flipYLabels } from "../common/mathUtils";
import { colors, floorPlanCalibId, floorPlanCalibMapId, roiId, tripDirId, tripwireId } from "../common/utils";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";
import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import ImageLabeler from "../image_drawing/ImageLabeler";
import MapLabeler from "../image_drawing/MapLabeler";
import "./ImageCalibrationStyles.css";



/**
 * App displaying the loaded image and map information side-by-side.
 * Enables the drawing of corresponding calibration polygons.
 */
class ImageCalibrationApp extends Component {
  constructor(props) {
    super(props);

    const { drawLabels } = this.props;

    const toggles = {};
    drawLabels.map(label => (toggles[label.id] = true));

    this.state = {
      hasHomography: this.props.isCalibrated,
      selected: null,
      mapDrawingMode: null,
      toggles
    };

    this.handleSelected = this.handleSelected.bind(this);
    this.handleCalibClick = this.handleCalibClick.bind(this);
    this.handleSaveClick = this.handleSaveClick.bind(this);
    this.handleAcceptCalib = this.handleAcceptCalib.bind(this);
  }

  /**
   * Checks if the figures or map shapes have changed, and save the changes to
   * the backend.
   * @param {object} prevProps Previous props before the update
   */
  componentDidUpdate(prevProps) {
    const { onLabelChange, figures, drawLabels } = this.props;
    console.log("fpca update", figures,  this.props)
    if (prevProps.figures !== figures ) {
      onLabelChange({
        imageLabelData: flipYLabels(figures, drawLabels),
        isCalibrated: false,
        isValidated: false
      });
    }
  }

  /**
   * Save calibration image data and map data to backend database.
   */
  handleSaveClick() {
    const { onLabelChange, figures, alert,drawLabels } = this.props;

    onLabelChange({
      imageLabelData: flipYLabels(figures, drawLabels),
      // mapLabelData: flipYLabels(mapShapes,drawLabels)
    });
    alert.success("Polygon shapes saved.");
  }

  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {
    console.log("fpca sel", selected)
    if (selected === this.state.selected) return;
    const { pushState, figures } = this.props;
    console.log("fpca", figures)
    if (!selected) {
      this.setState({ selected });
      this.setState({ mapDrawingMode: null });
      pushState(
        state => ({
          unfinishedFigure: null
        }),
        () => this.setState({ selected })
      );
      // pushMapState(
      //   state => ({
      //     unfinishedFigure: null
      //   }),
      //   () => this.setState({ selected })
      // );
      return;
    }

    if (
      // this may be limiting the roi id to one polygon
      // (selected === floorPlanCalibId && !mapShapes[floorPlanCalibMapId].length) ||
      (selected === roiId && !figures[roiId].length) ||
      (selected === tripwireId && !figures[tripwireId].length) ||
      (selected === tripDirId && !figures[tripDirId].length)


    ) {
      this.setState({ mapDrawingMode: "polygon" });
      console.log("fpca, map mode", this.state.mapDrawingMode)

    }

    const { drawLabels } = this.props;

    const labelIdx = drawLabels.findIndex(label => label.id === selected);
    const type = drawLabels[labelIdx].type;
    const color = colors[labelIdx];
    console.log("fpca,", labelIdx, type, color)
    pushState(
      state => ({
        unfinishedFigure: {
          id: selected,
          color,
          type,
          points: [],
          class: selected
        }
      }),
      () => this.setState({ selected })
    );

    if (
      selected === roiId ||
      selected === tripwireId ||
      selected === tripDirId 
      // selected === floorPlanCalibMapId
    ) {
      console.log("fpca 12")
      pushState(
        state => ({
          unfinishedFigure: {
            id: selected,
            color,
            type,
            points: [],
            class: selected
          }
        }),
        () => this.setState({ selected })
      );
    }

  }

  /**
   * Handle response to clicking calibrate.
   * Checks if calibration polygons are drawn, contain at least 8 points, and
   * contain the same number of points.
   * Checks if the ROI polygon is drawn.
   * Requests backend to calculate the homography matrix
   */
  async handleCalibClick() {
    const { onLabelChange, figures, alert,drawLabels } = this.props;

    onLabelChange({
      imageLabelData: flipYLabels(figures, drawLabels),
      isCalibrated: true,
      isValidated: true
    });
    // if (this.props.figures[floorPlanCalibId].length === 0) {
    //   alert.error("Error: image polygon not drawn.");
    //   return;
    // } else if (this.props.mapShapes[floorPlanCalibMapId].length === 0) {
    //   alert.error("Error: map polygon not drawn.");
    //   return;
    // } else 
    // if (this.props.figures[roiId].length === 0) {
    //   alert.error("Error: ROI polygon not drawn.");
    //   return;
    // }
    //TODO: add tripwire/direction pair check and number of point check
    // const imagePolygon = this.props.figures[floorPlanCalibId][0].points;
    // const mapPolygon = this.props.mapShapes[floorPlanCalibMapId][0].points;
    // const minPoints = 8;
    // if (imagePolygon.length < minPoints || mapPolygon.length < minPoints) {
    //   alert.error(`Error: polygons must have at least ${minPoints} vertices`);
    //   return;
    // } else if (imagePolygon.length !== mapPolygon.length) {
    //   alert.error("Error: polygons not same size");
    //   return;
    // }

    // await this.props.waitForHomography();
    alert.success("Drawings Saved.");
    this.forceUpdate();
    this.setState({ hasHomography: true });
  }

  /**
   * Handle response to clicking accept calibration.
   * Saves the map and image data and sets the isCalibrated boolean to true.
   */
  async handleAcceptCalib() {
    const { onLabelChange, figures, alert,drawLabels } = this.props;

    onLabelChange({
      imageLabelData: flipYLabels(figures, drawLabels),
      isCalibrated: true,
      isValidated: true
    });

    alert.success("Sensor has been calibrated");
    this.props.goBack();
  }

  render() {
    const {
      sensorId,
      id,
      type,
      API_KEY,
      mapCenter,
      imageUrl,
      floorPlanImageUrl,
      labels,
      pushState,
      popState,
      figures,
      unfinishedFigure,
      homography,
      mapZoom,
      updateMapView,
      showMapMarkers,
      showImageMarkers,
      mapLabels,
      drawLabels
    } = this.props;
    console.log("fpca shapes  ", labels, mapLabels )
    console.log("fpca", this.props)

    const { selected, toggles, mapDrawingMode, hasHomography } = this.state;

    const managerProps = {
      title: `Metropolis Camera Floor Plan Calibration Tool - ${sensorId}`,
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
      clearMapData: this.props.clearMapData,
      clearImageData: this.props.clearImageData,
      toggleMapMarkers: this.props.toggleMapMarkers,
      toggleImageMarkers: this.props.toggleImageMarkers,
      showMapMarkers: this.props.showMapMarkers
    };
    console.log(figures,floorPlanCalibId)

    return (
      <div>
        {hasHomography ? (
          <div className="Calibration Manager">
            <h2>Review Calibration Statistics</h2>
            <h3>Click Edit to Adjust the Polygons</h3>
          </div>
        ) : (
          <div className="calibrationManager">
            <DrawingManager labels={drawLabels} {...managerProps} />
          </div>
        )}
        <div className="calibrationDrawing">
      
          {/* <div className="calibrationImage"> */}
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
          {/* </div> */}

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

        {this.props.loadingHomography ? (
          <Loader active inline="centered" />
        ) : (
          <CalibrationButton
            hasHomography={hasHomography}
            onCalibClick={this.handleCalibClick}
            onEditClick={() => this.setState({ hasHomography: false })}
          />
        )}
        {/* {hasHomography ? (
          <CalibrationStats
            height={this.props.height}
            homography={homography}
            imagePoints={figures[floorPlanCalibId][0].points}
            mapPoints={figures[floorPlanCalibMapId][0].points}
            onAcceptCalib={this.handleAcceptCalib}
          />
        ) : null} */}
      </div>
    );
  }
}

export default withAlert()(
  withLoadImageData(withImageHistory(ImageCalibrationApp))
);

ImageCalibrationApp.propTypes = {
  /** Sensor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Type of sensor. This is used when saving the moved map location */
  type: PropTypes.string.isRequired,
  /** Label categories available for drawing in the image or google map */
  labels: PropTypes.arrayOf(PropTypes.object),
  /** map Label categories available for drawing in the image or google map */
  mapLabels: PropTypes.arrayOf(PropTypes.object),
  /** Google map API key */
  API_KEY: PropTypes.string.isRequired,
  /** Lat/Lng coordinate to load as google map center */
  mapCenter: PropTypes.object.isRequired,
  /** Zoom level to use when loading the google map */
  mapZoom: PropTypes.number.isRequired,
  /** Existing map shapes to load into google map */
  mapLabelData: PropTypes.object.isRequired,
  /** URL of image to use for sensor calibration */
  imageUrl: PropTypes.string.isRequired,
  /** URL of floorplan image to use for sensor calibration */
  floorPlanImageUrl: PropTypes.string.isRequired,
  /** Existing image shapes to load into the image */
  labelData: PropTypes.object.isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  /** Function that requests and waits for the backend to return a homography
   *  matrix */
  waitForHomography: PropTypes.func.isRequired,
  /** Boolean value to indicate if the homography is currently being loaded */
  loadingHomography: PropTypes.bool.isRequired,
  /** String representation of the homography matrix that can be parsed by
   * JSON.parse
   */
  homography: PropTypes.string,
  /** Indicator if the sensor is currently calibrated or not */
  isCalibrated: PropTypes.bool.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired,

  // fpHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
};
