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

import SensorDataForm from "../forms/SensorDataForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Bind the modal to the app element on component mount.
 */
class NewSensorModal extends Component {
  constructor(props) {
    super(props);
    // this.state = {
    //   floorPlanImageUrl: null,
    //   floorPlanImWidth: null,
    //   floorPlanImHeight: null

    // }
    this.handleNewSensor = this.handleNewSensor.bind(this);
    // this.getFloorPlanImage = this.getFloorPlanImage.bind(this)
  }

  /**
   * Load the intersections and corridors that belong to the city to set the
   * dropdown options for assigning a sensor to a intersection or corridor. Also
   * binds the modal to the app element.
   * @TODO Duplicate code, refactor to sensor data form.
   */
  async componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
 * Sync The Floor Plan images
 */
   async syncFPImages(projectId) {
    //update
    await axios
      .get(`${API_ENDPOINT}/syncFloorPlan/${projectId}/`)
      .then()
      .catch(error => console.error(error));
  }

  /**
   * Handle event that user selects to add the sensor based on the data
   * currently populating the form. If all the data is valid, a new sensor will
   * be created in the backend with the data provided in the form.
   * @param {object} sensorData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the sensor.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleNewSensor(sensorData, dataValidation, calibrationType) {
    let cancelSave = false;
    // let dv = {}
    // if  (!window.location.pathname.includes("/projects/geo/")){
      
    // } else{
      
    // }
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;
    // this.getFloorPlanImage()
    // const firstReq = await axios.get(`${API_ENDPOINT}/cities/${this.props.cityId}/`)

    // const city = firstReq.data
    // const { floorPlanImWidth, floorPlanImHeight, floorPlanImageUrl } = city;
    // console.log("cam1", floorPlanImageUrl)
    // // const {floorPlanImWidth, floorPlanImHeight, floorPlanImageUrl} = this.getFloorPlanImage(this.props.cityId)
    // console.log("cam12m, ", floorPlanImWidth, floorPlanImHeight, floorPlanImageUrl)
    sensorData = update(sensorData, {
      project: { $set: Number(this.props.projectId) }
    });

    // let form_data = new FormData();
    // // form_data.append("floorPlanImageUrl", filename, filename.name);
    // sensorData = update(sensorData, {
    //   floorPlanImageUrl: { $set: floorPlanImageUrl },
    //   floorPlanImHeight: { $set: floorPlanImHeight },
    //   floorPlanImWidth: { $set: floorPlanImWidth }
    // });
    sensorData = update(sensorData, {
      project: { $set: Number(this.props.projectId) }
    });
    if (this.props.intersectionId) {
      sensorData = update(sensorData, {
        intersection: { $set: Number(this.props.intersectionId) }
      });
    }
    if (this.props.corridorId) {
      sensorData = update(sensorData, {
        corridor: { $set: Number(this.props.corridorId) }
      });
    }
    sensorData = update(sensorData, {
      calibrationType: { $set: calibrationType }
    });
    await axios
      .post(`${API_ENDPOINT}/sensors/`, sensorData)
      .then(() => {
        this.props.alert.success(`Created ${sensorData.sensorId}`);
        if (window.location.pathname.includes("/projects/mtmc/")){
          this.syncFPImages(this.props.projectId)
        }
        this.props.reloadSensors();
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          Object.keys(data).forEach(key => {
            if (key === "non_field_errors" && data[key][0]=== "The fields sensorId, project must make a unique set."){
              alert.error(`Sensor with id ${sensorData.sensorId} already exists`);
            }
            else{
              alert.error(`Error in ${key}. ${data[key]}`);
            }
          });
        } else if (error.request) {
          console.error("No response from server.");
        }
      });
  }

  render() {
    const {
      modalShow,
      onClose,
      projectId,
      intersectionId,
      corridorId,
      placeId
    } = this.props;

    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
      >
        <h2>Create New Sensor:</h2>
        <SensorDataForm
          projectId={projectId}
          onClose={onClose}
          onSaveData={this.handleNewSensor}
          intersectionId={intersectionId}
          corridorId={corridorId}
          placeId={placeId}
          useDefaults={true}
        />
      </Modal>
    );
  }
}

export default withAlert()(NewSensorModal);

NewSensorModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the new sensor to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,

  /** Handle reloading the sensors after a new sensor is added */
  reloadSensors: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
