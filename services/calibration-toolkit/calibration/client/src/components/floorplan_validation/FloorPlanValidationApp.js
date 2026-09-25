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
import { multiply } from "mathjs";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Button } from "semantic-ui-react";
// import FloorPlanValidationMap from "./FloorPlanValidationMap";
import DrawingManager from "../common/managers/DrawingManager";
import {
  convertLatLngToXYMatrix,
  convertProjectedPoint,
  flipPointY
} from "../common/mathUtils";
import { colors, floorPlanRoiId, calValId, genId } from "../common/utils";
import { withImageDisplayHistory } from "../image_drawing/HOC/ImageDisplayHistoryHOC";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";
import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import ImageDisplayer from "../image_drawing/ImageDisplayer";
import ImageLabeler from "../image_drawing/ImageLabeler";
import "./FloorPlanValidationStyles.css";



// import NewCartImageInput from "../image_drawing/NewCartImageInput";

/**
 * App displaying the calibration image and google map side-by-side for
 * validating the homography matrix calculated during calbration. The app
 * automatically projects a polyline drawn in the image to the google map using
 * the homography matrix.
 */
class FloorPlanValidationApp extends Component {
  constructor(props) {
    super(props);

    const { drawLabels } = this.props;
    const toggles = {};
    drawLabels.map(label => (toggles[label.id] = true));


    this.state = {
      imgCrop: {
        invertImXPad: 0,
        invertImYPad: 0,
        invertImWidth: 0,
        invertImHeight: 0
      },
      fpImWidth: 0,
      fpImHeight: 0,
      toggles,
      cropChangeModal: false,
    };
    this.toggleModal = this.toggleModal.bind(this);
    // this.renderCropModal = this.renderCropModal.bind(this)
    this.handleSelected = this.handleSelected.bind(this);
    this.handleFiguresUpdate = this.handleFiguresUpdate.bind(this);
    this.handleUnfinishedFigureUpdate = this.handleUnfinishedFigureUpdate.bind(
      this
    );
    this.onValidatedClick = this.onValidatedClick.bind(this);

  }

