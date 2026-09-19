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
import Modal from "react-modal";
import update from "immutability-helper";
import axios from "axios";

import UploadFloorPlanImageForm from "../forms/UploadFloorPlanImageForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for adding an image/screenshot to the Sensor backend
 * via a file upload or an RTSP stream.
 */
class UploadFloorPlanImageModal extends Component {
  constructor(props) {
    super(props);

    this.handleSaveData = this.handleSaveData.bind(this);
  }
  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user saves the data input into the form. The sensor ID is
   * used to patch the sensor in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSaveData(changedData, dataValidation) {
    const { sensorId } = this.props;
    let sensorData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        sensorData = update(sensorData, { [key]: { $set: changedData[key] } });
      }
    });

    if (Object.entries(sensorData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }

    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, sensorData)
      .then(() => {
        this.props.alert.success("RTSP Saved");
        this.props.reloadSensors();
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          Object.keys(data).forEach(key => {
            alert.error(`Error in ${key}. ${data[key]}`);
          });
        } else if (error.request) {
          console.error("No response from server.");
        }
      });
  }

  render() {
    const { modalShow, onClose, sensorId, reloadSensors } = this.props;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor FloorPlan Input"
      >
        <UploadFloorPlanImageForm
          sensorId={sensorId}
          onSaveData={this.handleSaveData}
          reloadSensors={reloadSensors}
          onClose={onClose}
        />
      </Modal>
    );
  }
}

export default withAlert()(UploadFloorPlanImageModal);

UploadFloorPlanImageModal.propTypes = {
  /** Indiactor whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the sensor the image is being uploaded to */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the sensor after uploading the image */
  reloadSensors: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
