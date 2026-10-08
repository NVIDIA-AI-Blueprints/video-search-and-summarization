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
import { Link } from 'react-router-dom';


import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating sensor data.
 */
class DiscoverSensorForm extends Component {
  constructor(props) {
    super(props);
    this.state = {
      projectData: {
        mmsURL: "",
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
      isLoadingMMS: false
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
        const { mmsURL } = res.data;
        if (mmsURL) {
          const newData = updateDataValue(projectData, "mmsURL", mmsURL);
          const newDataValidation = updateDataValidation(
            dataValidation,
            "mmsURL",
            mmsURL
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
  async importSensors(projectId) {
    const { mmsURL } = this.state.dataValidation;
    if (mmsURL) {
      const { alert} = this.props
    }
    this.setState({ loadSensors: true });

    await axios
      .get(`${API_ENDPOINT}/importSensors/${projectId}/`)
      .then(() => {
        this.props.alert.success("Importing Sensors Completed");
        this.setState({ isLoadingMMS: false });
      })
      .catch(error => {
        this.props.alert.error(error.response.data)
        // this.props.alert.info("Error Occurred while Importing Sensors.");
        // this.props.alert.info("Check Sensors before proceeding with Calibration");
        console.log(error)
        // await axios
          // .delete(`${API_ENDPOINT}/projects/${projectId}/`)
          // .then(res => {})
          // .catch(error => console.error("Delete project error", error));
        this.props.reloadProject();
        this.setState({ isLoadingMMS: false });
      });
  }


  render() {
    const {
      projectId,
      onClose,
      onSaveData,
      onDelete,
    reloadProject } = this.props;
    const {
      projectData,
      dataValidation,
      isEdited,
      error,
      isLoaded,
      isLoadingMMS
    } = this.state;
    const {
      mmsURL
    } = projectData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    if (isLoadingMMS) {
      return (
        <div style={{ textAlign: "center" }}>
          <h1>Importing Sensors from MMS</h1>
          <p>Please wait. This may take a few seconds.</p>
          <Loader active inline="centered" />
        </div>
      );
    }

    return (
      <Fragment>
        <Form>
          <Form.Input
            label="Metropolis Media Server Address"
            content="mmsURL"
            value={mmsURL}
            error={
              isEdited.mmsURL && !dataValidation.mmsURL
                ? "Invalid NVStreamer or VST URL Name. Maximum Length: 200 Characters."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="ProjectMMSInput"
          />
          <Form.Group>
            <Form.Button
              color="green"
              onClick={() => {
                this.setAllEdited();
                onSaveData(projectData, dataValidation);
              }}
              data-testid="DiscSensorFormApply"
            >
              Save Media Server Url
            </Form.Button>
            <Form.Button
              color="green"
              onClick={async () => {
                this.setState({ isLoadingMMS: true });
                await onSaveData(projectData, dataValidation);
                await this.importSensors(projectId);
                // this.setImageResolution();
                this.setState({ isLoadingMMS: false });
                reloadProject();
              }}
            >
              Import and Update Sensors
            </Form.Button>
            {/* <Link to={`/projects/mtmc/${projectId}`}> */}
              <Form.Button
                color="purple"
                onClick={() => this.props.onClose()}
                data-testid="DiscSensorFormClose"
              >
                Close
              </Form.Button>
            {/* </Link> */}
          </Form.Group>
        </Form>
      </Fragment>
    );
  }
}

export default withAlert()(DiscoverSensorForm);

DiscoverSensorForm.propTypes = {
  /** ID of project if form being used to import MMS sensors. */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Reload the sensor when the sensors has been uploaded  */
  reloadProject: PropTypes.func,
  /** Handle action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired
};
