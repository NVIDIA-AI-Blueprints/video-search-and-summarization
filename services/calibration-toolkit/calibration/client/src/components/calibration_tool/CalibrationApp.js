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
import CalibrationButton from "../calibration/CalibrationButton";
import CalibrationMap from "./CalibrationMap";
import DrawingManager from "../common/managers/DrawingManager";
import { colors, calibId, roiId, tripwireId, tripDirId } from "../common/utils";
import { withMapHistory } from "../map_drawing/HOC/MapLabelingHistoryHOC";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";
import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import { flipY } from "../common/mathUtils";
import CalibrationStats from "../calibration/CalibrationStats";

import "./CalibrationStyles.css";

/**
 * App displaying the loaded image and map information side-by-side.
 * Enables the drawing of corresponding calibration polygons.
 */
class CalibrationApp extends Component {
  constructor(props) {
    super(props);

    const { labels } = this.props;

    const toggles = {};
    labels.map(label => (toggles[label.id] = true));

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
    const { onLabelChange, figures, mapShapes } = this.props;

    if (prevProps.figures !== figures || prevProps.mapShapes !== mapShapes) {
      onLabelChange({
        imageLabelData: flipY(figures, this.props.height),
        mapLabelData: mapShapes,
        isCalibrated: false,
        isValidated: false
      });
    }
  }

  /**
   * Save calibration image data and map data to backend database.
   */
  handleSaveClick() {
    const { onLabelChange, figures, mapShapes, alert } = this.props;

    onLabelChange({
      imageLabelData: flipY(figures, this.props.height),
      mapLabelData: mapShapes
    });
    alert.success("Polygon shapes saved.");
  }

  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {
    if (selected === this.state.selected) return;
    const { pushState, mapShapes } = this.props;

    if (!selected) {
      this.setState({ selected });
      this.setState({ mapDrawingMode: null });
      pushState(
        state => ({
          unfinishedFigure: null
        }),
        () => this.setState({ selected })
      );
      return;
    }

    if (
      // this may be limiting the roi id to one polygon
      (selected === calibId && !mapShapes[calibId].length) ||
      (selected === roiId && !mapShapes[roiId].length) ||
      (selected === tripwireId && !mapShapes[tripwireId].length) ||
      (selected === tripDirId && !mapShapes[tripDirId].length)


    ) {
      this.setState({ mapDrawingMode: "polygon" });
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
          points: []
        }
      }),
      () => this.setState({ selected })
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
    if (this.props.figures[calibId].length === 0) {
      alert.error("Error: image polygon not drawn.");
      return;
    } else if (this.props.mapShapes[calibId].length === 0) {
      alert.error("Error: map polygon not drawn.");
      return;
    } else if (this.props.mapShapes[roiId].length === 0) {
      alert.error("Error: ROI polygon not drawn.");
      return;
    }
    //TODO: add tripwire/direction pair check and number of point check
    const imagePolygon = this.props.figures[calibId][0].points;
    const mapPolygon = this.props.mapShapes[calibId][0].points;
    const minPoints = 8;
    if (imagePolygon.length < minPoints || mapPolygon.length < minPoints) {
      alert.error(`Error: polygons must have at least ${minPoints} vertices`);
      return;
    } else if (imagePolygon.length !== mapPolygon.length) {
      alert.error("Error: polygons not same size");
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

    onLabelChange({
      imageLabelData: flipY(figures, this.props.height),
      mapLabelData: mapShapes,
      isCalibrated: true
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
      mapShapes,
      imageUrl,
      labels,
      drawLabels,
      pushState,
      popState,
      figures,
      unfinishedFigure,
      homography,
      mapZoom,
      updateMapView,
      showMapMarkers,
      showImageMarkers
    } = this.props;

    const { selected, toggles, mapDrawingMode, hasHomography } = this.state;

    const managerProps = {
      title: `Metropolis Camera Calibration Tool - ${sensorId}`,
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
          <div className="calibrationImage">
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
          <div className="calibrationMap">
            <CalibrationMap
              id={id}
              type={type}
              toggles={toggles}
              showMapMarkers={showMapMarkers}
              apiKey={API_KEY}
              center={mapCenter}
              shapes={mapShapes}
              mapZoom={mapZoom}
              updateMapView={updateMapView}
              drawingMode={mapDrawingMode}
              handleDrawingChange={this.props.handleDrawingChange}
              reloadPage={this.props.reloadPage}
              selected={selected}
              unselectDrawing={this.handleSelected}
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
        {this.props.loadingHomography ? (
          <Loader active inline="centered" />
        ) : (
          <CalibrationButton
            hasHomography={hasHomography}
            onCalibClick={this.handleCalibClick}
            onEditClick={() => this.setState({ hasHomography: false })}
          />
        )}
        {hasHomography ? 
          (
          <CalibrationStats
            height={this.props.height}
            homography={homography}
            imagePoints={this.props.figures[calibId][0]?.points}
            mapPoints={this.props.mapShapes[calibId][0]?.points}
            calibrationType={this.props.calibrationType}
            onAcceptCalib={this.handleAcceptCalib}
          />
        ) : null}
      </div>
    );
  }
}

export default withAlert()(
  withLoadImageData(withImageHistory(withMapHistory(CalibrationApp)))
);

CalibrationApp.propTypes = {
  /** Sensor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Type of sensor. This is used when saving the moved map location */
  type: PropTypes.string.isRequired,
  /** Label categories available for drawing in the image or google map */
  labels: PropTypes.arrayOf(PropTypes.object),
  /** Label categories available for drawing in the image or google map */
  drawLabels: PropTypes.arrayOf(PropTypes.object),
  /** Google map API key */
  API_KEY: PropTypes.string.isRequired,
  /** Lat/Lng coordinate to load as google map center */
  mapCenter: PropTypes.object.isRequired,
  /** Zoom level to use when loading the google map */
  mapZoom: PropTypes.number.isRequired,
  /** Existing map shapes to load into google map */
  mapLabels: PropTypes.object.isRequired,
  /** URL of image to use for sensor calibration */
  imageUrl: PropTypes.string.isRequired,
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
  /** Sensor ID */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** calibration type */
  calibrationType: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
  .isRequired,

};
