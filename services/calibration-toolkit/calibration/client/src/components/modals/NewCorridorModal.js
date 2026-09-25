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

import CorridorDataForm from "../forms/CorridorDataForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for entering data to add a new corridor.
 */
class NewCorridorModal extends Component {
  constructor(props) {
    super(props);

    this.handleNewCorridor = this.handleNewCorridor.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user selects to add the corridor based on the data
   * currently populating the form. If all the data is valid, a new corridor
   * will be created in the backend with the data provided in the form.
   * @param {object} corridorData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the corridor.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleNewCorridor(corridorData, dataValidation) {
    const { cityId, projectId } = this.props;
    let cancelSave = false;
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    corridorData = update(corridorData, {
      project: { $set: projectId }
    });
    corridorData = update(corridorData, {
      city: { $set: cityId }
    });

    await axios
      .post(`${API_ENDPOINT}/corridors/`, corridorData)
      .then(() => {
        this.props.alert.success(`Created ${corridorData.name}`);
        this.props.reloadCorridors();
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          Object.keys(data).forEach(key => {
            if (key === "non_field_errors" && data[key][0]=== "The fields name, project must make a unique set."){
              alert.error(`Corridor with name ${corridorData.name} already exists`);
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
    const { modalShow, onClose, projectName } = this.props;

    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
      >
        <h2 data-testid="NewCorTitle">{`Create New Corridor in ${projectName}:`}</h2>
        <CorridorDataForm
          onSaveData={this.handleNewCorridor}
          onClose={onClose}
        />
      </Modal>
    );
  }
}

export default withAlert()(NewCorridorModal);

NewCorridorModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the corridor to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** ID of the city to add the corridor to */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Name of the city to add the corridor to */
  projectName: PropTypes.string,
  /** Handle reloading the corridors when a new one has been added */
  reloadCorridors: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
