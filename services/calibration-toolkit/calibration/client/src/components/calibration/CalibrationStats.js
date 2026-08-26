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
import { Table, Button } from "semantic-ui-react";
import { multiply } from "mathjs";
import { withAlert } from "react-alert";
import { haversineDistance, euclideanDistance, flipPointY } from "../common/mathUtils";

import {
  convertLatLngToXYMatrix,
  convertProjectedPoint
} from "../common/mathUtils";

/**
 * Display the current reprojection error stats using the currenty homography
 * matrix calculated by the calibration app.
 */
class CalibrationStats extends Component {
  constructor(props) {
    super(props);
    console.log("calib here",this.props)
    this.state = {
      isLoaded: false,
      reprojectionErrors: []
    };

    this.checkCalibrationStats = this.checkCalibrationStats.bind(this);
  }


  componentDidMount() {
    const { imagePoints, mapPoints, calibrationType } = this.props;
    console.log("calibstats", this.props)
    if (this.props.homography) {
      const homography = JSON.parse(this.props.homography);
      if (calibrationType === "geo"){
        const reprojectionErrors = imagePoints.map((point, index) => {
          const matrixPoint = convertLatLngToXYMatrix(point, this.props.height);
          const projectedPoint = convertProjectedPoint(
            multiply(homography, matrixPoint)
          );
          console.log("projectedPoint", projectedPoint)
          return haversineDistance(projectedPoint, mapPoints[index]);
          
        });
        this.setState({ isLoaded: true, reprojectionErrors });
      }
      else{
        const reprojectionErrors = imagePoints.map((point, index) => {
          // For cartesian calibration, use the actual image height for point transformation
          const height = this.props.fpImHeight || this.props.height;
          // Convert original point to matrix coordinates (origin at top-left)
          const matrixPoint = convertLatLngToXYMatrix(point, height);
          
          // Project the point using homography
          const projectedPoint = convertProjectedPoint(
            multiply(homography, matrixPoint)
          );
          
          const error = euclideanDistance(projectedPoint, mapPoints[index]);
          console.log("Reprojection error:", error);
          return error;
        });
        this.setState({ isLoaded: true, reprojectionErrors });
      }
   }
  }
  

  checkCalibrationStats() {
    const errorLimit = 3.0;
    const { reprojectionErrors } = this.state;
    const {calibrationType} = this.props;
    let acceptCalib = true;
    if (calibrationType === 'geo'){
      reprojectionErrors.forEach(error => {
        if (Number(error) >= Number(errorLimit)) {
          acceptCalib = false;
          return;
        }
      });
    }else{
      acceptCalib =  true;
    }

    if (acceptCalib) {
      this.props.onAcceptCalib();
    } else {
      this.props.alert.error(
        `All points must have error of less than ${errorLimit}.`
      );
    }
  }

  render() {
    const { reprojectionErrors, isLoaded } = this.state;
    const pointStats = (error, index) => {
      return (
        <Table.Row key={index}>
          <Table.Cell>{index}</Table.Cell>
          <Table.Cell data-testid={`RepErr_${index}`}>{error}</Table.Cell>
        </Table.Row>
      );
    };

    if (!isLoaded) {
      return (
        <h2 data-testid="HomographyWarning">No homography matrix provided</h2>
      );
    }
    return (
      <div>
        <Table celled>
          <Table.Header>
            <Table.Row>
              <Table.HeaderCell>Point</Table.HeaderCell>
              <Table.HeaderCell>Reprojection Error    (meters)</Table.HeaderCell>
            </Table.Row>
          </Table.Header>

          <Table.Body>
            {reprojectionErrors.map((error, index) => pointStats(error, index))}
          </Table.Body>
          {/* {/* <Table.Row>  */}
            {/* <Table.Column></Table.Column>
            <Table.Column textAlign="right"> *This is just a suggestion in Cartesian/ Multi Camera Tracking</Table.Column> */}
          {/* </Table.Row> */} 
        </Table>
        <Button
          color="green"
          onClick={this.checkCalibrationStats}
          data-testid="AcceptCalibration"
        >
          Accept Calibration
        </Button>
      </div>
    );
  }
}

export default withAlert()(CalibrationStats);

CalibrationStats.propTypes = {
  /** Height aspect of the image resolution */
  height: PropTypes.number,
  /** String representation of the homography matrix that can be parsed by
   * JSON.parse
   */
  homography: PropTypes.string,
  /** Array of points in the image frame to be projected to the map frame */
  imagePoints: PropTypes.arrayOf(PropTypes.object),
  /** Array of ground truth values in the map frame */
  mapPoints: PropTypes.arrayOf(PropTypes.object),
  /** Handle the action of clicking the accept calibration button */
  onAcceptCalib: PropTypes.func,
  /**Type of calibration */
  calibrationType: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
  .isRequired,
  /** floorPlan image height*/
  fpImHeight: PropTypes.oneOfType([PropTypes.number]).isRequired,

};
