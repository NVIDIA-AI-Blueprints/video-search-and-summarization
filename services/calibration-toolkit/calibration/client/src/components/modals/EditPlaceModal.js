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

import PlaceDataForm from "../forms/PlaceDataForm";
import { modalLayer1 } from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Modal to use as popup when editing the data of an existing place.
 */
class EditPlaceModal extends Component {
  constructor(props) {
    super(props);

    this.handleSaveData = this.handleSaveData.bind(this);
    this.handleDelete = this.handleDelete.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Handle event that user saves the data input into the form. The place ID
   * is used to patch the place in the backend. Only data that is changed and
   * is valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSaveData(changedData, dataValidation) {
    const { placeId } = this.props;
    let updatedData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        updatedData = update(updatedData, {
          [key]: { $set: changedData[key] }
        });
      }
    });

    if (Object.entries(updatedData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }

    await axios
      .patch(`${API_ENDPOINT}/places/${placeId}/`, updatedData)
      .then(() => {
        this.props.reloadPlaces();
        this.props.alert.success("Updated place Data");
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
   * Handle the action of the user choosing to delete the place. Deletes the
   * place from the backend based on its ID and reloads the page to show the
   * place removal.
   */
  async handleDelete() {
    const { placeId } = this.props;
    await axios
      .delete(`${API_ENDPOINT}/places/${placeId}/`)
      .then(() => {
        this.props.reloadPlaces();
        this.props.onClose();
      })
      .catch(error => console.error(error));
  }

  render() {
    const { modalShow, onClose, placeId, placeType } = this.props;
    return (
      <Modal
        style={{ overlay: { zIndex: modalLayer1 } }}
        isOpen={modalShow}
        contentLabel="Place Metadata Input"
      >
        <h2>Edit Place:</h2>
        <PlaceDataForm
          placeId={placeId}
          onSaveData={this.handleSaveData}
          onDelete={this.handleDelete}
          onClose={onClose}
          placeType={placeType}
        />
      </Modal>
    );
  }
}

export default withAlert()(EditPlaceModal);

EditPlaceModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the place being edited */
  placeId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the places after editing */
  reloadPlaces: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
