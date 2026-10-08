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
import { Form, Loader } from "semantic-ui-react";
import {
  setEdited, updateDataValidation, updateDataValue
} from "../common/utils";


import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating sensor data.
 */
class UploadWebApiForm extends Component {
  constructor(props) {
    super(props);
    this.state = {
      projectData: {
        webApiUrl: "",
      },
      dataValidation: {
        url: false,
      },
      isEdited: {
        url: false,
      },
      jsonModalShow: false,
      error: false,
      isLoaded: false,
      isLoadingWebApi: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.setAllEdited = this.setAllEdited.bind(this);
  }

    /**
   * Get rtsp url oo component mount
   */
  async componentDidMount() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const { projectData, dataValidation } = this.state;
        const { webApiUrl } = res.data;
        if (webApiUrl) {
          const newData = updateDataValue(projectData, "webApiUrl", webApiUrl);
          const newDataValidation = updateDataValidation(
            dataValidation,
            "webApiUrl",
            webApiUrl
          );
          this.setState({
            isLoaded: true,
            projectData: newData,
            dataValidation: newDataValidation
          });
        } else {
          this.setState({ isLoaded: true });
        }
      });
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

    /**
   * Request the backend to import the new sensors
   */
  async uploadData(projectId) {
    const { mmsUrl } = this.state.dataValidation;
    if (mmsUrl) {
      const { alert} = this.props
    }
    this.setState({ loadSensors: true });

    await axios
      .get(`${API_ENDPOINT}/uploadWebApi/${projectId}/`)
      .then(() => {
        this.props.alert.success("Uploading Data Completed");
        this.setState({ isLoadingWebApi: false });
      })
      .catch((error) => {

        this.props.alert.error("Error while uploading data");
        // await axios
          // .delete(`${API_ENDPOINT}/cities/${projectId}/`)
          // .then(res => {})
          // .catch(error => console.error("Delete city error", error));
        this.props.reloadProjects();
        this.setState({ isLoadingWebApi: false });
      });
  }


  render() {
    const { 
      projectId,
      onClose, 
      onSaveData, 
      onDelete,
    reloadProjects } = this.props;
    const {
      projectData,
      dataValidation,
      isEdited,
      error,
      isLoaded,
      isLoadingWebApi
    } = this.state;
    const {
      webApiUrl
    } = projectData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    if (isLoadingWebApi) {
      return (
        <div style={{ textAlign: "center" }}>
          <h1>Uploading Data to Web API</h1>
          <p>Please wait. This may take a few seconds.</p>
          <Loader active inline="centered" />
        </div>
      );
    }
    
    return (
      <Fragment>
        <Form>
          <Form.Input
            label="Metropolis Web API Server Address"
            content="webApiUrl"
            value={webApiUrl}
            error={
              (isEdited.webApiUrl && !dataValidation.webApiUrl)
                ? "Invalid Web API URL Name."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="WebAPIInput"
          />
          <Form.Group>
            <Form.Button
              color="green"
              disabled={!dataValidation.webApiUrl}
              onClick={() => {
                this.setAllEdited();
                onSaveData(projectData, dataValidation);
              }}
              data-testid="WebApiFormApply"
            >
              Save Web API Url
            </Form.Button>
            <Form.Button
              color="green"
              disabled={!dataValidation.webApiUrl}
              onClick={async () => {
                this.setState({ isLoadingWebApi: true });
                //await this.props.downloadData()
                await onSaveData(projectData, dataValidation);
                await this.uploadData(projectId);
                // this.setImageResolution();
                this.setState({ isLoadingWebApi: false });
                this.props.reloadProjects();
              }}
            >
              Upload Data
            </Form.Button>
            <Form.Button
              color="purple"
              onClick={() => onClose()}
              data-testid="DiscSensorFormClose"
            >
              Close
            </Form.Button>
          </Form.Group>
        </Form>
      </Fragment>
    );
  }
}

export default withAlert()(UploadWebApiForm);

UploadWebApiForm.propTypes = {
  /** ID of city if form being used to import MMS sensors. */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Reload the sensor when the sensors has been uploaded  */
  reloadProjects: PropTypes.func,                     
  /** Handle action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired,
  /** Handle reloading the cities after new city added */
  downloadData: PropTypes.func,
};
