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


import React, { Fragment } from "react";
import PropTypes from "prop-types";
import { Label, Checkbox, Button, Grid, Image } from "semantic-ui-react";
import { Link } from "react-router-dom";

import { useAlert } from "react-alert";
import FloorPlanSensorsGrid from "./FloorPlanSensorsGrid";
import CartesianSensorsGrid from "./CartesianSensorsGrid";
import GeoSensorsGrid from "./GeoSensorsGrid";

/**  Map of Calibration Type Grid Rows */
// const componentMap = {1: <FirstComponent/>, 2: <SecondComponent/>, 3: <ThirdComponent/>};




/**
 * Grid of sensors. The grid shows the sensor name, if
 * a button to add screenshot/image, a checkbox if the sensor is calibrated
 * and/or validated, and actions to calibrate, validate, or edit the sensor.
 */
function SensorsGrid(props) {
  const {  calibrationType, sensors, toggleEditSensor, toggleUploadShow } = props;
  const alert = useAlert();
  const renderSwitchGrid = grid => {
    switch(calibrationType){
      case 'geo':
      case 'cartesian':
        return CartesianSensorsGrid
      case 'floorplan':
        return FloorPlanSensorsGrid
    }
  };
  return (
    <div
      {sensors.map((sensor, key) => {

        return (
          <div {this.renderSwitch()}/>

          // <Grid.Row key={key}>
          //   <Grid.Column verticalAlign="middle" width={3}>
          //     {/* {sensorOnly ? `${sensor.sensorId}` : sensor.sensorId} */}
          //     {switch (calibrationType){
          //       case 'geo':
          //         return sensor.sensorId
          //       case 'cartesian':
          //         return `${sensor.sensorId}`
          //       case 'floorplan':
          //         return sensor.sensorId
          //     }}
          //     <Image src={sensor.imageUrl}/>
          //   </Grid.Column>
          //   <Grid.Column verticalAlign="middle" textAlign="center" width={5}>
          //     <Button
          //       onClick={() => toggleUploadShow(sensor.id)}
          //       color="green"
          //       icon="sensor"
          //       label="Upload Calibration Image"
          //       size="tiny"
          //     />
          //   </Grid.Column>

          //   <Grid.Column verticalAlign="middle" textAlign="center" width={2}>
          //     <Checkbox checked={sensor.isCalibrated} label="Calibrated" />
          //      {/* {(sensor.sensorOnly) &&  <Checkbox checked={sensor.isCalibrated} label="Top Down Image Created" />} */}
          //     <Checkbox checked={sensor.isValidated} label="Validated" />
          //   </Grid.Column>
          //   <Grid.Column verticalAlign="middle" textAlign="center" width={6}>
          //     <div>
          //       <Link
          //         to={
          //           sensor.imageUrl
          //             ? sensorOnly
          //               ? `/calib/cartesian/${sensor.id}`
          //               : `/calib/geo/${sensor.id}`
          //             : "#"
          //         }
          //       >
          //         <Button
          //           color="green"
          //           icon="pencil"
          //           label="Calibrate"
          //           size="tiny"
          //           secondary={!sensor.imageUrl}
          //           onClick={() =>
          //             !sensor.imageUrl
          //               ? alert.show("Please upload calibration image first.")
          //               : null
          //           }
          //         />
          //       </Link>

          //       <Link
          //         to={
          //           (sensor.isCalibrated)
          //             ? sensorOnly
          //               ? `/validation/cartesian/${sensor.id}`
          //               : `/validation/geo/${sensor.id}`
          //             : "#"
          //         }
          //       >
          //         <Button
          //           color="green"
          //           icon="pencil"
          //           label="Validate"
          //           size="tiny"
          //           secondary={!sensor.isCalibrated}
          //           onClick={() =>
          //             !sensor.isCalibrated
          //               ? alert.show("Please calibrate first.")
          //               : null
          //           }
          //         />
          //       </Link>
          //       <Button
          //         onClick={() => toggleEditSensor(sensor.id)}
          //         color="green"
          //         icon="pencil"
          //         label="Edit Sensor"
          //         size="tiny"
          //       />
          //     </div>
          //   </Grid.Column>
          // </Grid.Row>
        );
      })}
    </Fragment>
  );
}

export default SensorsGrid;

SensorsGrid.propTypes = {
  /** ID of the sensor the image is being uploaded to */
  sensorOnly: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Project calibration Type */
  calibrationType: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
  /** Sensors to include in the grid */
  sensors: PropTypes.arrayOf(PropTypes.object),
  /** Handle action of clicking to edit a sensor */
  toggleEditSensor: PropTypes.func,
  /** Toggle whether or not the upload modal image modal is showing */
  toggleUploadShow: PropTypes.func
};
