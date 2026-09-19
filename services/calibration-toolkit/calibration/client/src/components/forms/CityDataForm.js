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
import { Header, Form, Loader } from "semantic-ui-react";
import Modal from "react-modal";
import axios from "axios";
import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";
import DeleteButton from "../common/DeleteButton";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating city data.
 */
class CityDataForm extends Component {
  constructor(props) {
    super(props);
    const { useDefaults } = this.props;
    this.state = {
      cityData: {
        name: "",
        // sensorOnly: false,
        calibrationType: "gis",
        // originLat: "",
        // originLng: "",
        // mapFile: "",
        // mapAPIKey: ""
      },
      dataValidation: {
        name: false,
        // sensorOnly: useDefaults,
        calibrationType: useDefaults,
        // originLat: false,
        // originLng: false,
        // mapFile: false,
        // mapAPIKey: false
      },
      isEdited: {
        name: false,
        // sensorOnly: false,
        calibrationType: false,
        // originLat: false,
        // originLng: false,
        // mapFile: false,
        // mapAPIKey: false
      },
      error: null,
      isLoaded: false,
      calibrationTypeChanged: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
  }

  /**
   * Load the current city data if the city exists (i.e. if editing).
   */
  async componentDidMount() {
    Modal.setAppElement("body");
    const { cityId } = this.props;
    if (cityId) {
      await axios
        .get(`${API_ENDPOINT}/cities/${cityId}/`)
        .then(res => {
          const cityData = res.data;
          this.setState({ isLoaded: true, cityData });
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
    const { cityData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(cityData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      isEdited: newEdited,
      cityData: newData,
      dataValidation: newDataValidation

    });
  }

  render() {
    const { onClose, onSaveData, cityId, onDelete, nextStep } = this.props;
    const { cityData, dataValidation, isEdited, isLoaded, error } = this.state;
    const { name, calibrationType, originLat, originLng, mapFile, mapAPIKey } = cityData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    return (
      <Form>
        <Form.Input
          label="Name *"
          content="name"
          value={name}
          error={
            isEdited.name && !dataValidation.name
              ? "Invalid City Name. Maximum Length: 200 Characters."
              : false
          }
          onChange={(e, data) => this.handleDataChange(e, data)}
          data-testid="CityNameInput"
        />

          <Form.Group>
            <Form.Radio
              label="GIS Calibration"
              checked={calibrationType === "geo"}
              onChange={e =>

                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "geo"
                })
              }
            />
            <Form.Radio
              label="Cartesian Calibration"
              checked={calibrationType  === "cartesian"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "cartesian"
                })
              }
            />
            <Form.Radio
              label="Floor Plan Calibration"
              checked={calibrationType  === "floorplan"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "floorplan"
                })
              }
            />
            <Form.Radio
              label="MultiCamera Tracking Calibration"
              checked={calibrationType  === "mtmc"}
              onChange={e =>
                this.handleDataChange(e, {
                  content: "calibrationType",
                  value: "mtmc"
                })
              }
            />

          </Form.Group>

        {/* <Form.Input
          label="Map File"
          content="mapFile"
          value={mapFile}
          error={
            isEdited.mapFile && !dataValidation.mapFile
              ? "Invalid Map File"
              : false
          }
          onChange={(e, data) => this.handleDataChange(e, data)}
        />
        <Form.Input
          label="Google API Key"
          content="mapAPIKey"
          value={mapAPIKey}
          error={
            isEdited.mapAPIKey && !dataValidation.mapAPIKey
              ? "Invalid API Key"
              : false
          }
          onChange={(e, data) => this.handleDataChange(e, data)}
        /> */}
        {/* <Form.Group widths="equal">
          <Form.Input
            label="City Origin Latitude *"
            content="originLat"
            value={originLat}
            error={
              isEdited.originLat && !dataValidation.originLat
                ? "Invalid Latitude"
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="CityLatInput"
          />
          <Form.Input
            label="City Origin Longitude  *"
            content="originLng"
            value={originLng}
            error={
              isEdited.originLng && !dataValidation.originLng
                ? "Invalid Longitude"
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="CityLngInput"
          />
        </Form.Group> */}
        <Form.Group>
          <Form.Button
            color="green"
            onClick={() => {
              onSaveData(cityData, dataValidation);
            }}
            data-testid="CityFormApply"
          >
            Apply
          </Form.Button>
          <Form.Button
            color="blue"
            onClick={() => nextStep()}
            data-testid="CityFormClose"
          >
            Next
          </Form.Button>
        </Form.Group>
        {cityId && (
          <div id="delete-project" style={{ padding: "2em 0" }}>
            <hr />
            <Header>DELETE CITY</Header>
            <p>
              The button bellow will delete city and all data within the city
              (intersections and sensors).
            </p>
            <DeleteButton
              onConfirmDelete={() => onDelete()}
              label={"DELETE CITY"}
              data-testid="CityFormDelete"
            />
          </div>
        )}
      </Form>
    );
  }
}

export default withAlert()(CityDataForm);

CityDataForm.propTypes = {
  /** ID of the city if form being used to edit existing city */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of clicking to save the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of deleting the city if editing existing city */
  onDelete: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired,
  /** Boolean to set if default form dropdown value are used*/
  useDefaults: PropTypes.bool,
  /** Handle the action of moving to next step in form */
  nextStep: PropTypes.func.isRequired,
};
