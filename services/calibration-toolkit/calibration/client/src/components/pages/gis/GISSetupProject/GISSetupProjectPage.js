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
import GISLeftMenu from "../../../common/LeftMenu/GISLeftMenu";
import MainContainer from "../../../common/MainContainer/MainContainer";
import './GISSetupProjectPage.css'
import MapDataForm from "../../../forms/MapDataForm";
import { Loader } from "semantic-ui-react";
import DOMPurify from "dompurify";

import { API_ENDPOINT } from "../../../common/axios_instance";

/**
 * Modal to use as popup for adding an image/screenshot to the project backend
 * via a file upload or an RTSP stream.
 */
class GISSetupProjectPage extends Component {
  constructor(props) {
    super(props);
    this.state ={
      projectId: null,
      step: 1,
      loadingMapFile: false,
      validMapFile: null,
      previousMapFile: null,
      sensorsList: []

    }
    this.handleSaveData = this.handleSaveData.bind(this);
    this.goBack = this.goBack.bind(this)
    this.prevStep = this.prevStep.bind(this)
    this.downloadMapFile = this.downloadMapFile.bind(this);
    this.revertMapFile = this.revertMapFile.bind(this);
    this.backupMapFile = this.backupMapFile.bind(this);
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
   * Handle event that user saves the data input into the form. The project ID is
   * used to patch the project in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
   async handleSaveData(changedData, dataValidation) {
    const { match } = this.props;
    const { projectId } = match.params;    let projectData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        projectData = update(projectData, { [key]: { $set: changedData[key] } });
      }
    });

    if (Object.entries(projectData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }

    if (Object.keys(projectData).includes("calibrationType")){
      console.log(projectData)
      this.props.alert.show("Calibration Type has Changed. This may cause issues with your project");
      // return;
    }


    // if (dataValidation.sensorOnly){
    //   await this.confirm
    // }

    if (dataValidation.mapFile) {
      await this.backupMapFile();
    }

    await axios
      .patch(`${API_ENDPOINT}/projects/${projectId}/`, projectData)
      .then(() => {
        if (!dataValidation.mapFile) {
          this.props.alert.success("Updated Metadata");
        }
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

    if (dataValidation.mapFile) {
      await this.downloadMapFile();
      const { validMapFile } = this.state;
      if (!validMapFile) {
        await this.revertMapFile();
      }
    }
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
   * Go back to the Sensors tab in the project page.
   */
   goBack = () => {
    const { projectId } = this.state;
    let path = `/projects/geo/${projectId}/`;
    let { history } = this.props;
    history.push({
      pathname: path,
      // state: { prevPath: "Sensors" }
    });
  };

    // go back to previous step
    prevStep = () => {
      const { step } = this.state;
      this.setState({ step: step - 1 });
    }


  /**
   * Backup the previous map file before downloading a new map file in case the
   * new map file errors while downloading the map.
   */
   async backupMapFile() {
    const { match } = this.props;
    const { projectId } = match.params;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        this.setState({ previousMapFile: res.data.mapFile });
      })
      .catch(error => {});
  }

  /**
   * Request the backend to download the map value given the download link
   * provided by the user.
   */
  async downloadMapFile() {
    const { match } = this.props;
    const { projectId } = match.params;
    const { alert } = this.props;
    this.setState({ loadingMapFile: true });
    await axios
      .get(`${API_ENDPOINT}/mapFile/${projectId}/`)
      .then(() => {
        alert.success("Map File Downloaded");
        this.setState({ loadingMapFile: false, validMapFile: true });
      })
      .catch(error => {
        alert.error("Invalid Map File Url");
        this.setState({ loadingMapFile: false, validMapFile: false });
      });
  }

  /**
   * Revert to the previous map file if there is an error while downloading the
   * current map file requested by the user.
   */
  async revertMapFile() {
    const { match } = this.props;
    const { projectId } = match.params;
    const { alert } = this.props;
    const { previousMapFile } = this.state;
    await axios
      .patch(`${API_ENDPOINT}/projects/${projectId}/`, { mapFile: previousMapFile })
      .then(() => {
        alert.success("Previous Map File Restored");
      })
      .catch(error => {});
  }

  render() {
    const { modalShow, onClose } = this.props;
    const projectId = DOMPurify.sanitize(this.props.match.params.projectId)
    const { step, loadingMapFile } = this.state;

    console.log("ufppip", projectId, this.props.match, this.props)
    return (
      <div>
        <TopMenu />
        <GISLeftMenu projectId={projectId} />
        <MainContainer>
        {loadingMapFile ? (
              <div style={{ textAlign: "center" }}>
                <h1>Downloading Map File</h1>
                <p>Please wait. This may take a few minutes.</p>
                <Loader data-testid="CalibLoaderLoading" active inline="centered" />
              </div>
            ) : (
            <div style={{ textAlign: "left" }}>
              <h1></h1>
              <h1/>
                <h1>Setup GIS Project Details</h1>
                <h1/>

                <Grid
                  // style={{ overlay: { zIndex: modalLayer1 } }}
                  // isOpen={modalShow}
                  // contentLabel="project FloorPlan Input"
                >
                  {/* Test */}
                  <MapDataForm
                    projectId={projectId}
                    onSaveData={this.handleSaveData}
                    reloadProject={this.loadProject}
                    prevStep={this.prevStep}
                    onClose={this.goBack}
                  />
                </Grid>
          </div>)}
        </MainContainer>
      </div>
    );
  }
}

export default withAlert()(GISSetupProjectPage);

GISSetupProjectPage.propTypes = {
  /** Indiactor whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project the image is being uploaded to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the project after uploading the image */
  // reloadproject: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
