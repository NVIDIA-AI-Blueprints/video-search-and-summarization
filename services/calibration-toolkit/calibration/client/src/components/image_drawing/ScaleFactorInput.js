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
import { Form, Button } from "semantic-ui-react";
import Modal from "react-modal";
import update from "immutability-helper";
import { withAlert } from "react-alert";
import axios from "axios";

import {
  updateDataValue,
  updateDataValidation,
  modalLayer2
} from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";

const customStyles = {
  content: {
    top: "50%",
    left: "50%",
    right: "50%",
    bottom: "auto",
    marginRight: "-50%",
    transform: "translate(-50%, -50%)"
  },
  overlay: { zIndex: modalLayer2 }
};

/**
 * Modal to change the current center view of the google map.
 */
class ScaleFactorInput extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapData: {
        scaleFactor: 1.0
      },
      dataValidation: {
        scaleFactor: true,
      }
    };

    this.updateCrop = this.updateCrop.bind(this);
    this.loadDefaults = this.loadDefaults.bind(this);
    this.handleSumbit = this.handleSumbit.bind(this);
  }

  /**
   * Setup the modal and update the center when the component is mounted.
   */
  componentDidMount() {
    Modal.setAppElement("body");
    this.updateCrop();
  }

  /**
   * Update the center then the component updates.
   * @param {object} prevProps Previous props before the update
   */
  componentDidUpdate(prevProps) {
    const { scaleFactor } = this.props;
    if (scaleFactor !== prevProps.scaleFactor) {
      this.updateCrop();
    }
  }

  /**
   * Load the default map center (i.e. origin latitude and longitude associated
   * with the current map).
   */
  async loadDefaults() {
    const { id } = this.props;
    let { mapData, dataValidation } = this.state;
    await axios
      .get(`${API_ENDPOINT}/projects/${id}/`)
      .then(res => {
        const { scaleFactor } = res.data;
        mapData = update(mapData, { scaleFactor: { $set: scaleFactor } });
        dataValidation = update(dataValidation, { scaleFactor: { $set: true } });
        this.setState({ dataValidation, mapData });
      })
      .catch(error => console.error(error));
  }

  /**
   * Update the crop saved in the component's state.
   */
  updateCrop() {
    let { mapData } = this.state;
    // console.log("newcart", this.props)
    const { scaleFactor } = this.props;
    mapData = update(mapData, { scaleFactor: { $set: scaleFactor } });
    this.setState({ mapData });
  }

  /**
   * Handle data changed in any form input field. Sets the value in the state
   * based off of what was written in the field, and runs data validation to see
   * if the entered data is accurate.
   * @param {object} e Data changed event, unused
   * @param {object} data Information on the data in the form
   */
  handleDataChange(e, data) {
    const { content, value } = data;
    const { mapData, dataValidation } = this.state;
    const newData = updateDataValue(mapData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      mapData: newData,
      dataValidation: newDataValidation
    });
  }

  /**
   * Handle the submission of the inputted latitude and longitude coordinates.
   */
  async handleSumbit() {
    const { mapData, dataValidation } = this.state;
    let validCoords = true;
    Object.keys(dataValidation).forEach(coord => {
      if (!dataValidation[coord]) {
        validCoords = false;
        this.props.alert.error(`Invalid ${coord}`);
      }
    });
    if (validCoords) {
      const {  onSubmit, reloadPage, onClose } = this.props;
      let newCrop = {
        scaleFactor: Number(mapData.scaleFactor)
      };
      await onSubmit(newCrop);
      await reloadPage();
      onClose();
      return;
    }
  }

  render() {
    const { mapData, dataValidation } = this.state;
    const { scaleFactor } = mapData;
    const { modalShow, onClose } = this.props;
    return (
      <Modal style={customStyles} isOpen={modalShow}>
        <Form>
          <Form.Group widths="equal">
            <Form.Input
              label="Scale Factor"
              content="scaleFactor"
              value={scaleFactor}
              error={(!dataValidation.scaleFactor) ? "Invalid Scale Factor" : false}
              onChange={(e, data) => this.handleDataChange(e, data)}
            />

          </Form.Group>
        </Form>
        <Button color="green" floated="left" onClick={this.handleSumbit}>
          Submit
        </Button>
        <Button
          color="purple"
          floated="left"
          onClick={() => this.loadDefaults()}
        >
          Load Defaults
        </Button>
        <Button negative floated="right" onClick={onClose}>
          Cancel
        </Button>
      </Modal>
    );
  }
}

export default withAlert()(ScaleFactorInput);

ScaleFactorInput.propTypes = {
  /** Indicator whether or not the new lat/lng modal is being shown */
  modalShow: PropTypes.bool,
  /** ID of the object to save the map view to */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  // /** Type of object to save the map view to */
  // sensor: PropTypes.object.isRequired,
  /** Current size of the Warped Image */
  scaleFactor: PropTypes.object,
  /** Handle the action of submitting the new map lat/lng */
  onSubmit: PropTypes.func,
  /** Reload the page with new view */
  reloadPage: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
