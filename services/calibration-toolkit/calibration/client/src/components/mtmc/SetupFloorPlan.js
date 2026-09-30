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

import {API_ENDPOINT} from "../common/axios_instances";

/**
 * Modal to use as popup for entering data to add a new city.
 */
class SetupFloorPlanModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      loadingMapFile: false,
      step: 1,


    };

    this.handleNewCity = this.handleNewCity.bind(this);
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
  async handleNewCity(cityData, dataValidation) {
    let cancelSave = false;
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    const { name, originLat, originLng, mapFile, mapAPIKey, calibrationType } = cityData;
    const { projectId } = this.props;
    cityData = {
      mapAPIKey,
      name,
      originLat,
      originLng,
      mapFile,
      project: Number(projectId),
      // sensorOnly,
      calibrationType
    };

    await axios
      .post(`${API_ENDPOINT}/cities/`, cityData)
      .then(async res => {
        const cityId = res.data.id;
        await this.downloadMapFile(cityId);
        this.props.alert.success(`Created ${cityData.name}`);
        this.props.reloadCities();
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
   * @param {number} cityId ID representing city to download map file for
   */
  async downloadMapFile(cityId) {
    const { alert } = this.props;
    this.setState({ loadingMapFile: true });
    await axios
      .get(`${API_ENDPOINT}/mapFile/${cityId}/`)
      .then(() => {
        alert.success("Map File Downloaded");
        this.setState({ loadingMapFile: false });
      })
      .catch(async error => {
        alert.error("Invalid Map File Url. City Deleted.");
        await axios
          .delete(`${API_ENDPOINT}/cities/${cityId}/`)
          .then(res => {})
          .catch(error => console.error("Delete city error", error));
        this.props.reloadCities();
        this.setState({ loadingMapFile: false });
      });
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
    const { modalShow, onClose, projectName } = this.props;
    const { loadingMapFile } = this.state;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
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
            <CityDataForm onSaveData={this.handleNewCity} onClose={onClose} />
          </div>
        )}
      </Modal>
    );
  }
}

export default withAlert()(SetupFloorPlanModal);

SetupFloorPlanModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the city to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** Name of the project to add the city to */
  projectName: PropTypes.string.isRequired,
  /** Handle reloading the cities after new city added */
  reloadCities: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
