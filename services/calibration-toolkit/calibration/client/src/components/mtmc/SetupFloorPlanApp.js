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
import SensorManager from "../common/managers/SensorManager";
import { flipYLabels } from "../common/mathUtils";
import { colors, floorPlanCalibId, floorPlanCalibMapId, roiId, tripDirId, tripwireId } from "../common/utils";
import { withSetupFloorPlanImageHistory } from "../image_drawing/HOC/SetupFloorPlanLabelHistoryHOC";
import { withImageHistory } from "../image_drawing/HOC/ImageLabelHistoryHOC";
import { withLoadImageData } from "../image_drawing/HOC/LoadImageDataHOC";
import ImageLabeler from "../image_drawing/ImageLabeler";
import MapLabeler from "../image_drawing/MapLabeler";
import "./SetupFloorPlanStyles.css";
// import MenuBar from '../common/Menubar'
import ScaleFactorInput from "../image_drawing/ScaleFactorInput";


/**
 * App displaying the loaded image and map information side-by-side.
 * Enables the drawing of corresponding calibration polygons.
 */
class SetupFloorPlanApp extends Component {
  constructor(props) {
    super(props);

    const { drawLabels, sensorList} = this.props;

    const toggles = {};
    drawLabels.map(label => (toggles[label.id] = true));

    this.state = {
      // hasHomography: this.props.isCalibrated,
      selected: null,
      mapDrawingMode: null,
      scaleFactor: 1.0,
      toggles,
      scaleFactorChangeModal: false,
    };

    this.handleSelected = this.handleSelected.bind(this);
    this.handleCalibClick = this.handleCalibClick.bind(this);
    this.handleSaveClick = this.handleSaveClick.bind(this);
    this.handleAcceptCalib = this.handleAcceptCalib.bind(this);
    this.toggleModal = this.toggleModal.bind(this);
    this.renderScaleFactorModal = this.renderScaleFactorModal.bind(this)

  }

  /**
   * Checks if the figures or map shapes have changed, and save the changes to
   * the backend.
   * @param {object} prevProps Previous props before the update
   */
  componentDidUpdate(prevProps) {
    const { onLabelChange, figures, mapShapes, drawLabels } = this.props;
    console.log("fpca update", figures, mapShapes, this.props)
    if (prevProps.figures !== figures || prevProps.mapShapes !== mapShapes) {
      onLabelChange({
        imageLabelData: flipYLabels(figures, drawLabels),
        mapLabelData: flipYLabels(mapShapes, drawLabels),
        isCalibrated: false,
        isValidated: false
      });
    }
  }



