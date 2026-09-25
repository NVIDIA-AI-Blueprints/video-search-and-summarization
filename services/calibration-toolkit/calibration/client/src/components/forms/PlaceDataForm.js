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


import React, { Component, Fragment } from "react";
import PropTypes from "prop-types";
import { Form, Header, Loader } from "semantic-ui-react";
import axios from "axios";
import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";
import DeleteButton from "../common/DeleteButton";
import ImportJSONModal from "../modals/ImportJSONModal";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating corridor data.
 */
class PlaceDataForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      placeData: {
        name: "",
        originLat: "",
        originLng: ""
      },
      dataValidation: {
        name: false,
        originLat: false,
        originLng: false
      },
      isEdited: {
        name: false,
        originLat: false,
        originLng: false
      },
      jsonModalShow: false,
      error: null,
      isLoaded: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.handleJSONUpload = this.handleJSONUpload.bind(this);
    this.setAllEdited = this.setAllEdited.bind(this);
  }

  /**
   * Load the current corridor data if the corridor exists (i.e. if editing).
   */
  async componentDidMount() {
    const { placeId } = this.props;
    if (placeId) {
      await axios
        .get(`${API_ENDPOINT}/places/${placeId}/`)
        .then(res => {
          const placeData = DOMPurify.sanitize(res.data);
          this.setState({ isLoaded: true, placeData });
        })
        .catch(error => {
          this.setState({
            isLoaded: true,
            error
          });
        });
    } else {
      this.setState({ isLoaded: true });
    }
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
    const { placeData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(placeData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      isEdited: newEdited,
      placeData: newData,
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

  /**
   * Handle uploading data from the JSON input modal. If a key in the JSON
   * object matches an input field in the form, that input field is set as
   * edited, the data is updated with the data from the key, and the data from
   * the key is validated.
   * @param {object} jsonObject JSON input by the user
   */
  handleJSONUpload(jsonObject) {
    const { platData, dataValidation, isEdited } = this.state;
    let newData = platData;
    let newValidation = dataValidation;
    let newEdited = isEdited;
    Object.keys(jsonObject).forEach(key => {
      if (key in platData) {
        newEdited = setEdited(newEdited, key);
        newData = updateDataValue(newData, key, jsonObject[key]);
        newValidation = updateDataValidation(
          newValidation,
          key,
          jsonObject[key]
        );
      }
    });
    this.setState({
      platData: newData,
      dataValidation: newValidation,
      isEdited: newEdited
    });
    this.toggleModalShow();
  }

  /**
   * Set all fields as edited. Used to show which fields are empty when creating
   * a new corridor.
   */
  setAllEdited() {
    const { isEdited } = this.state;
    const { placeId } = this.props;
    if (!placeId) {
      let updatedEdited = {};
      Object.keys(isEdited).forEach(key => {
        updatedEdited[key] = true;
      });
      this.setState({ isEdited: updatedEdited });
    }
  }

  render() {
    const { onClose, onSaveData, onDelete, placeType } = this.props;
    const {
      placeData,
      dataValidation,
      isEdited,
      jsonModalShow,
      error,
      isLoaded
    } = this.state;
    const { name, originLat, originLng } = placeData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    return (
      <Fragment>
        <ImportJSONModal
          modalShow={jsonModalShow}
          onClose={this.toggleModalShow}
          onUpload={this.handleJSONUpload}
        />
        <Form>
          <Form.Input
            label={`${placeType} NAME`}
            content="name"
            value={name}
            error={
              isEdited.name && !dataValidation.name
                ? `Invalid ${placeType} Name. Maximum Length: 200 Characters.`
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
          />
          <Form.Group widths="equal">
            <Form.Input
              label={`${placeType} latitude`}
              content="originLat"
              value={originLat}
              error={
                isEdited.originLat && !dataValidation.originLat
                  ? "Invalid Latitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
            />
            <Form.Input
              label={`${placeType} Longitude`}
              content="originLng"
              value={originLng}
              error={
                isEdited.originLng && !dataValidation.originLng
                  ? "Invalid Longitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
            />
          </Form.Group>
          <Form.Group>
            <Form.Button
              color="green"
              onClick={() => {
                this.setAllEdited();
                onSaveData(placeData, dataValidation);
              }}
            >
              Apply
            </Form.Button>
            <Form.Button color="purple" onClick={() => onClose()}>
              Close
            </Form.Button>
            <Form.Button
              floated="right"
              color="green"
              onClick={this.toggleModalShow}
            >
              Upload from JSON
            </Form.Button>
          </Form.Group>
          {onDelete && (
            <div>
              <hr />
              <Header>{`DELETE ${placeType}`}</Header>
              <p>
                {`The button bellow will delete the ${placeType} and all data within
                the ${placeType}.`}
              </p>
              <DeleteButton
                onConfirmDelete={() => onDelete()}
                label={`DELETE ${placeType}`}
              />
            </div>
          )}
        </Form>
      </Fragment>
    );
  }
}

export default PlaceDataForm;

PlaceDataForm.propTypes = {
  /** ID of corridor if form is being used to edit existing corridor */
  corridorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of clicking to save the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of deleting the corridor if editing existing corridor */
  onDelete: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired
};
