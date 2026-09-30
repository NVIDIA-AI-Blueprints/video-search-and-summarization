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

import IntersectionDataForm from "../forms/IntersectionDataForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for entering data to add a new intersection.
 */
class NewIntersectionModal extends Component {
  constructor(props) {
    super(props);

    this.handleNewIntersection = this.handleNewIntersection.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user selects to add the intersection based on the data
   * currently populating the form. If all the data is valid, a new intersection
   * will be created in the backend with the data provided in the form.
   * @param {object} intersectionData Keys represent attribute in the backend,
   * and value represents the value to be used when creating the intersection.
   * @param {object} dataValidation Keys represent attribute in the backend,
   * and value is a boolean that is true if and only if the data belonging to
   * that key has been updated and is valid.
   */
  async handleNewIntersection(intersectionData, dataValidation) {
    const { projectId } = this.props;
    let cancelSave = false;
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    intersectionData = update(intersectionData, {
      project: { $set: projectId }
    });


    await axios
      .post(`${API_ENDPOINT}/intersections/`, intersectionData)
      .then(() => {
        this.props.alert.success(`Created ${intersectionData.name}`);
        this.props.reloadIntersections();
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          Object.keys(data).forEach(key => {
            if (key === "non_field_errors" && data[key][0]=== "The fields name, project must make a unique set."){
              alert.error(`Intersection with name ${intersectionData.name} already exists`);
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
        <h2 data-testid="NewIntersecTitle">{`Create New Intersection in ${projectName}:`}</h2>
        <IntersectionDataForm
          onClose={onClose}
          onSaveData={this.handleNewIntersection}
        />
      </Modal>
    );
  }
}

export default withAlert()(NewIntersectionModal);

NewIntersectionModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of project to add the intersection to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** Name of the project to add the intersection to */
  projectName: PropTypes.string,
  /** Handle reloading the intersections after a new one is added */
  reloadIntersections: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
