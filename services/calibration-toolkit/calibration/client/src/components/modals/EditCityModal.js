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
import PropTypes, { object } from "prop-types";
import { withAlert } from "react-alert";
import Modal from "react-modal";
import axios from "axios";
import update from "immutability-helper";
import { Loader } from "semantic-ui-react";

import { modalLayer1 } from "../common/utils";
import CityDataForm from "../forms/CityDataForm";
import MapDataForm from "../forms/MapDataForm";
import UploadFloorPlanProjectImageModal from "../modals/UploadFloorPlanProjectImageModal";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for editing the data of an existing city.
 */
class EditCityModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      step: 1,
      loadingMapFile: false,
      validMapFile: null,
      previousMapFile: null,
      uploadFloorPlanModalShow: false,
      calibrationType: null,
      sensorsList :[]
    };

    this.handleSaveData = this.handleSaveData.bind(this);
    this.downloadMapFile = this.downloadMapFile.bind(this);
    this.revertMapFile = this.revertMapFile.bind(this);
    this.backupMapFile = this.backupMapFile.bind(this);
    this.toggleUploadFloorPlanShow = this.toggleUploadFloorPlanShow.bind(this)

  }

  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
    Modal.setAppElement("body");
  }



    /**
   * Backup the previous map file before downloading a new map file in case the
   * new map file errors while downloading the map.
   */
    async getSensors() {
    const { cityId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/cities/${cityId}/`)
      .then(res => {
        this.setState({ sensorsList: res.data.sensor_set });
      })
      .catch(error => {});
  }


  async pushSensorData(data){
    const {id, coordinates} = data
    await axios
    .patch(`${API_ENDPOINT}/sensors/${id}/`, {
      coordinates
    })
    .then()
    .catch(error => console.error("err", error));
  }

  updateSensorsData(cityData){
    this.getSensors()
    const {sensorsList} = this.state
    console.log("ecm", cityData, sensorsList)
    sensorsList.forEach( sensorName => {
      console.log("output p", sensorName)
      // const sensor = sensors.filter(({sensorId})=> sensorName.includes(sensorId))
      // const sensor = sensors.filter(item => item.sensorId === sensorName)

      // console.log("output pu", sensor[0])
      // const {id, sensorId}  = sensor[0]

      // console.log("output pus", sensor, coordinatessensorId)
      // const coordinates = JSON.stringify((labelData.mapLabelData[sensorId]));
      // console.log("output puc", id, coordinates)
      // this.pushSensorData({id,})
      })
  }

  /**
   * Handle event that user saves the data input into the form. The city ID is
   * used to patch the city in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSaveData(changedData, dataValidation) {
    const { cityId } = this.props;
    let cityData = {};
    console.log("ECM", dataValidation)
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        cityData = update(cityData, { [key]: { $set: changedData[key] } });
      }
    });

    if (Object.entries(cityData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }

    if (Object.keys(cityData).includes("calibrationType")){
      console.log(cityData)
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
      .patch(`${API_ENDPOINT}/cities/${cityId}/`, cityData)
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
    this.props.reloadCity();
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


  /**
   * Backup the previous map file before downloading a new map file in case the
   * new map file errors while downloading the map.
   */
  async backupMapFile() {
    const { cityId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/cities/${cityId}/`)
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
    const { cityId } = this.props;
    const { alert } = this.props;
    this.setState({ loadingMapFile: true });
    await axios
      .get(`${API_ENDPOINT}/mapFile/${cityId}/`)
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
    const { cityId, alert } = this.props;
    const { previousMapFile } = this.state;
    await axios
      .patch(`${API_ENDPOINT}/cities/${cityId}/`, { mapFile: previousMapFile })
      .then(() => {
        alert.success("Previous Map File Restored");
      })
      .catch(error => {});
  }

  render() {
    const { modalShow, onClose, cityId } = this.props;
    const { step, loadingMapFile, uploadFloorPlanModalShow } = this.state;
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
                <h2 data-testid="EditCityTitle">{`Edit Location`}</h2>
                <CityDataForm nextStep={this.nextStep} cityId={cityId}  onSaveData={this.handleSaveData} onClose={onClose} />
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
                  <h2 data-testid="EditCityTitle">{`Edit Location`}</h2>
                  <MapDataForm prevStep={this.prevStep} cityId={cityId} onSaveData={this.handleSaveData} onClose={onClose} toggleUploadFloorPlanShow={this.toggleUploadFloorPlanShow} />
                </div>
              )}
            </Modal>
            <UploadFloorPlanProjectImageModal
                modalShow={uploadFloorPlanModalShow}
                projectId={cityId}
                reloadCity={this.props.reloadCity}
                onClose={this.toggleUploadFloorPlanShow}
            />
          </div>
        );

      default:
    }
  }
}

export default withAlert()(EditCityModal);

EditCityModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the city being edited by the modal */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the city data after editing the city */
  reloadCity: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired,
  /** Handle the action of deleting the city that is currently being edited */
  onDelete: PropTypes.func.isRequired
};