  /**
 * Toggle the modal showing the Warped Image module.
 */
    toggleModal() {
    const { cropChangeModal } = this.state;
    this.setState({ cropChangeModal: !cropChangeModal });
  }



  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {
    if (selected === this.state.selected) return;
    const { pushState, mapFigures } = this.props;
    console.log("handle sel", mapFigures)
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

    // if (
    //   (selected === roiId && !mapShapes[roiId].length) ||

    // )
    // {
    //   this.setState({ mapDrawingMode: "polygon" });
    // }

    const { drawLabels } = this.props;

    const labelIdx = drawLabels.findIndex(label => label.id === selected);
    const type = drawLabels[labelIdx].type;
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
  componentDidUpdate(prevProps,prevState) {
    const prevFigures = prevProps.figures;
    const prevUnfinishedFigure = prevProps.unfinishedFigure;
    const { figures, unfinishedFigure } = this.props;
    // console.log("check do we get here on initialize", unfinishedFigure, figures, this.props)

    // const {imgCrop} = this.state
    // if (prevState.imgCrop !== imgCrop){
    //   this.props.updateValImView(imgCrop)
    // }
    if (
      figures !== prevFigures ||
      (unfinishedFigure !== prevUnfinishedFigure && unfinishedFigure === null)
    ) {
      // console.log("check do we get here on initialize1")
      this.handleFiguresUpdate(figures);
    } else if (unfinishedFigure !== prevUnfinishedFigure) {
      this.handleUnfinishedFigureUpdate(unfinishedFigure);
      // console.log("check do we get here on initialize12")

    };

    //This updates roi polygon in validation map, however we don't use this
    //CHECK THIS
    // const {  mapFigures } = this.props;
    // console.log("app", mapFigures)

    // if (prevProps.mapShapes[roiId] !== mapShapes[roiId]) {
    //   onLabelChange({
    //     // mapData.imageLabelData: mapShapes,
    //     isValidated: false
    //   });
    // }

  }




  // old
  /**
   * Projects the new map shape based on the homography matrix when a figure
   * drawing is completed in the image plane.
   * @param {object} figures Figures drawn in the image. Contains all data (
   * category, points) required for projection to google map.
   */
  handleFiguresUpdate(figures) {

    const category = Object.keys(figures)[0];
    const invertImHeight = this.props.fpImHeight
    // console.log("initialize invIm, ", invertImHeight)
    let newMapShapes = [];
    const { mapFigures } = this.props;
    figures[category].forEach((figure, key) => {
      const newShapePoints = figure.points.map(point => {
        const { homography } = this.props;
        // console.log("homography", homography)

        const flipOrigPoint = flipPointY(point, this.props.height)
        // console.log( "initialize fop",  point, flipOrigPoint)
        const flippedPoint = flipPointY(flipOrigPoint, invertImHeight)
        // console.log( "initialize fp", flippedPoint)
        const matrixPoint = convertLatLngToXYMatrix(flippedPoint, invertImHeight);
        // console.log( "initialize fp mp", matrixPoint)

        const projectedPoint = convertProjectedPoint(
          multiply(homography, matrixPoint)
        );

        const imagePoint = flipPointY(projectedPoint, invertImHeight)

        // console.log( "initialize fp pp", imagePoint)

        // const flipProjPoint = flipPointY(projectedPoint, invertImHeight)

        // console.log("1", flippedPoint)
        // const paddedPoint = padPointY(flippedPoint,0)
        // console.log("2", paddedPoint)
        return imagePoint;
          // return point;
        });
      newMapShapes.push({
          id: figure.id,
          points: newShapePoints,
          type: figure.type,
          });
    });
    newMapShapes = update(mapFigures, {
      [category]: {
        $set: newMapShapes
      }
    });
    // this.props.handleDrawingChange(newMapShapes);
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
    const invertImHeight = this.props.fpImHeight
    // console.log("initialize invIm, ", invertImHeight, this.props)
    let newMapShapes = {};
    const newShapePoints = unfinishedFigure.points.map(point => {
      const { homography } = this.props;
      // console.log("initial ", homography, point, this.props.height, invertImHeight )

      const flipOrigPoint = flipPointY(point, this.props.height)
      // console.log( "initialize fop",  point, flipOrigPoint)
      const flippedPoint = flipPointY(flipOrigPoint, invertImHeight)
      // console.log( "initialize fp", flippedPoint)
      const matrixPoint = convertLatLngToXYMatrix(flippedPoint, invertImHeight);
      // console.log( "initialize fp mp", matrixPoint)

      const projectedPoint = convertProjectedPoint(
        multiply(homography, matrixPoint)
      );

      const imagePoint = flipPointY(projectedPoint, invertImHeight)

      // console.log( "initialize fp pp", imagePoint)

      // const flipProjPoint = flipPointY(projectedPoint, invertImHeight)

      // console.log("1", flippedPoint)
      // const paddedPoint = padPointY(flippedPoint,0)
      // console.log("2", paddedPoint)
      return imagePoint;
      // return point;
    });

    const { mapFigures } = this.props;
    if (mapFigures){
        // console.log(mapShapes.length)
        newMapShapes = update(mapFigures, {
          [category]: {
            $set: [{
              id: unfinishedFigure.id || genId(),
              points: newShapePoints,
              type: unfinishedFigure.type
            }]
          }
        });
        this.props.handleDrawingChange(newMapShapes);
    }
  }

  /**
   * Handle action when calibration is validated. Double checks the ROI is
   * drawn.
   */
  onValidatedClick() {
    const { mapFigures, alert, handleValidated, goBack } = this.props;
    if (mapFigures[floorPlanRoiId].length === 0) {
      alert.error("Error: ROI polygon not drawn.");
      return;
    }
    handleValidated();
    goBack();
  }



  render() {
    const {
      sensorId,
      mapFigures,
      mapUnfinishedFigure,
      imageUrl,
      floorPlanImageUrl,
      labels,
      mapLabels,
      drawLabels,
      pushState,
      popState,
      figures,
      unfinishedFigure,
      showImageMarkers
    } = this.props;
    const { selected,  toggles } = this.state;
    // this.setState({
    //               fpImHeight: fpImHeight,
    //               fpImWidth: fpImWidth
    // });
    console.log ("fpva", drawLabels)

    const managerProps = {
      title: `Metropolis Calibration FloorPlan Validation Tool - ${sensorId}`,
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
    console.log("initialize m , ", mapFigures, mapLabels, labels)
    console.log("initialize, ", figures)
    console.log("height val", this.props.height)

    return (
      <div>
        <div className="cartesianValidationManager">
          <DrawingManager labels={drawLabels} {...managerProps} />
        </div>
        <div className="cartesianValidationDrawing">
          <div className="cartesianValidationImage">
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
          <div className="cartesianValidationImage2">
          <ImageDisplayer
              toggles={toggles}
              showImageMarkers={showImageMarkers}
              imageUrl={floorPlanImageUrl}
              labels={labels}
              // pushState={pushState}
              // popState={popState}
              figures={mapFigures}
              unfinishedFigure={mapUnfinishedFigure}
              unselectDrawing={this.handleSelected}
              // handleDrawingChange={this.props.handleDrawingChange}
            />
          </div>
          {/* <div className="cartesianValidationMap">
            <cartesianValidationMap
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
          </div> */}
        </div>
        <Button
          color="green"
          onClick={() => {
            this.onValidatedClick();
          }}
        >
          Validate Calibration
        </Button>
        {/* <Button floated="left" color="purple" onClick={this.toggleModal}>
          Change Warped Image Crop
        </Button>
          {this.renderCropModal()} */}
        <Button onClick={this.props.goBack} color="purple" floated="right">
          Close
        </Button>
      </div>
    );
  }
}

export default withAlert()(
  withLoadImageData(withImageHistory(withImageDisplayHistory(FloorPlanValidationApp)))
);

FloorPlanValidationApp.propTypes = {
  /** Name of the sensor */
  sensorId: PropTypes.string,
  /** ID number of the sensor*/
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** floorPlan image height*/
  fpImHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Type of sensor. This is used when saving the moved map location */
  type: PropTypes.string.isRequired,
  /** Label categories available for drawing in the image or google map */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Draw Label categories available for drawing in the image or google map */
  drawLabels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Draw Label categories available for drawing in the image or google map */
  mapLabels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Google map API key */
  API_KEY: PropTypes.string.isRequired,
  /** Lat/Lng coordinate to load as google map center */
  // imgCrop: PropTypes.object.isRequired,
  /** Existing map shapes to load into google map */
  // mapLabels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** URL of image to use for sensor calibration */
  imageUrl: PropTypes.string.isRequired,
  /** URL of inverted image to use for sensor calibration */
  floorPlanImageUrl: PropTypes.string.isRequired,
  /** Existing image shapes to load into the image */
  labelData: PropTypes.object.isRequired,
  /** Existing mapped image shapes to load into the image */
  mapLabelData: PropTypes.object.isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  /** Matrix representing the homography calculated during calibration */
  homography: PropTypes.any.isRequired,
  /** Handle the action of clicking the validate sensor button */
  handleValidated: PropTypes.func.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired,
  /** Function to update the Validation Warped Image crop */
  updateValImView: PropTypes.func.isRequired,
};
