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


import PropTypes from "prop-types";
import React from "react";
import { useAlert } from "react-alert";
import CartesianSensorsGrid from "./CartesianSensorsGrid";
import FloorPlanSensorsGrid from "./FloorPlanSensorsGrid";
import MTMCFloorPlanSensorsGrid from "./MTMCFloorPlanSensorsGrid";
import GeoSensorsGrid from "./GeoSensorsGrid";
import ImageSensorsGrid from "./ImageSensorsGrid";
// import FloorPlanSensorsGrid from "./FloorPlanSensorsGrid";
// import CartesianSensorsGrid from "./CartesianSensorsGrid";
// import GeoSensorsGrid from "./GeoSensorsGrid";

/**  Map of Calibration Type Grid Rows */
// const componentMap = {1: <FirstComponent/>, 2: <SecondComponent/>, 3: <ThirdComponent/>};




/**
 * Grid of sensors. The grid shows the sensor name, if
 * a button to add screenshot/image, a checkbox if the sensor is calibrated
 * and/or validated, and actions to calibrate, validate, or edit the sensor.
 */
function SensorsGrid(props) {

  const {  calibrationType, sensors,
          toggleEditSensor, toggleUploadShow, toggleUploadFloorPlanShow, floorPlan, mapAPIKey } = props;
  const alert = useAlert();

  console.log("cg",props, calibrationType, sensors)
  switch (calibrationType){
    case "geo":
      return (<GeoSensorsGrid 
        // sensorOnly={sensorOnly}
        calibrationType={calibrationType}
        sensors={sensors}
        toggleEditSensor={toggleEditSensor}
        toggleUploadShow={toggleUploadShow}
        mapAPIKey={mapAPIKey}
      />)
    case "cartesian":
      return (<CartesianSensorsGrid
        // sensorOnly={sensorOnly}
        calibrationType={calibrationType}
        sensors={sensors}
        toggleEditSensor={toggleEditSensor}
        toggleUploadShow={toggleUploadShow}
        />)
    case "floorplan":
      return (<FloorPlanSensorsGrid 
        // sensorOnly={sensorOnly}
        calibrationType={calibrationType}
        sensors={sensors}
        toggleEditSensor={toggleEditSensor}
        toggleUploadShow={toggleUploadShow}
        toggleUploadFloorPlanShow={toggleUploadFloorPlanShow}
        // cityFloorPlan={cityFloorPlan}
      />)
    case "mtmc":
      return (<MTMCFloorPlanSensorsGrid 
        // sensorOnly={sensorOnly}
        calibrationType={calibrationType}
        sensors={sensors}
        toggleEditSensor={toggleEditSensor}
        toggleUploadShow={toggleUploadShow}
        toggleUploadFloorPlanShow={toggleUploadFloorPlanShow}
        floorPlan={floorPlan}
      />)
    case "image":
      return (<ImageSensorsGrid 
        // sensorOnly={sensorOnly}
        calibrationType={calibrationType}
        sensors={sensors}
        toggleEditSensor={toggleEditSensor}
        toggleUploadShow={toggleUploadShow}
        toggleUploadFloorPlanShow={toggleUploadFloorPlanShow}
        floorPlan={floorPlan}
      />)
    default:
    
  }

}

export default SensorsGrid;

SensorsGrid.propTypes = {
  /** ID of the sensor the image is being uploaded to */
  // sensorOnly: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Project calibration Type */
  mapAPIKey: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Project calibration Type */
  floorPlan: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Project calibration Type */
  calibrationType: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Sensors to include in the grid */
  sensors: PropTypes.arrayOf(PropTypes.object),
  /** Handle action of clicking to edit a sensor */
  toggleEditSensor: PropTypes.func,
  /** Toggle whether or not the upload modal image modal is showing */
  toggleUploadShow: PropTypes.func,
  /** Toggle whether or not the upload modal image modal is showing */
  toggleUploadFloorPlanShow: PropTypes.func
};
