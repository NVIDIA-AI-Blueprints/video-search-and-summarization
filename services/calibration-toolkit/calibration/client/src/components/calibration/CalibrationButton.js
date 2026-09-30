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
import { Button } from "semantic-ui-react";

/**
 * Button to signal to calibrate the sensor given the current shapes drawn in
 * the image and google map.
 */
class CalibrationButton extends Component {
  render() {
    const { onEditClick, onCalibClick } = this.props;
    return (
      <div>
        {this.props.hasHomography ? (
          <Button
            floated="left"
            color="green"
            onClick={() => onEditClick()}
            data-testid="EditCalibrationBtn"
          >
            Edit
          </Button>
        ) : (
          <div>
            <Button
              floated="left"
              color="green"
              onClick={() => onCalibClick()}
              data-testid="CalibrateBtn"
            >
              Calibrate
            </Button>
          </div>
        )}
      </div>
    );
  }
}

export default CalibrationButton;

CalibrationButton.propTypes = {
  /** Indicator if the calibration button should be displayed */
  hasHomography: PropTypes.bool,
  /** Handle the action of clicking the edit button */
  onEditClick: PropTypes.func,
  /** Handle the action of clicking the calibration button */
  onCalibClick: PropTypes.func
};
