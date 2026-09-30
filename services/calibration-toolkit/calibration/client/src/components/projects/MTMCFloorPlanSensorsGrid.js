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
import React, { Fragment } from "react";
import { useAlert } from "react-alert";
import { Link } from "react-router-dom";
import { Button, Checkbox, Grid, Image } from "semantic-ui-react";
import { getMediaUrl } from "../common/MediaUrl";


/**
 * Grid of sensors. The grid shows the sensor name, if
 * a button to add screenshot/image, a checkbox if the sensor is calibrated
 * and/or validated, and actions to calibrate, validate, or edit the sensor.
 */
function MTMCFloorPlanSensorsGrid(props) {
  const {  sensors, toggleEditSensor,
          toggleUploadShow } = props;
  console.log("MTMCGrid", sensors)
  const alert = useAlert();

  return (
    <Fragment>
      {sensors.map((sensor, key) => {
        return (
          <Grid.Row key={key}>
            <Grid.Column verticalAlign="middle" width={3}>
              {`${sensor.sensorId}`}
              <Image src={getMediaUrl(sensor.imageUrl)}/>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={5}>
              <Button
                className="ui compact left floated button"
                onClick={() => toggleUploadShow(sensor.id)}
                color="green"
                icon="camera"
                label="Upload Calibration Image"
                size="tiny"
              />
              {/* <Button
                className="ui compact right floated button"
                onClick={() => toggleUploadFloorPlanShow(sensor.id)}
                color="green"
                icon="camera"
                label="Upload FloorPlan"
                size="tiny"

              /> */}
            </Grid.Column>

            <Grid.Column verticalAlign="middle" textAlign="center" width={2}>
              <Checkbox checked={sensor.isCalibrated} label="Calibrated" />
               {/* {(sensor.sensorOnly) &&  <Checkbox checked={sensor.isCalibrated} label="Top Down Image Created" />} */}
              <Checkbox checked={sensor.isValidated} label="Validated" />
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={6}>
              <div>
                <Link
                  to={
                    (sensor.imageUrl) ? `/calib/floorplan/${sensor.id}`: "#"
                  }
                >
                  <Button
                    color="green"
                    icon="pencil"
                    label="Calibrate"
                    size="tiny"
                    secondary={!(sensor.imageUrl)}
                    onClick={() =>
                      !(sensor.imageUrl && sensor.floorPlanImageUrl)
                        ? alert.show("Please upload calibration image and floorplan first.")
                        : null
                    }
                  />
                </Link>

                <Link
                  to={
                    (sensor.isCalibrated) ? `/validation/floorplan/${sensor.id}` : "#"
                  }
                >
                  <Button
                    color="green"
                    icon="pencil"
                    label="Validate"
                    size="tiny"
                    secondary={!sensor.isCalibrated}
                    onClick={() =>
                      !sensor.isCalibrated
                        ? alert.show("Please calibrate first.")
                        : null
                    }
                  />
                </Link>
                <Button
                  onClick={() => toggleEditSensor(sensor.id)}
                  color="green"
                  icon="pencil"
                  label="Edit Sensor"
                  size="tiny"
                />
              </div>
            </Grid.Column>
          </Grid.Row>
        );
      })}
    </Fragment>
  );
}

export default MTMCFloorPlanSensorsGrid;

MTMCFloorPlanSensorsGrid.propTypes = {
  /** ID of the sensor the image is being uploaded to */
  // sensorOnly: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.bool]),
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
