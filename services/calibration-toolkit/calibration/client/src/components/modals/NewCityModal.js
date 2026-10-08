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
import axios from "axios";
import { Loader } from "semantic-ui-react";

import { modalLayer1 } from "../common/utils";
import CityDataForm from "../forms/CityDataForm";
import MapDataForm from "../forms/MapDataForm";
import UploadFloorPlanProjectImageModal from "../modals/UploadFloorPlanProjectImageModal";

import {API_ENDPOINT} from "../common/axios_instances";

/**
 * Modal to use as popup for entering data to add a new city.
 */
class NewCityModal extends Component {
  constructor(props) {
    super(props);
    // const { step } = this.state
    this.state = {
      loadingMapFile: false,
      step: 1,
      uploadFloorPlanModalShow: false,
      cityId : ""

    };

    this.handleNewCity = this.handleNewCity.bind(this);
    this.toggleUploadFloorPlanShow = this.toggleUploadFloorPlanShow.bind(this)

  }

  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user selects to add the city based on the data
   * currently populating the form. If all the data is valid, a new city will
   * be created in the backend with the data provided in the form.
   * @param {object} cityData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the city.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleNewCity(projectData, dataValidation) {
    let cancelSave = false;
    console.log ("test", projectData, dataValidation)
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    const { name, originLat, originLng, mapFile, mapAPIKey, calibrationType } = projectData;
    const { projectId } = this.props;
    projectData = {
      // mapAPIKey,
      name,
      originLat,
      originLng,
      // mapFile,
      project: Number(projectId),
      // sensorOnly,
      calibrationType
    };

    await axios
      .post(`${API_ENDPOINT}/projects/`, projectData)
      .then(async res => {
        // TODO
        this.state.cityId = res.data.id
        // await this.downloadMapFile(res.data.id);
        this.props.alert.success(`Created ${projectData.name}`);
        this.props.reloadProjects();
        this.props.onClose();
      })
      .catch(error => {
        const { alert } = this.props;
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
   * Request the backend to download the map value given the download link
   * provided by the user. If the link doesn't download correctly, a new city
   * will not be created.
   * @param {number} projectId ID representing city to download map file for
   */
  async downloadMapFile(projectId) {
    const { alert } = this.props;
    this.setState({ loadingMapFile: true });
    await axios
      .get(`${API_ENDPOINT}/mapFile/${projectId}/`)
      .then(() => {
        alert.success("Map File Downloaded");
        this.setState({ loadingMapFile: false });
      })
      .catch(async error => {
        alert.error("Invalid Map File Url. Project Deleted.");
        await axios
          .delete(`${API_ENDPOINT}/projects/${projectId}/`)
          .then(res => {})
          .catch(error => console.error("Delete city error", error));
        this.props.reloadProjects();
        this.setState({ loadingMapFile: false });
      });
  }


  /**
   * Toggle if the new sensor modal is shown or not.
   * @param {null} sensorId Pass the null value to show that new sensor is being
   * added (i.e. not editing a sensor)
   */
   toggleUploadFloorPlanShow(sensorId) {
    this.setState({ editingSensor: sensorId });
    const uploadFloorPlanModalShow = !this.state.uploadFloorPlanModalShow;
    this.setState({ uploadFloorPlanModalShow });
  }

  // go back to previous step
  prevStep = () => {
    const { step } = this.state;
    this.setState({ step: step - 1 });
  }
  // proceed to the next step
  nextStep = () => {
    const { step } = this.state;
    this.setState({ step: step + 1 });
  }

  // handle field change
  handleChange = input => e => {
    this.setState({ [input]: e.target.value });
  }

  render() {
    const { step, cityId } = this.state
    const { modalShow, onClose, projectName } = this.props;
    const { loadingMapFile, calibrationType, uploadFloorPlanModalShow
    } = this.state;
    switch (step) {
      case 1:
        return (
          <Modal
            style={{ overlay: { zIndex: modalLayer1 } }}
            isOpen={modalShow}
            contentLabel="City Metadata Input"
          >
            {loadingMapFile ? (
              <div style={{ textAlign: "center" }}>
                <h1>Creating City</h1>
                <p>Please wait. This may take a few minutes.</p>
                <Loader data-testid="CalibLoaderLoading" active inline="centered" />
              </div>
            ) : (
              <div>
                <h2 data-testid="NewCityTitle">{`Create New City in ${projectName}:`}</h2>
                <CityDataForm nextStep={this.nextStep} onSaveData={this.handleNewCity} onClose={onClose} />
              </div>
            )}
          </Modal>
        );
      case 2:
        return (
          <div className="upload map">

            <Modal
              style={{ overlay: { zIndex: modalLayer1 } }}
              isOpen={modalShow}
              contentLabel="City Metadata Input"
            >
              {loadingMapFile ? (
                <div style={{ textAlign: "center" }}>
                  <h1>Creating City</h1>
                  <p>Please wait. This may take a few minutes.</p>
                  <Loader data-testid="CalibLoaderLoading" active inline="centered" />
                </div>
              ) : (
                <div>
                  <h2 data-testid="NewCityTitle">{`Create New City in ${projectName}:`}</h2>
                  <MapDataForm prevStep={this.prevStep}  onSaveData={this.handleNewCity} onClose={onClose} />
                </div>
              )}
            </Modal>
            <UploadFloorPlanProjectImageModal
                modalShow={uploadFloorPlanModalShow}
                cityId={cityId}
                reloadSensors={this.handleNewCity}
                onClose={this.toggleUploadFloorPlanShow}
            />
          </div>
        );

      default:
    }
  }
}

export default withAlert()(NewCityModal);

NewCityModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the city to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** Name of the project to add the city to */
  projectName: PropTypes.string.isRequired,
  /** Handle reloading the projects after new project added */
  reloadProjects: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func

};
