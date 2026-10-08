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
import { withAlert } from "react-alert";
import { Form, Header, Button, Loader } from "semantic-ui-react";
import axios from "axios";

import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Form for uploading a calibration image from either a RTSP stream or a jpg/png
 * screenshot.
 */
class UploadImageForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      sensorData: {
        rtspURL: ""
      },
      dataValidation: {
        rtspURL: false
      },
      isEdited: {
        rtspURL: false
      },
      isLoadingRTSP: false,
      isLoaded: false,
      error: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.getScreenshotFromRTSP = this.getScreenshotFromRTSP.bind(this);
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
   * Request backend to generate a screenshot for the RTSP stream associated
   * with the sensor.
   */
  async getScreenshotFromRTSP() {
    const { rtspURL } = this.state.dataValidation;
    if (rtspURL) {
      const { sensorId, alert } = this.props;
      await axios
        .get(`${API_ENDPOINT}/rtsp/${sensorId}/`)
        .then(() => {
          alert.success("Screenshot taken from RTSP steam");
        })
        .catch(() => alert.error("Error reading rtsp stream"));
    } else {
      const { alert } = this.props;
      alert.error("RTSP String Not Valid");
    }
  }

  /**
   * Upload and save an image to the backend from a file obtained using the file
   * explorer interface.
   * @param {number} sensorId Public key representing the sensor IDp
   * @param {object} filename Information about the selected image to use for
   * calibration
   */
  async handleUploadImage(sensorId, filename) {
    let form_data = new FormData();
    form_data.append("imageUrl", filename, filename.name);
    form_data.append("sensorPolygon", "[]");
    form_data.append("homography", "[]");
    form_data.append("imHomography", "[]");
    form_data.append("edgeLengths", "[]");
    form_data.append("isCalibrated", false);
    form_data.append("isValidated", false);
    console.log("upisd", form_data)
    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, form_data)
      .then(res => {
        const { sensorId } = res.data;
        this.props.alert.success(`Uploaded image for ${sensorId}`);
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
        const imageUrl = getMediaUrl(res.data.imageUrl);
        if (imageUrl) {
          this.loadImageResolution(imageUrl);
        }
      })
      .catch(error => console.error(error));
  }

  /**
   * Load the resolution of the image once it has been uploaded to the backend.
   * @param {string} imageUrl Filepath to image uploaded and saved in backend
   */
  async loadImageResolution(imageUrl) {
    const { sensorId } = this.props;
    let img = new Image();
    img.onload = async function() {
      const { height, width } = this;
      const { invertImHeight, invertImWidth} = this;
      await axios
        .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, { height, width, invertImWidth, invertImHeight })
        .then()
        .catch();
    };
    img.src = imageUrl;
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

    if (isLoadingRTSP) {
      return (
        <div style={{ textAlign: "center" }}>
          <h1>Loading RTSP Screenshot</h1>
          <p>Please wait. This may take a few seconds.</p>
          <Loader active inline="centered" />
        </div>
      );
    }

    return (
      <Form>
        {/* <h1>Upload Via RTSP url:</h1> */}
        {/* <Form.Input
          label="RTSP url"
          content="rtspURL"
          value={rtspURL}
          error={
            isEdited.rtspURL && !dataValidation.rtspURL
              ? "Invalid RTSP url"
              : false
          }
          onChange={(e, data) => this.handleDataChange(e, data)}
          data-testid="RTSPInput"
        />
        <Form.Group>
          <Form.Button
            color="green"
            onClick={() => onSaveData(sensorData, dataValidation)}
            data-testid="RTSPSave"
          >
            Save RTSP Url
          </Form.Button>
          <Form.Button
            color="green"
            onClick={async () => {
              this.setState({ isLoadingRTSP: true });
              await onSaveData(sensorData, dataValidation);
              await this.getScreenshotFromRTSP();
              this.setImageResolution();
              this.setState({ isLoadingRTSP: false });
              reloadSensors();
            }}
          >
            Save and Load Screenshot
          </Form.Button>
        </Form.Group> */}

        <hr />
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

export default withAlert()(UploadImageForm);

UploadImageForm.propTypes = {
  /** ID of sensor to upload the image to */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Reload the sensor when the image has been uploaded */
  reloadSensors: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func
};
