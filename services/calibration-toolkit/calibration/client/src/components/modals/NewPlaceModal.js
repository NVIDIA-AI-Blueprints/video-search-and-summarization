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
import update from "immutability-helper";
import axios from "axios";

import { modalLayer1 } from "../common/utils";
import PlaceDataForm from "../forms/PlaceDataForm";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup for entering data to add a new place.
 */
class NewPlaceModal extends Component {
  constructor(props) {
    super(props);

    this.handleNewPlace = this.handleNewPlace.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user selects to add the place based on the data
   * currently populating the form. If all the data is valid, a new place
   * will be created in the backend with the data provided in the form.
   * @param {object} placeData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the place.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleNewPlace(placeData, dataValidation) {
    const { cityId, projectId, placeTypeId } = this.props;
    let cancelSave = false;
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    placeData = update(placeData, {
      project: { $set: projectId }
    });
    placeData = update(placeData, {
      city: { $set: cityId }
    });
    placeData = update(placeData, {
      placeType: { $set: placeTypeId }
    });

    await axios
      .post(`${API_ENDPOINT}/places/`, placeData)
      .then(() => {
        this.props.alert.success(`Created ${placeData.name}`);
        this.props.reloadPlaces();
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

  render() {
    const { modalShow, onClose, cityName, placeType } = this.props;

    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
      >
        <h2>{`Create New ${placeType} in ${cityName}:`}</h2>
        <PlaceDataForm
          onSaveData={this.handleNewPlace}
          onClose={onClose}
          placeType={placeType}
        />
      </Modal>
    );
  }
}

export default withAlert()(NewPlaceModal);

NewPlaceModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the place to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** ID of the city to add the place to */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Name of the city to add the place to */
  cityName: PropTypes.string,
  /** Handle reloading the places when a new one has been added */
  reloadPlaces: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
