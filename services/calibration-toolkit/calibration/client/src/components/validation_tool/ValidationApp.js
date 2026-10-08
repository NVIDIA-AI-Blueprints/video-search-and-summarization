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
import { Button } from "semantic-ui-react";
import { withAlert } from "react-alert";

import ImageLabeler from "../image_drawing/ImageLabeler";
import ValidationMap from "./ValidationMap";
import DrawingManager from "../common/managers/DrawingManager";
import { colors, roiId, calValId, tripwireId, tripDirId } from "../common/utils";
import { withMapHistory } from "../map_drawing/HOC/MapLabelingHistoryHOC";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";
import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import {
  convertLatLngToXYMatrix,
  convertProjectedPoint
} from "../common/mathUtils";

import "./ValidationStyles.css";

import { multiply } from "mathjs";

/**
 * App displaying the calibration image and google map side-by-side for
 * validating the homography matrix calculated during calbration. The app
 * automatically projects a polyline drawn in the image to the google map using
 * the homography matrix.
 */
class ValidationApp extends Component {
  constructor(props) {
    super(props);

    const { labels } = this.props;
    const toggles = {};
    labels.map(label => (toggles[label.id] = true));

    this.state = { toggles };
    this.handleSelected = this.handleSelected.bind(this);
    this.handleFiguresUpdate = this.handleFiguresUpdate.bind(this);
    this.handleUnfinishedFigureUpdate = this.handleUnfinishedFigureUpdate.bind(
      this
    );
    this.onValidatedClick = this.onValidatedClick.bind(this);
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


    //TODO only allows one polygon,tripwire, direction
    if (selected === roiId && !mapShapes[roiId].length) {
      this.setState({ mapDrawingMode: "polygon" });
    }

    const { labels } = this.props;

    const labelIdx = labels.findIndex(label => label.id === selected);
    const type = labels[labelIdx].type;
    const color = colors[labelIdx];

    if (selected === calValId) {
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
      return;
    }
    this.setState({ selected });
  }

  /**
   * If the figure is being drawn in the image, recalculate the polyline to
   * be drawn in the map. Also pushes changes in the ROI polygon to the backend.
   * @param {object} prevProps Previous props before the update.
   */
  componentDidUpdate(prevProps) {
    const prevFigures = prevProps.figures;
    const prevUnfinishedFigure = prevProps.unfinishedFigure;
    const { figures, unfinishedFigure } = this.props;
    if (
      figures !== prevFigures ||
      (unfinishedFigure !== prevUnfinishedFigure && unfinishedFigure === null)
    ) {
      this.handleFiguresUpdate(figures);
    } else if (unfinishedFigure !== prevUnfinishedFigure) {
      this.handleUnfinishedFigureUpdate(unfinishedFigure);
    }

    const { onLabelChange, mapShapes } = this.props;
    if (prevProps.mapShapes[roiId] !== mapShapes[roiId] || prevProps.mapShapes[tripwireId] !== mapShapes[tripwireId] || prevProps.mapShapes[tripDirId] !== mapShapes[tripDirId]  ) {
      onLabelChange({
        mapLabelData: mapShapes,
        isValidated: false
      });

    }
  }

  /**
   * Projects the new map shape based on the homography matrix when a figure
   * drawing is completed in the image plane.
   * @param {object} figures Figures drawn in the image. Contains all data (
   * category, points) required for projection to google map.
   */
  handleFiguresUpdate(figures) {
    const category = Object.keys(figures)[0];
    let newMapShapes = [];
    const { mapShapes } = this.props;
    figures[category].forEach((figure, key) => {
      const newShapePoints = figure.points.map(point => {
        const { homography } = this.props;
        const matrixPoint = convertLatLngToXYMatrix(point, this.props.height);
        const projectedPoint = convertProjectedPoint(
          multiply(homography, matrixPoint)
        );
        return projectedPoint;
      });
      newMapShapes.push({ id: key, category, points: newShapePoints });
    });
    newMapShapes = update(mapShapes, {
      [category]: {
        $set: newMapShapes
      }
    });
    this.props.handleDrawingChange(newMapShapes);
  }

