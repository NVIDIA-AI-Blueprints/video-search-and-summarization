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
import update from "immutability-helper";
import axios from "axios";
import { Grid } from "semantic-ui-react";
import DiscoverSensorForm from "../../../forms/DiscoverSensorForm";
import TopMenu from "../../../common/TopMenu/TopMenu"
import ImageLeftMenu from "../../../common/LeftMenu/ImageLeftMenu";
import MainContainer from "../../../common/MainContainer/MainContainer";
import './ImageDiscoverSensorsPage.css'

import { API_ENDPOINT } from "../../../common/axios_instance";

import DOMPurify from "dompurify";

/**
 * Modal to use as popup for adding an image/screenshot to the city backend
 * via a file upload or an RTSP stream.
 */
class ImageDiscoverSensorsPage extends Component {
  constructor(props) {
    super(props);
    this.state ={
      projectId: null
    }
    this.handleSensors = this.handleSensors.bind(this);
    this.goBack = this.goBack.bind(this)
    this.loadProject = this.loadProject.bind(this);

  }
  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    // Modal.setAppElement("body");
    this.loadProject();

  }

  /**
   * Load all the data of the particular Project. The Project ID is passed via the
   * URL.
   */
   async loadProject() {
    console.log("UFPIP", this.props)
    const { match } = this.props;
    const { projectId } = match.params;
    await axios.get(`${API_ENDPOINT}/projects/${projectId}/`).then(res => {
      const projectName = res.data.name;
      const { placeTypes_set,placeTypeHierarchy, calibrationType } = res.data;
      this.setState({
        isLoaded: true,
        projectName,
        placeTypes_set,
        placeTypeHierarchy,
        calibrationType,
        projectId
      });
    });
    this.forceUpdate()
  }


  /**
   * Go back to the Sensors tab in the city page.
   */
   goBack = () => {
    const { projectId } = this.state;
    let path = `/projects/image/${projectId}/`;
    let { history } = this.props;
    history.push({
      pathname: path,
      // state: { prevPath: "Sensors" }
    });
  };



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
    const { match } = this.props;
    const { projectId } = match.params;
    let projectData = {};
    console.log("dsp", changedData)
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
        this.loadProject();
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
  render() {
    const { modalShow, onClose } = this.props;
    const projectId = DOMPurify.sanitize(this.props.match.params.projectId)
    console.log("ufppip", projectId, this.props.match, this.props)
    return (
      <div>
        <TopMenu />
        <ImageLeftMenu projectId={projectId} />
        <MainContainer>
          <h1 style={{ display: "inline", wordWrap: "break-word" }}>
            {projectId}
          </h1>
          <h1></h1>
          <Grid
            // style={{ overlay: { zIndex: modalLayer1 } }}
            // isOpen={modalShow}
            // contentLabel="City FloorPlan Input"
          >
            {/* Test */}
            <DiscoverSensorForm
              projectId={projectId}
              onSaveData={this.handleSensors}
              reloadProject={this.loadProject}
              onClose={this.goBack}
            />
          </Grid>
        </MainContainer>
      </div>
    );
  }
}

export default withAlert()(ImageDiscoverSensorsPage);

ImageDiscoverSensorsPage.propTypes = {
  /** Indiactor whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the city the image is being uploaded to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the city after uploading the image */
  // reloadCity: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
