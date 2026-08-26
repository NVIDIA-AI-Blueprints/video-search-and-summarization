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
import React, { Component, Fragment } from "react";
import { withAlert } from "react-alert";
import { Form, Header, Loader } from "semantic-ui-react";
import DeleteButton from "../common/DeleteButton";
import {
  setEdited, updateDataValidation, updateDataValue
} from "../common/utils";
import ImportJSONModal from "../modals/ImportJSONModal";
import UploadProjectModal from "../modals/UploadProjectModal";
import UploadProjectForm from "./UploadProjectForm";


import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating sensor data.
 */
class ProjectDataForm extends Component {
  constructor(props) {
    super(props);
    const { useDefaults } = this.props;
    this.state = {
      projectData: {
        name: "",
        calibrationType: "",
      },
      dataValidation: {
        name: false,
        calibrationType: useDefaults,
      },
      isEdited: {
        name: false,
        calibrationType: false,
      },
      jsonModalShow: false,
      projectModalShow: false,
      error: null,
      isLoaded: false,
      calibrationTypeChanged: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.toggleProjectModalShow = this.toggleProjectModalShow.bind(this);
    this.handleJSONUpload = this.handleJSONUpload.bind(this);
    this.handleProjectJSONUpload = this.handleProjectJSONUpload.bind(this);

    // this.setAllEdited = this.setAllEdited.bind(this);
  }

  /**
   * Load the current project data if the project exists (i.e. if
   * editing).
   */
  async componentDidMount() {
    const { projectId } = this.props;
    if (projectId) {
      await axios
        .get(`${API_ENDPOINT}/projects/${projectId}/`)
        .then(res => {
          const projectData = res.data;
          this.setState({ isLoaded: true, projectData});
        })
        .catch(error => {
            this.setState({
              isLoaded: true,
              error
            });
          });
    } else {
      this.setState({ isLoaded: true });
    }
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
    const { projectData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(projectData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      isEdited: newEdited,
      projectData: newData,
      dataValidation: newDataValidation
    });


  }

  /**
   * Toggle if the upload data from JSON modal is shown or not.
   */
  toggleModalShow() {
    const { jsonModalShow } = this.state;
    this.setState({ jsonModalShow: !jsonModalShow });
  }


  /**
   * Toggle if the upload project data from JSON modal is shown or not.
   */
   toggleProjectModalShow() {
    const { projectModalShow } = this.state;

    this.setState({ projectModalShow: !projectModalShow });
  }

  /**
   * Handle uploading data from the JSON input modal. If a key in the JSON
   * object matches an input field in the form, that input field is set as
   * edited, the data is updated with the data from the key, and the data from
   * the key is validated.
   * @param {object} jsonObject JSON input by the user
   */
  async handleJSONUpload(jsonObject) {
    const { projectData, dataValidation, isEdited } = this.state;
    let newData = projectData;
    let newValidation = dataValidation;
    let newEdited = isEdited;
    Object.keys(jsonObject).forEach(key => {
      if (key in projectData) {
        newEdited = setEdited(newEdited, key);
        newData = updateDataValue(newData, key, jsonObject[key]);
        newValidation = updateDataValidation(
          newValidation,
          key,
          jsonObject[key]
        );
      }
    });
    this.setState({
      projectData: newData,
      dataValidation: newValidation,
      isEdited: newEdited
    });
    this.toggleModalShow();
  }

/**
   * Handle uploading data from the JSON input modal. If a key in the JSON
   * object matches an input field in the form, that input field is set as
   * edited, the data is updated with the data from the key, and the data from
   * the key is validated.
   * @param {object} jsonObject JSON input by the user
   */
 async handleProjectJSONUpload(jsonObject) {
  const { projectData, dataValidation, isEdited } = this.state;
  let newData = projectData;
  let newValidation = dataValidation;
  let newEdited = isEdited;
  Object.keys(jsonObject).forEach(key => {
    if (key in projectData) {
      newEdited = setEdited(newEdited, key);
      newData = updateDataValue(newData, key, jsonObject[key]);
      newValidation = updateDataValidation(
        newValidation,
        key,
        jsonObject[key]
      );
    }
  });
  this.setState({
    projectData: newData,
    dataValidation: newValidation,
    isEdited: newEdited
  });
  this.toggleModalShow();
}

  // /**
  //  * Set all fields as edited. Used to show which fields are empty when creating
  //  * a new intersection.
  //  */
  setAllEdited() {
    const { isEdited } = this.state;
    const { projectId } = this.props;
    if (!projectId) {
      let updatedEdited = {};
      Object.keys(isEdited).forEach(key => {
        updatedEdited[key] = true;
      });
      this.setState({ isEdited: updatedEdited });
    }
  }

  render() {
    const { onClose, onSaveData, onDelete, reloadProject  } = this.props;
    const {
      projectData,
      dataValidation,
      isEdited,
      jsonModalShow,
      projectModalShow,
      error,
      isLoaded,
    } = this.state;
    const {
      name,
      // projectType
      calibrationType
    } = projectData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    return (
      <Fragment>
        <ImportJSONModal
          modalShow={jsonModalShow}
          onClose={this.toggleModalShow}
          onUpload={this.handleJSONUpload}
        />
        <UploadProjectModal
          modalShow={projectModalShow}
          onClose={this.toggleProjectModalShow}
          reloadProject={reloadProject}
        />

        <Form>
          <Form.Input
            label="Project Name"
            content="name"
            value={name}
            error={
              isEdited.name && !dataValidation.name
                ? "Invalid Project Name. Maximum Length: 200 Characters."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="ProjNameInput"
          />
          <Form.Group>
            <Form.Radio
              label="GIS Calibration"
              checked={calibrationType === "geo"}
              onChange={e =>

                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "geo"
                })
              }
            />
            <Form.Radio
              label="Cartesian Calibration"
              checked={calibrationType  === "cartesian"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "cartesian"
                })
              }
            />
            {/* <Form.Radio
              label="Floor Plan Calibration"
              checked={calibrationType  === "floorplan"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "floorplan"
                })
              }
            /> */}
            <Form.Radio
              label="MultiCamera Tracking Calibration"
              checked={calibrationType  === "mtmc"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "mtmc"
                })
              }
            />
            <Form.Radio
              label="Image Calibration"
              checked={calibrationType  === "image"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "image"
                })
              }
            />
          </Form.Group>
          <Form.Group>
            <Form.Button
              color="green"
              onClick={() => {
                this.setAllEdited();
                onSaveData(projectData, dataValidation);
              }}
              data-testid="ProjFormApply"
            >
              Apply
            </Form.Button>
            <Form.Button
              color="purple"
              onClick={() => onClose()}
              data-testid="ProjFormClose"
            >
              Close
            </Form.Button>
            {/* <Form.Button
              floated="right"
              color="green"
              onClick={this.toggleModalShow}
            >
              Upload from JSON
            </Form.Button> */}
            {/* <Form.Button
              floated="right"
              color="green"
              onClick={this.toggleProjectModalShow}
            >
              Import Project
            </Form.Button> */}
          </Form.Group>
          {onDelete && (
            <div>
              <hr />
              <Header>DELETE PROJECT</Header>
              <p>
                The button bellow will delete the project and all data within
                the project (sensors).
              </p>
              <DeleteButton
                onConfirmDelete={() => onDelete()}
                label={"DELETE PROJECT"}
              />
            </div>
          )}
        </Form>
      </Fragment>
    );
  }
}

export default withAlert()(ProjectDataForm);

ProjectDataForm.propTypes = {
  /** ID of intersection if form being used to edit existing intersection */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of deleting if editing an existing intersection */
  onDelete: PropTypes.func,
  /** Handle action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired,
  /** Boolean to set if default form dropdown value are used*/
  useDefaults: PropTypes.bool,
  /** Handle the action of closing the form */
  reloadProject: PropTypes.func.isRequired,
};
