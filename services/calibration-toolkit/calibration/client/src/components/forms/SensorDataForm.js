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


import axios from "axios";
import PropTypes from "prop-types";
import React, { Component, Fragment } from "react";
import { withAlert } from "react-alert";
import { Form, Header, Loader } from "semantic-ui-react";

import DeleteButton from "../common/DeleteButton";
import {
  directionsOptions, setEdited, updateDataValidation, updateDataValue
} from "../common/utils";
import ImportJSONModal from "../modals/ImportJSONModal";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating sensor data.
 */
class SensorDataForm extends Component {
  _isMounted = false;
  constructor(props) {
    super(props);

    const { useDefaults, intersectionId, corridorId, placeId } = this.props;
    this.state = {
      sensorData: {
        sensorId: "",
        sensorName: "",
        majorRoad: "ROAD_1",
        minorRoad: "ROAD_2",
        originLat: "",
        originLng: "",
        mmsInfo_host: "",
        mmsInfo_protocol: "",
        mmsInfo_type: "",
        fps: "",
        deviceId : "",
        depth : "",
        fieldOfView : "",
        direction : "",
        cardinalDirection: "NW",
        videoURL: "",
        intersection_set: intersectionId,
        // intersection_set: intersectionId ? [intersectionId] : [],
        corridor_set: corridorId ? [corridorId] : [],
        place_set: placeId ? [placeId] : [],

        // place_set: placeId ? [placeId] : [],
        // place_set: placeId,


      },
      place_holder: {},
      dataValidation: {
        sensorId: false,
        majorRoad: true,
        minorRoad: true,
        originLat: false,
        originLng: false,
        cardinalDirection: useDefaults,
        intersection: useDefaults,
        corridor: useDefaults,
        place_set: useDefaults,
        fps: false,
        deviceId: false,
        depth: false,
        fieldOfView: false,
        direction: false,
        mmsInfo_host: false,
        mmsInfo_protocol: false,
        mmsInfo_type: false,
        videoURL: false
      },
      isEdited: {
        sensorId: false,
        majorRoad: false,
        minorRoad: false,
        originLat: false,
        originLng: false,
        cardinalDirection: false,
        intersection: false,
        corridor: false,
        place_set: false,
        fps: false,
        deviceId: false,
        depth: false,
        fieldOfView: false,
        direction: false,
        mmsInfo_host: false,
        mmsInfo_protocol: false,
        mmsInfo_type: false,
        videoURL: false
      },
      intersectionOptions: null,
      corridorOptions: null,
      placeTypes: null,
      jsonModalShow: false,
      isLoaded: false,
      error: false,
      calibrationType: "",
    };

    this.loadDropdownOptions = this.loadDropdownOptions.bind(this);
    this.handlePlaceDropdownChange = this.handlePlaceDropdownChange.bind(this);
    this.handleDataChange = this.handleDataChange.bind(this);
    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.handleJSONUpload = this.handleJSONUpload.bind(this);
    this.setAllEdited = this.setAllEdited.bind(this);
    this.renderPlacesDropdowns = this.renderPlacesDropdowns.bind(this);
  }

  /**
   * Load the current sensor data if the sensor exists (i.e. if editing).
   */
  async componentDidMount() {
    this._isMounted = true;
    const { sensorId } = this.props;
    if (sensorId) {
      await axios
        .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
        .then(res => {
          const sensorData = res.data;
          this._isMounted && this.setState({ isLoaded: true, sensorData });
        })
        .catch(error => {
          this._isMounted &&
            this.setState({
              isLoaded: true,
              error
            });
        });
    } else {
      this._isMounted && this.setState({ isLoaded: true });
    }
    this.loadDropdownOptions();

  }

