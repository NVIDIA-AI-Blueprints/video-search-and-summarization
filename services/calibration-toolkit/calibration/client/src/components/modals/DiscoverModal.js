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
import update from "immutability-helper";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import Modal from "react-modal";
import { modalLayer1 } from "../common/utils";
import DiscoverSensorForm from "../forms/DiscoverSensorForm";



import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for entering data to add a new project.
 */
class DiscoverModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      loadingSensors: false
    };

    this.handleSensors = this.handleSensors.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * @TODO need to fix the comments here
   * Handle event that user selects to add the project based on the data
   * currently populating the form. If all the data is valid, a new project will
   * be created in the backend with the data provided in the form.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the project.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSensors(changedData, dataValidation) {
    const { projectId } = this.props;

    let projectData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        projectData = update(projectData, { [key]: { $set: changedData[key] } });
      }
    });

    if (Object.entries(projectData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }
    
    await axios
      .patch(`${API_ENDPOINT}/projects/${projectId}/`, projectData)
      .then(() => {
        //Change this to show sensors updated
        this.props.alert.success(`MMS URL saved`);
        this.props.reloadSensors();
      })
      .catch(error => {
        const { alert } = this.props;
        console.log(error)
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

  /**
   * @TODO needs to be updated
   * Request the backend to download the map value given the download link
   * provided by the user. If the link doesn't download correctly, a new project
   * will not be created.
   * @param {number} projectId ID representing project to download map file for
   */
  // async importSensors(projectId) {
  //   const { alert } = this.props;
  //   this.setState({ loadingSensors: true });
  //   await axios
  //     .get(`${API_ENDPOINT}/discoverSensors/${projectId}/`)
  //     .then(() => {
  //       alert.success("Importing Sensors");
  //       this.setState({ loadingMapFile: false });
  //     })
  //     .catch(async error => {
  //       alert.error("Invalid Map File Url. City Deleted.");
  //       await axios
  //         .delete(`${API_ENDPOINT}/mms/${projectId}/`)
  //         .then(res => {})
  //         .catch(error => console.error("Delete project error", error));
  //       this.props.reloadSensors();
  //       this.setState({ loadingMapFile: false });
  //     });
  // }

  render() {
    const { modalShow, goBack, projectId, projectName, reloadSensors } = this.props;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Import"
      >
          <div>
            <h2 data-testid="ImportSensors">{`Importing Sensors in ${projectName}:`}</h2>
            <DiscoverSensorForm 
              projectId={projectId}
              onSaveData={this.handleSensors} 
              reloadSensors={reloadSensors}
              goBack={goBack} 
              />
          </div>
        
      </Modal>
    );
  }
}

export default withAlert()(DiscoverModal);

DiscoverModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** Name of the project to add the project to */
  projectName: PropTypes.string.isRequired,
  /** ID of the project to add the project to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Handle reloading the cities after new project added */
  reloadSensors: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
