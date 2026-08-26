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
import Modal from "react-modal";
import { modalLayer1 } from "../common/utils";
import ProjectDataForm from "../forms/ProjectDataForm";



import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for entering data to add a new intersection.
 */
class NewProjectModal extends Component {
  constructor(props) {
    super(props);

    this.handleNewProject = this.handleNewProject.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * @todo this needs to be fixed
   * Handle event that user selects to add the intersection based on the data
   * currently populating the form. If all the data is valid, a new intersection
   * will be created in the backend with the data provided in the form.
   * @param {object} projectData Keys represent attribute in the backend,
   * and value represents the value to be used when creating the intersection.
   * @param {object} dataValidation Keys represent attribute in the backend,
   * and value is a boolean that is true if and only if the data belonging to
   * that key has been updated and is valid.
   */
   /**
   * Handle the new project selection. Creates a new project with the name
   * NEW_PROJECT and saves it in the backend.
   * @todo Find a way so that the name is different than NEW_PROJECT if a
   * project named NEW_PROJECT already exists.
   */
  async handleNewProject(projectData, dataValidation) {

    let cancelSave = false;
    console.log("npm", projectData)
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    // const {projectId, projectType} = this.props;
    // console.log ("project name: "  projectId)
    // console.log("project type:" projectType )
    // projectData = update(projectData, {
    //   name: { $set: projectId }
    // });
    // projectData = update(projectData, {
    //   projectType: { $set: projectType }
    // });

    // console.log(`project name ${projectData.name}`)

    await axios
        .post(`${API_ENDPOINT}/projects/`, projectData)
        .then(() => {
        // const newProject = res.data;
        // this.setState({
        //     projects: this.state.projects.concat([newProject])
        // });
        // alert.success(`NEW_PROJECT created`);
        // })
            this.props.alert.success(`Created ${projectData.name}`);
            this.props.reloadProject();
            this.props.onClose()
          })
        .catch(error => {
            const { alert } = this.props;
            if (error.response) {
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
    const { modalShow, onClose, reloadProject} = this.props;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Project Metadata Input"
      >
        <h2 data-testid="NewProjectTitle">{`Create New Project:`}</h2>
        <ProjectDataForm
          onSaveData={this.handleNewProject}
          onClose={onClose}
          reloadProject={reloadProject}
        />
      </Modal>
    );
  }
}

export default withAlert()(NewProjectModal);

NewProjectModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of project to add the intersection to */
  // projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the intersections after a new one is added */
  reloadProject: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
