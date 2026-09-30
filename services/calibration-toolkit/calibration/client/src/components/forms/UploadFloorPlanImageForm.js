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


import axios from "axios";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Button, Form, Header, Loader } from "semantic-ui-react";
import { getMediaUrl } from "../common/MediaUrl";
import {
  setEdited,
  updateDataValidation,
  updateDataValue
} from "../common/utils";

import { API_ENDPOINT } from "../common/axios_instance";

/**
 * Form for uploading a calibration image from either a RTSP stream or a jpg/png
 * screenshot.
 */
class UploadFloorPlanImageForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      sensorData: {
        floorPlanImageUrl: ""
      },
      dataValidation: {
        floorPlanImageUrl: false
      },
      isEdited: {
        floorPlanImageUrl: false
      },
      isLoadingRTSP: false,
      isLoaded: false,
      error: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.setImageResolution = this.setImageResolution.bind(this);
    this.loadImageResolution = this.loadImageResolution.bind(this);
  }

  /**
   * Get rtsp url oon component mount
   */
  async componentDidMount() {
    const { sensorId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const { sensorData, dataValidation } = this.state;
        const { rtspURL } = res.data;
        if (rtspURL) {
          const newData = updateDataValue(sensorData, "rtspURL", rtspURL);
          const newDataValidation = updateDataValidation(
            dataValidation,
            "rtspURL",
            rtspURL
          );
          this.setState({
            isLoaded: true,
            sensorData: newData,
            dataValidation: newDataValidation
          });
        } else {
          this.setState({ isLoaded: true });
        }
      });
  }


  /**
   * Upload and save an image to the backend from a file obtained using the file
   * explorer interface.
   * @param {number} sensorId Public key representing the sensor ID
   * @param {object} filename Information about the selected image to use for
   * calibration
   */
  async handleUploadImage(sensorId, filename) {
    let form_data = new FormData();
    form_data.append("floorPlanImageUrl", filename, filename.name);
    // form_data.append("sensorPolygon", "[]");
    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, form_data)
      .then(res => {
        const { sensorId } = res.data;
        this.props.alert.success(`Uploaded floorplan image for ${sensorId}`);
        this.props.reloadSensors();
      })
      .catch(error => console.error(error));
  }

  /**
   * Save the resolution of the image (height and width) to the backend of the
   * sensor.
   */
  async setImageResolution() {
    const { sensorId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const floorPlanImageUrl = getMediaUrl(res.data.floorPlanImageUrl);
        if (floorPlanImageUrl) {
          this.loadImageResolution(floorPlanImageUrl);
        }
      })
      .catch(error => console.error(error));
  }

  /**
   * Load the resolution of the image once it has been uploaded to the backend.
   * @param {string} floorPlanImageUrl Filepath to image uploaded and saved in backend
   */
  async loadImageResolution(floorPlanImageUrl) {
    const { sensorId } = this.props;
    let img = new Image();
    img.onload = async function() {
      const { height, width } = this;

      const floorPlanImHeight = height
      const floorPlanImWidth = width
      // const { invertImHeight, invertImWidth} = this;
      await axios
        .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, { floorPlanImWidth, floorPlanImHeight })
        .then()
        .catch();
    };
    img.src = floorPlanImageUrl;
  }

  /**
   * Handle data changed in any form input field. Sets that field as edited,
   * sets the value in the state based off of what was written in the field,
   * and runs data validation to see if the entered data is accurate.
   * @param {object} e Data changed event, unused
   * @param {object} data Information on the data in the form
   */
  handleDataChange(e, data) {
    const { content, value } = data;
    const { sensorData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(sensorData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      isEdited: newEdited,
      sensorData: newData,
      dataValidation: newDataValidation
    });
  }

  render() {
    const {
      sensorId,
      onClose,
      onDelete,
      onSaveData,
      reloadSensors
    } = this.props;
    const {
      dataValidation,
      sensorData,
      isEdited,
      isLoadingRTSP,
      isLoaded,
      error
    } = this.state;
    const { rtspURL } = sensorData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }


    return (
      <Form>
        <h1>Upload Via Screenshot File:</h1>
        <label>
          <input
            onChange={async e => {
              await this.handleUploadImage(sensorId, e.target.files[0]);
              this.setImageResolution();
              reloadSensors();
            }}
            type="file"
            accept="image/png, image/jpeg"
          />
        </label>
        <hr />
        <Form.Button
          color="purple"
          onClick={() => onClose()}
          data-testid="UploadFormClose"
        >
          Close
        </Form.Button>
        {onDelete && (
          <div>
            <hr />
            <Header>DELETE CAMERA</Header>
            <p>The button bellow will delete the sensor and all its data.</p>
            <Button negative onClick={() => onDelete()}>
              DELETE CAMERA
            </Button>
          </div>
        )}
      </Form>
    );
  }
}

export default withAlert()(UploadFloorPlanImageForm);

UploadFloorPlanImageForm.propTypes = {
  /** ID of sensor to upload the image to */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Reload the sensor when the image has been uploaded */
  reloadSensors: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func
};