  /**
   * Load the options for the dropdowns. Dropdowns are intersection, corridor,
   * and any other places that are defined.
   */
  async loadDropdownOptions() {
    const { projectId } = this.props;
    const place_set = this.state.sensorData.place_set;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        let intersectionOptions = [];
        let corridorOptions = [];
        let placeTypes = {};
        let place_holder = {}
        let calibrationType = res.data.calibrationType
        res.data.corridor_set.forEach(corridor => {
          corridorOptions.push({
            key: corridor.id,
            text: corridor.name,
            value: corridor.id
          });
        });
        res.data.intersection_set.forEach(intersection => {
          intersectionOptions.push({
            key: intersection.id,
            text: intersection.name,
            value: intersection.id
          });
        });
        // res.data.placeTypes_set.forEach(placeType => {
        //   place_holder[placeType.placeType] = ""
        //   placeTypes[placeType.placeType] = placeType.place_set.map(place => {
        //     if (place_set.includes(place.id)){
        //       place_holder[placeType.placeType] = place.id;
        //     }
        //     return { key: place.id, text: place.name, value: place.id };
        //   });
        // });
        this._isMounted &&
          this.setState({
            intersectionOptions,
            corridorOptions,
            placeTypes,
            place_holder,
            calibrationType
          });
      })
      .catch(error => {
        this._isMounted &&
          this.setState({
            error
          });
      });
  }

  componentWillUnmount() {
    this._isMounted = false;
  }

    /**
   * Handle data changed in any form input field. Sets that field as edited,
   * sets the value in the state based off of what was written in the field,
   * and runs data validation to see if the entered data is accurate.
   * @param {object} e Data changed event, unused
   * @param {object} data Information on the data in the form
   */
   handlePlaceDropdownChange(e, data, placeType) {
      const {place_holder} = this.state;
      const {value} = data;
      place_holder[placeType] =  value;
      this.setState({
        place_holder
      });

      const place_set = [];
      Object.keys(place_holder).forEach((place,val) => {
        if(place_holder[place] !== ""){
          place_set.push(place_holder[place])
        }
      });

      const consolidatedData = {
        content: "place_set",
        value: place_set
      }
      this.handleDataChange(e,consolidatedData)
    }


  /**
   * Handle data changed in any form input field. Sets that field as edited,
   * sets the value in the state based off of what was written in the field,
   * and runs data validation to see if the entered data is accurate.
   * @param {object} e Data changed event, unused
   * @param {object} data Information on the data in the form
   */
  handleDataChange(e, data) {
    // console.log("data", data)
    const { content, value } = data;
    // console.log("caksjdf", content)
    const { sensorData, dataValidation, isEdited } = this.state;
    // if calibrationType
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(sensorData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );
    this.setState({
      isEdited: newEdited,
      sensorData: newData,
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
    const { sensorData, dataValidation, isEdited } = this.state;
    let newData = sensorData;
    let newValidation = dataValidation;
    let newEdited = isEdited;
    Object.keys(jsonObject).forEach(key => {
      if (key in sensorData) {
        if (this.checkIfDropdown(key)) {
          if (key === "cardinalDirection") {
            jsonObject[key] = String(jsonObject[key]).toUpperCase();
          }
          if (!this.validateDropdowns(key, jsonObject[key])) {
            return;
          }
        }
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
      sensorData: newData,
      dataValidation: newValidation,
      isEdited: newEdited
    });
    this.toggleModalShow();
  }

  /**
   * Check the values provided to the dropdown are valid. Used when uploading
   * values via JSON, where values are not restricted by the dropdown options.
   * @param {string} dropdown Represents the data category being changed
   * @param {any} value Represents the value the data is being changed to
   */
  validateDropdowns(dropdown, value) {
    switch (dropdown) {
      case "intersection":
        for (let intersection of this.state.intersectionOptions) {
          if (value === intersection.value) {
            return true;
          }
        }
        break;
      case "corridor":
        for (let corridor of this.state.corridorOptions) {
          if (value === corridor.value) {
            return true;
          }
        }
        break;
      case "cardinalDirection":
        for (let cardinalDirection of directionsOptions) {
          if (value === cardinalDirection.value) {
            return true;
          }
        }
        break;
      default:
        break;
    }
    this.props.alert.error(`${value} is not a valid ${dropdown} value`);
    return false;
  }

  /**
   * Check if the data category in the JSON upload is a dropdown menu.
   * @param {string} category Data category of interest
   */
  checkIfDropdown(category) {
    if (
      category === "intersection" ||
      category === "corridor" ||
      category === "cardinalDirection"
    ) {
      return true;
    }
    return false;
  }

  /**
   * Set all fields as edited. Used to show which fields are empty when creating
   * a new sensor.
   */
  setAllEdited() {
    const { isEdited } = this.state;
    const { sensorId } = this.props;
    if (!sensorId) {
      let updatedEdited = {};
      Object.keys(isEdited).forEach(key => {
        updatedEdited[key] = true;
      });
      this.setState({ isEdited: updatedEdited });
    }

  }

  /**
   * Render the dropdown menus for different places that the sensor can belong
   * to.
   */
  renderPlacesDropdowns() {
    const { placeTypes, place_holder } = this.state;
    // const { place_set } = this.state.sensorData;

    if (!!placeTypes) {
      const places = Object.keys(placeTypes).map(placeType => {
        return (
          <Form.Dropdown
            id={`${placeType}`}
            key={`${placeType}`}
            label={`${placeType}`}
            value={place_holder[placeType]}
            content="place"
            search
            clearable
            selection
            onChange={(e,data) => this.handlePlaceDropdownChange(e,data, placeType)}
            options={placeTypes[placeType]}
          />

        );
      });
      return places;
    }
  }

  render() {
    const { onClose, onDelete, onSaveData } = this.props;
    const {
      error,
      isLoaded,
      dataValidation,
      isEdited,
      sensorData,
      jsonModalShow,
      intersectionOptions,
      corridorOptions,
      calibrationType
    } = this.state;
    const {
      sensorId,

      originLat,
      originLng,
      sensorName,
      cardinalDirection,
      intersection_set,
      corridor_set,
      fps,
      fieldOfView,
      direction,
      deviceId,
      depth,
      mmsInfo_host,
      mmsInfo_protocol,
      mmsInfo_type,
      videoURL,
      majorRoad,
      minorRoad,
    } = sensorData;



    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    return (
      <Fragment>
        <ImportJSONModal
          modalShow={jsonModalShow}
          onUpload={this.handleJSONUpload}
          onClose={this.toggleModalShow}
        />
        <Form>
          <Form.Group widths="equal">
            <Form.Input
              label="Sensor Id"
              content="sensorId"
              value={sensorId}
              error={
                isEdited.sensorId && !dataValidation.sensorId
                  ? "Invalid Sensor Id. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamSensorIdInput"
            />
            {/* <Form.Input
              label="Sensor FPS"
              content="fps"
              value={fps}
              error={
                isEdited.fps && !dataValidation.fps
                  ? "Invalid FPS"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamFPSInput"
            /> */}
            <Form.Input
              label="Device Id"
              content="deviceId"
              value={deviceId}
              error={
                isEdited.deviceId && !dataValidation.deviceId
                  ? "Invalid Device Id. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamDeviceIdInput"
            />
          </Form.Group>
          <Form.Input
            label="Sensor Name"
            content="sensorName"
            value={sensorName}
            error={
              isEdited.sensorName && !dataValidation.sensorName
                ? "Invalid sensor name. Maximum Length: 200 Characters."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="CamNameInput"
          />
          {calibrationType === "geo" ?(
          <Form.Group widths="equal">
            <Form.Input
              label="Major Road"
              content="majorRoad"
              value={majorRoad}
              error={
                isEdited.majorRoad && !dataValidation.majorRoad
                  ? "Invalid road name. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamMajRdInput"
            />
            <Form.Input
              label="Minor Road"
              content="minorRoad"
              value={minorRoad}
              error={
                isEdited.minorRoad && !dataValidation.minorRoad
                  ? "Invalid road name. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamMinRdInput"
            />
          </Form.Group>):(<div></div>)}
          <Form.Group widths="equal">
            <Form.Input
              label="Sensor Latitude"
              content="originLat"
              value={originLat}
              error={
                isEdited.originLat && !dataValidation.originLat
                  ? "Invalid Latitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamLatInput"
            />
            <Form.Input
              label="Sensor Longitude"
              content="originLng"
              value={originLng}
              error={
                isEdited.originLng && !dataValidation.originLng
                  ? "Invalid Longitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamLngInput"
            />
            <Form.Dropdown
              label="Cardinal Direction"
              content="cardinalDirection"
              selection
              search
              value={cardinalDirection}
              options={directionsOptions}
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamCardDirInput"
            />
          </Form.Group>
          <Form.Group widths="equal">
            <Form.Input
              label="Sensor FPS"
              content="fps"
              value={fps}
              error={
                isEdited.fps && !dataValidation.fps
                  ? "Invalid FPS"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamFPSInput"
            />
            <Form.Input
              label="Sensor Depth"
              content="depth"
              value={depth}
              error={
                isEdited.depth && !dataValidation.depth
                  ? "Invalid Depth"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamDepthInput"
            />
            <Form.Input
              label="Sensor FOV"
              content="fieldOfView"
              value={fieldOfView}
              error={
                isEdited.fieldOfView && !dataValidation.fieldOfView
                  ? "Invalid Field of View"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamFOVInput"
            />
            <Form.Input
              label="Sensor Direction"
              content="direction"
              value={direction}
              error={
                isEdited.direction && !dataValidation.direction
                  ? "Invalid direction"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamDirectionInput"
            />
          </Form.Group>
          <Form.Group widths="equal">
            <Form.Input
              label="Video Url"
              content="videoURL"
              value={videoURL}
              error={
                isEdited.videoURL && !dataValidation.videoURL
                  ? "Invalid videoURL"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamVideoURLInput"
            />
            <Form.Input
              label="MMS Protocol"
              content="mmsInfo_protocol"
              value={mmsInfo_protocol}
              error={
                isEdited.mmsInfo_protocol && !dataValidation.mmsInfo_protocol
                  ? "Invalid MMSInfo Protocol"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamMMSInfoProtocolInput"
            />
            <Form.Input
              label="MMS Type"
              content="mmsInfo_type"
              value={mmsInfo_type}
              error={
                isEdited.mmsInfo_type && !dataValidation.mmsInfo_type
                  ? "Invalid MMS Info Type"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamMMSInfoTypeInput"
            />
            <Form.Input
              label="MMS Host"
              content="mmsInfo_host"
              value={mmsInfo_host}
              error={
                isEdited.mmsInfo_host && !dataValidation.mmsInfo_host
                  ? "Invalid MMS Info Host"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="CamMMSInfoHostInput"
            />
          </Form.Group>
          <Form.Group widths="equal">
            <Form.Dropdown
              label="Corridor"
              value={corridor_set}
              content="corridor_set"
              search
              selection
              clearable
              multiple
              onChange={(e, data) => this.handleDataChange(e, data)}
              options={corridorOptions}
              data-testid="CamCorInput"
            />
            <Form.Dropdown
              label="Intersection"
              value={intersection_set}
              content="intersection_set"
              search
              selection
              clearable
              onChange={(e, data) => this.handleDataChange(e, data)}
              options={intersectionOptions}
              data-testid="CamIntersecInput"
            />
                      {this.renderPlacesDropdowns()}

          </Form.Group>
          <Form.Group>
            <Form.Button
              color="green"
              onClick={() => {
                this.setAllEdited();
                onSaveData(sensorData, dataValidation, calibrationType);
              }}
              type="button"
              data-testid="CamFormApply"
            >
              Apply
            </Form.Button>
            <Form.Button
              color="purple"
              onClick={() => onClose()}
              data-testid="CamFormClose"
              type="button"
            >
              Close
            </Form.Button>
            <Form.Button
              floated="right"
              type="button"
              color="green"
              onClick={this.toggleModalShow}
            >
              Upload from JSON
            </Form.Button>
          </Form.Group>

          {onDelete && (
            <div>
              <hr />
              <Header>DELETE CAMERA</Header>
              <p>The button bellow will delete the sensor and all its data.</p>
              <DeleteButton
                onConfirmDelete={() => onDelete()}
                label={"DELETE CAMERA"}
              />
            </div>
          )}
        </Form>
      </Fragment>
    );
  }
}

export default withAlert()(SensorDataForm);

SensorDataForm.propTypes = {
  /** ID of project that the sensor is being added to or edited in */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Ihe ID of the sensor being edited if editing existing sensor */
  sensorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired,
  /** Handle the action of deleting the sensor (if existing) */
  onDelete: PropTypes.func,
  /** Handle the action of creating a new sensor or patching an existing */
  onSaveData: PropTypes.func.isRequired,
  /** Intersection Id if sensor is to be created in a specific intersection */
  intersectionId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Corridor Id if sensor is to be created in a specific corridor */
  corridorId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Place Id if sensor is to be created in a specific corridor */
  placeId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Boolean to set if default form dropdown values are used*/
  useDefaults: PropTypes.bool
};