  /**
   * Save calibration image data and map data to backend database.
   */
  handleSaveClick() {
    const { onLabelChange, figures, mapShapes, alert,drawLabels } = this.props;

    onLabelChange({
      imageLabelData: flipYLabels(figures, drawLabels),
      mapLabelData: flipYLabels(mapShapes,drawLabels)
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
    const { pushState, pushMapState, mapShapes } = this.props;
    console.log("fpca", mapShapes)
    if (!selected) {
      this.setState({ selected });
      this.setState({ mapDrawingMode: null });
      pushState(
        state => ({
          unfinishedFigure: null
        }),
        () => this.setState({ selected })
      );
      pushMapState(
        state => ({
          unfinishedFigure: null
        }),
        () => this.setState({ selected })
      );
      return;
    }

    if (
      // this may be limiting the roi id to one polygon
      (selected === floorPlanCalibId && !mapShapes[floorPlanCalibMapId].length) ||
      (selected === roiId && !mapShapes[roiId].length) ||
      (selected === tripwireId && !mapShapes[tripwireId].length) ||
      (selected === tripDirId && !mapShapes[tripDirId].length)


    ) {
      this.setState({ mapDrawingMode: "polygon" });
      console.log("fpca, map mode", this.state.mapDrawingMode)

    }

    const { sensors } = this.props;

    const labelIdx = sensors.findIndex(label => label.id === selected);
    const dbIdx = sensors[labelIdx].dbId;
    const type = sensors[labelIdx].type;
    const color = colors[labelIdx];
    console.log("fpca,", sensors, labelIdx, type, color)
    pushState(
      state => ({
        unfinishedFigure: {
          id: selected,
          dbId: dbIdx,
          color,
          type,
          points: [],
          class: selected
        }
      }),
      () => this.setState({ selected })
    );

    if (
      selected
    ) {
      console.log("fpca 12")
      pushMapState(
        state => ({
          unfinishedMapFigure: {
            id: selected,
            dbId: dbIdx,
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
    const { alert } = this.props;
    if (this.props.figures[floorPlanCalibId].length === 0) {
      alert.error("Error: image polygon not drawn.");
      return;
    } else if (this.props.mapShapes[floorPlanCalibMapId].length === 0) {
      alert.error("Error: map polygon not drawn.");
      return;
    } else if (this.props.mapShapes[roiId].length === 0) {
      alert.error("Error: ROI polygon not drawn.");
      return;
    }
    //TODO: add tripwire/direction pair check and number of point check
    const imagePolygon = this.props.figures[floorPlanCalibId][0].points;
    const mapPolygon = this.props.mapShapes[floorPlanCalibMapId][0].points;
    const minPoints = 8;
    if (imagePolygon.length < minPoints || mapPolygon.length < minPoints) {
      alert.error(`Error: polygons must have at least ${minPoints} vertices`);
      return;
    } else if (imagePolygon.length !== mapPolygon.length) {
      alert.error("Error: polygons not same size");
      return;
    }

    // await this.props.waitForHomography();
    // alert.success("Homography matrix calculated.");
    // this.forceUpdate();
    // this.setState({ hasHomography: true });
  }

      /**
 * Toggle the modal showing the Warped Image module.
 */
   toggleModal() {
    const { scaleFactorChangeModal } = this.state;
    this.setState({ scaleFactorChangeModal: !scaleFactorChangeModal });
  }
    /**
   * Render the coordinate modal based on the state of the current map.
   */
    renderScaleFactorModal() {
      const { scaleFactorChangeModal } = this.state;
      const { updateScaleFactor, reloadPage, id } = this.props;

      let scaleFactor;
      if ( this.state.scaleFactor === 0    ) {

            scaleFactor = this.props.scaleFactor;
      } else {
        scaleFactor = this.state.scaleFactor;
      }
      // fix handlecalibclick to save crop data
      return (
        <ScaleFactorInput
          modalShow={scaleFactorChangeModal}
          id={id}
          // sensor={sensor}
          scaleFactor={scaleFactor}
          onSubmit={updateScaleFactor}
          reloadPage={reloadPage}
          onClose={this.toggleModal}
        />
      );
    }
    /**

  /**
   * Handle response to clicking accept calibration.
   * Saves the map and image data and sets the isCalibrated boolean to true.
   */
  async handleAcceptCalib() {
    const { onLabelChange, figures, mapShapes, alert,drawLabels } = this.props;

    onLabelChange({
      imageLabelData: flipYLabels(figures, drawLabels),
      mapLabelData: flipYLabels(mapShapes,drawLabels),
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
      // floorPlanImageUrl,
      labels,
      pushState,
      popState,
      pushMapState,
      popMapState,
      figures,
      unfinishedFigure,
      unfinishedMapFigure,
      // homography,
      mapZoom,
      updateMapView,
      showMapMarkers,
      showImageMarkers,
      mapLabels,
      drawLabels,
      sensors
      // sensorList
    } = this.props;
    console.log(" do i get to sfpa")
    console.log("sfpa mapshapes  ", mapShapes, labels, mapLabels, sensors )
    console.log("sfpa", this.props)

    const { selected, toggles, mapDrawingMode } = this.state;

    const managerProps = {
      title: `Metropolis Sensor Floor Plan Calibration Tool - ${sensorId}`,
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
      <React.Fragment>
        {/* <SideBar></SideBar> */}
        {/* <MenuBar> */}
        <div>
          {(
          //   <div className="calibrationManager">
          //     <DrawingManager labels={drawLabels} {...managerProps} />
          //     {/* <SensorManager labels={drawLabels} {...managerProps} /> */}
          //   </div>

          // )}
          // {(
            <div className="calibrationManager">
              {/* <DrawingManager labels={drawLabels} {...managerProps} /> */}
              <SensorManager labels={sensors} {...managerProps} />
            </div>

          )}
          <div className="calibrationDrawing">
          {/* <div className="calibrationMap"> */}
              {/* <FloorPlanCalibrationMap
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
              /> */}
              <MapLabeler
                imageUrl={imageUrl}
                toggles={toggles}
                mapLabels={mapLabels}
                pushMapState={pushMapState}
                popMapState={popMapState}
                mapShapes={mapShapes}
                unfinishedMapFigure={unfinishedMapFigure}
                showImageMarkers={showImageMarkers}
                unselectDrawing={this.handleSelected}
                // hasHomography={hasHomography}
              />
            {/* </div> */}

          </div>
        <Button floated="left" color="purple" onClick={this.toggleModal}>
            Change Scale Factor
        </Button>
        {this.renderScaleFactorModal()}
          <Button
            onClick={() => {
              this.props.goBack();
            }}
            color="purple"
            floated="right"
          >
            Close
          </Button>
          {/* {this.props.loadingHomography ? (
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
              imagePoints={figures[floorPlanCalibId][0].points}
              mapPoints={mapShapes[floorPlanCalibMapId][0].points}
              onAcceptCalib={this.handleAcceptCalib}
            />
          ) : null} */}
        </div>
        {/* </MenuBar> */}
      </React.Fragment>
    );
  }
}

export default withAlert()(
  withLoadImageData(withImageHistory(withSetupFloorPlanImageHistory(SetupFloorPlanApp)))
);

SetupFloorPlanApp.propTypes = {
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
  // /** Lat/Lng coordinate to load as google map center */
  // mapCenter: PropTypes.object.isRequired,
  // /** Zoom level to use when loading the google map */
  // mapZoom: PropTypes.number.isRequired,
  /** Existing map shapes to load into google map */
  mapLabelData: PropTypes.object.isRequired,
  /** URL of image to use for sensor calibration */
  imageUrl: PropTypes.string.isRequired,
  /** URL of floorplan image to use for sensor calibration */
  // floorPlanImageUrl: PropTypes.string.isRequired,
  /** Existing image shapes to load into the image */
  labelData: PropTypes.object.isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  // /** Function that requests and waits for the backend to return a homography
  //  *  matrix */
  // waitForHomography: PropTypes.func.isRequired,
  // /** Boolean value to indicate if the homography is currently being loaded */
  // loadingHomography: PropTypes.bool.isRequired,
  // /** String representation of the homography matrix that can be parsed by
  //  * JSON.parse
  //  */
  // homography: PropTypes.string,
  /** Indicator if the sensor is currently calibrated or not */
  isCalibrated: PropTypes.bool.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired,
  /** Sensors  available for drawing in the image or google map */
  sensors: PropTypes.arrayOf(PropTypes.object),
  // fpHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Function to update the Validation Warped Image crop */
  updateScaleFactor: PropTypes.func.isRequired,
};