  /**
   * Projects the new map shape ased on the homography matrix when an
   * unfinished figure is updated in the image plane.
   * @param {object} unfinishedFigure Unfinished figure currently being drawn in
   * the image. Contains all data (category, points) required for projetion to
   * the google map.
   */
  handleUnfinishedFigureUpdate(unfinishedFigure) {
    const category = unfinishedFigure.id;
    let newMapShapes = [];
    const newShapePoints = unfinishedFigure.points.map(point => {
      const { homography } = this.props;
      const matrixPoint = convertLatLngToXYMatrix(point, this.props.height);
      const projectedPoint = convertProjectedPoint(
        multiply(homography, matrixPoint)
      );
      return projectedPoint;
    });
    const { mapShapes } = this.props;
    newMapShapes = update(mapShapes, {
      [category]: {
        $push: [{ id: mapShapes.length, category, points: newShapePoints }]
      }
    });
    this.props.handleDrawingChange(newMapShapes);
  }

  /**
   * Handle action when calibration is validated. Double checks the ROI is
   * drawn.
   */
  onValidatedClick() {
    const { mapShapes, alert, handleValidated, goBack } = this.props;

    //put in checks for tripwire and direction
    if (mapShapes[roiId].length === 0) {
      alert.error("Error: ROI polygon not drawn.");
      return;
    }
    else if(mapShapes["0003"].length === 0){
      alert.error("Error: Calibration polygon not drawn.");
      return;
    }
    handleValidated();
    goBack();
  }

  render() {
    const {
      sensorId,
      id,
      type,
      API_KEY,
      mapCenter,
      mapShapes,
      mapZoom,
      imageUrl,
      labels,
      pushState,
      popState,
      figures,
      reloadPage,
      unfinishedFigure,
      updateMapView,
      showMapMarkers,
      showImageMarkers
    } = this.props;

    const { selected, mapDrawingMode, toggles } = this.state;

    const managerProps = {
      title: `Metropolis Calibration Validation Tool - ${sensorId}`,
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
      onFormChange: (labelId, newValue) =>
        pushState(state => ({
          figures: update(figures, { [labelId]: { $set: newValue } })
        })),
      labelData: figures,
      clearMapData: this.props.clearMapData,
      clearImageData: this.props.clearImageData,
      toggleMapMarkers: this.props.toggleMapMarkers,
      toggleImageMarkers: this.props.toggleImageMarkers,
      showMapMarkers: this.props.showMapMarkers
    };

    return (
      <div>
        <div className="validationManager">
          <DrawingManager labels={labels} {...managerProps} />
        </div>
        <div className="validationDrawing">
          <div className="validationImage">
            <ImageLabeler
              toggles={toggles}
              showImageMarkers={showImageMarkers}
              imageUrl={imageUrl}
              labels={labels}
              pushState={pushState}
              popState={popState}
              figures={figures}
              unfinishedFigure={unfinishedFigure}
              unselectDrawing={this.handleSelected}
            />
          </div>
          <div className="validationMap">
            <ValidationMap
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
              reloadPage={reloadPage}
              selected={selected}
              unselectDrawing={this.handleSelected}
            />
          </div>
        </div>
        <Button
          color="green"
          onClick={() => {
            this.onValidatedClick();
          }}
        >
          Validate Calibration
        </Button>
        <Button onClick={this.props.goBack} color="purple" floated="right">
          Close
        </Button>
      </div>
    );
  }
}

export default withAlert()(
  withLoadImageData(withImageHistory(withMapHistory(ValidationApp)))
);

ValidationApp.propTypes = {
  /** Name of the sensor */
  sensorId: PropTypes.string,
  /** ID number of the sensor*/
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Type of sensor. This is used when saving the moved map location */
  type: PropTypes.string.isRequired,
  /** Label categories available for drawing in the image or google map */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Label categories available for drawing in the image or google map */
  drawLabels: PropTypes.arrayOf(PropTypes.object).isRequired,
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
  /** Matrix representing the homography calculated during calibration */
  homography: PropTypes.any.isRequired,
  /** Handle the action of clicking the validate sensor button */
  handleValidated: PropTypes.func.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired
};
