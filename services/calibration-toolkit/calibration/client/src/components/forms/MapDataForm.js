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
import { Header, Form, Loader } from "semantic-ui-react";
import Modal from "react-modal";
import axios from "axios";
import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";
import DeleteButton from "../common/DeleteButton";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating project data.
 */
class MapDataForm extends Component {
  constructor(props) {
    super(props);
    const { useDefaults } = this.props;
    this.state = {
      projectData: {
        name: "",
        // sensorOnly: false,
        calibrationType: "geo",
        originLat: "",
        originLng: "",
        mapFile: "",
        mapAPIKey: "",
        
      },
      dataValidation: {
        name: false,
        // sensorOnly: useDefaults,
        calibrationType: useDefaults,
        originLat: false,
        originLng: false,
        mapFile: false,
        mapAPIKey: false
      },
      isEdited: {
        name: false,
        // sensorOnly: false,
        calibrationType: false,
        originLat: false,
        originLng: false,
        mapFile: false,
        mapAPIKey: false
      },
      error: null,
      isLoaded: false,
      calibrationTypeChanged: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
  }

  /**
   * Load the current project data if the project exists (i.e. if editing).
   */
  async componentDidMount() {
    Modal.setAppElement("body");
    const { projectId } = this.props;
    if (projectId) {
      await axios
        .get(`${API_ENDPOINT}/projects/${projectId}/`)
        .then(res => {
          const projectData = res.data;
          this.setState({ isLoaded: true, projectData });
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

  render() {
    const { onClose, onSaveData, projectId, onDelete, prevStep, toggleUploadFloorPlanShow } = this.props;
    const { projectData, dataValidation, isEdited, isLoaded, error } = this.state;
    const { name, originLat, originLng, mapFile, mapAPIKey, calibrationType} = projectData;

    console.log("MPDF cal", calibrationType)
    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }
    switch(calibrationType){
      case "geo":
        //TODO refactor, handle api and map key
        return (
          <Form>
            <Form.Input
              label="Map File"
              content="mapFile"
              value={mapFile}
              error={
                isEdited.mapFile && !dataValidation.mapFile
                  ? "Invalid Map File"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
            />
            <Form.Input
              label="Google API Key"
              content="mapAPIKey"
              value={mapAPIKey}
              error={
                isEdited.mapAPIKey && !dataValidation.mapAPIKey
                  ? "Invalid API Key"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
            />
            <Form.Group>

              <Form.Input
                label="Origin Latitude"
                content="originLat"
                value={originLat}
                error={
                  isEdited.originLat && !dataValidation.originLng
                    ? "Invalid Latitude"
                    : false
                }
                onChange={(e, data) => this.handleDataChange(e, data)}
              />
              <Form.Input
                label="Origin Longitude"
                content="originLng"
                value={originLng}
                error={
                  isEdited.originLng && !dataValidation.originLng
                    ? "Invalid Longitude"
                    : false
                }
                onChange={(e, data) => this.handleDataChange(e, data)}
              />

            </Form.Group>
            <Form.Group>
              <Form.Button
                color="green"
                onClick={() => {
                  onSaveData(projectData, dataValidation);
                }}
                data-testid="CityFormApply"
              >
                Apply
              </Form.Button>
              {/* <Form.Button
                color="purple"
                onClick={() => prevStep()}
                data-testid="CityFormPrevStep"
              >
                Back
              </Form.Button> */}
              <Form.Button
                color="purple"
                onClick={() => onClose()}
                data-testid="CityFormClose"
              >
                Close
              </Form.Button>
            </Form.Group>
            {/* {projectId && (
              <div id="delete-project" style={{ padding: "2em 0" }}>
                <hr />
                <Header>DELETE PROJECT</Header>
                <p>
                  The button bellow will delete project and all data within the project
                  (intersections and sensors).
                </p>
                <DeleteButton
                  onConfirmDelete={() => onDelete()}
                  label={"DELETE CITY"}
                  data-testid="CityFormDelete"
                />
              </div>
            )} */}
          </Form>
        )
        case "mtmc":
          //TODO refactor
          return (
            <Form>
              {/* <Form.Group>
                <Form.Button
                  className="ui compact right floated button"
                  onClick={() => toggleUploadFloorPlanShow(projectId)}
                  color="green"
                  icon="sensor"
                  label="Upload FloorPlan"
                  size="tiny"

                />
              </Form.Group> */}
              <Form.Group>
                <Form.Button
                  color="green"
                  onClick={() => {
                    onSaveData(projectData, dataValidation);
                  }}
                  data-testid="CityFormApply"
                >
                  Apply
                </Form.Button>
                {/* <Form.Button
                  color="blue"
                  onClick={() => prevStep()}
                  data-testid="CityFormPrevStep"
                >
                  Back
                </Form.Button> */}
                <Form.Button
                  color="purple"
                  onClick={() => onClose()}
                  data-testid="CityFormClose"
                >
                  Close
                </Form.Button>
              </Form.Group>
              {projectId && (
                <div id="delete-project" style={{ padding: "2em 0" }}>
                  <hr />
                  <Header>DELETE CITY</Header>
                  <p>
                    The button bellow will delete project and all data within the project
                    (intersections and sensors).
                  </p>
                  <DeleteButton
                    onConfirmDelete={() => onDelete()}
                    label={"DELETE CITY"}
                    data-testid="CityFormDelete"
                  />
                </div>
              )}
            </Form>

          )  
      default:
        return(
          <Form>
             <Form.Group>
                <Form.Button
                  color="green"
                  onClick={() => {
                    onSaveData(projectData, dataValidation);
                  }}
                  data-testid="CityFormApply"
                >
                  Apply
                </Form.Button>
                <Form.Button
                  color="blue"
                  onClick={() => prevStep()}
                  data-testid="CityFormPrevStep"
                >
                  Back
                </Form.Button>
                <Form.Button
                  color="purple"
                  onClick={() => onClose()}
                  data-testid="CityFormClose"
                >
                  Close
                </Form.Button>
              </Form.Group>
              {/* {projectId && (
                <div id="delete-project" style={{ padding: "2em 0" }}>
                  <hr />
                  <Header>DELETE CITY</Header>
                  <p>
                    The button bellow will delete project and all data within the project
                    (intersections and sensors).
                  </p>
                  <DeleteButton
                    onConfirmDelete={() => onDelete()}
                    label={"DELETE CITY"}
                    data-testid="CityFormDelete"
                  />
                </div>
              )} */}
          </Form>
        )
      }
  }
}

export default withAlert()(MapDataForm);

MapDataForm.propTypes = {
  /** ID of the project if form being used to edit existing project */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of clicking to save the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of deleting the project if editing existing project */
  onDelete: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired,
  /** Boolean to set if default form dropdown value are used*/
  useDefaults: PropTypes.bool,
  /** Handle the action of moving to previous step in form */
  prevStep: PropTypes.func.isRequired,
  /** Handle the action of toggling upload floor plan */
  // toggleUploadFloorPlanShow: PropTypes.func.isRequired,
};
