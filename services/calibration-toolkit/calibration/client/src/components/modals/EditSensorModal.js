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
import Tabs from "../common/Tabs";
import update from "immutability-helper";
import axios from "axios";

import SensorDataForm from "../forms/SensorDataForm";
import UploadImageForm from "../forms/UploadImageForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

require("../common/TabStyles.css");

/**
 * Modal to use as popup when editing the data of an existing sensor.
 */
class SensorDataModal extends Component {
  constructor(props) {
    super(props);

    this.handleSaveData = this.handleSaveData.bind(this);
    this.handleDelete = this.handleDelete.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
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
  async handleSaveData(changedData, dataValidation, calibrationType) {
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

    console.log(sensorData)
    await axios
      .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, sensorData)
      .then(() => {
        this.props.reloadSensors();
        this.props.alert.success("Updated Metadata");
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          console.log("error here")
          Object.keys(data).forEach(key => {
            alert.error(`Error in ${key}. ${data[key]}`);
          });
        } else if (error.request) {
          console.error("No response from server.");
        }
      });
    if (dataValidation.sensorId){
      console.log("sensor id name changed")
      await this.updateMMSSensorName()
    }
  }

  /** Post Method */
  pushData(mmsInfo_host, deviceId, sensorId){
    const data = {"name": `${sensorId}`};
    const {alert} = this.props;
    console.log("updating", data)
    console.log("this is the data" , mmsInfo_host,  deviceId, sensorId)

    var mmsURL = mmsInfo_host
    if (!mmsInfo_host.endsWith("/")){
      mmsURL = mmsInfo_host + "/"
    }
    console.log("i amm here" , `${mmsURL}api/v1/sensor/${deviceId}/info`)

    axios
    .post(`${mmsURL}api/v1/sensor/${deviceId}/info`, data)
    .then(res => {
      alert.success("MMS Sensor Name Updated");
    })
    .catch(error => {
      alert.error("Error in Updating MMS Sensor Name");
    })
  }

  /**
   * Push update of Sensor ID to MMS
   */
  async updateMMSSensorName(){
    const {sensorId,} = this.props
    await axios
      .get(`${API_ENDPOINT}/syncSensor/${sensorId}/`)
      .then(res => {
        const {mmsInfo_host, deviceId, sensorId}  =  res.data
        this.props.alert.success("MMS Sensor Name Updated");
        // this.pushData(mmsInfo_host,deviceId,sensorId)
      })
      .catch(error => {
        this.props.alert.error("Error in Updating MMS Sensor Name")
      });

  }

  /**
   * Handle the action of the user choosing to delete the sensor. Deletes the
   * sensor from the backend based on its ID and reloads the page to show the
   * sensor removal.
   */
  async handleDelete() {
    const { sensorId } = this.props;
    await axios
      .delete(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(() => {
        this.props.reloadSensors();
        this.props.onClose();
      })
      .catch(error => console.error(error));
  }

  render() {
    const { modalShow, onClose, sensorId,  projectId, reloadSensors } = this.props;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
      >
        <Tabs>
          <div label="Change Metadata">
            <h2>Input Sensor Metadata:</h2>
            <SensorDataForm
              projectId={projectId}
              sensorId={sensorId}
              onClose={onClose}
              onDelete={this.handleDelete}
              onSaveData={this.handleSaveData}
              useDefaults={false}
            />
          </div>
          <div label="Upload Image">
            <UploadImageForm
              sensorId={sensorId}
              onClose={onClose}
              onSaveData={this.handleSaveData}
              reloadSensors={reloadSensors}
              onDelete={this.handleDelete}
            />
          </div>
        </Tabs>
      </Modal>
    );
  }
}

export default withAlert()(SensorDataModal);

SensorDataModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the city that the sensor belongs to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** ID of the sensor being edited */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of reloading the sensor data after patching backend */
  reloadSensors: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
