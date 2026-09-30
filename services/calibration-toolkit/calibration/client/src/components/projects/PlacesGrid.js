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
import axios from "axios";
import { Grid, Button, Loader } from "semantic-ui-react";
import NewSensorModal from "../modals/NewSensorModal";
import EditSensorModal from "../modals/EditSensorModal";
import SensorsGrid from "./SensorsGrid";
import UploadImageModal from "../modals/UploadImageModal";
import NewPlaceModal from "../modals/NewPlaceModal";
import EditPlaceModal from "../modals/EditPlaceModal";
import EditPlaceTypeModal from "../modals/EditPlaceTypeModal";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Grid of places. Each grid entery has the option edit the place data,
 * or add a new sensor to the place. Each entry also lists all sensors
 * that belong to that place via the SensorsGrid.
 */
export default class PlacesGrid extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      place_set: [],
      newPlaceModal: false,
      editPlaceModal: false,
      editingPlace: null,
      editPlaceTypeModal: false,
      newSensorModal: false,
      editSensorModal: false,
      editingSensor: null,
      uploadModalShow: false,
      calibrationType: ""
    };

    this.toggleNewPlaceModal = this.toggleNewPlaceModal.bind(this);
    this.toggleEditPlaceModal = this.toggleEditPlaceModal.bind(this);
    this.toggleEditPlaceTypeModal = this.toggleEditPlaceTypeModal.bind(this);
    this.loadCityPlaces = this.loadCityPlaces.bind(this);
    this.toggleNewSensorModal = this.toggleNewSensorModal.bind(this);
    this.toggleEditSensorModal = this.toggleEditSensorModal.bind(this);
    this.toggleUploadShow = this.toggleUploadShow.bind(this);
  }

  /**
   * Run loadCityPlaces function on component mount.
   */
  componentDidMount() {
    this.loadCityPlaces();
  }

  /**
   * Load all places that belong to a specific place type.
   */
  async loadCityPlaces() {
    const { placeTypeId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/placeTypes/${placeTypeId}/`)
      .then(res => {
        const { place_set } = res.data;
        console.log(place_set)
        this.setState({
          isLoaded: true,
          place_set
        });
      })
      .catch(error => {
        this.setState({
          isLoaded: true,
          error
        });
      });
  }

  /**
   * Toggle if the new place modal is showing or not.
   */
  toggleNewPlaceModal() {
    const newPlaceModal = !this.state.newPlaceModal;
    this.setState({ newPlaceModal });
  }

  /**
   * Toggle if the edit place modal is showing or not.
   * @param {number} placeId ID of place being edited
   */
  toggleEditPlaceModal(placeId) {
    this.setState({ editingPlace: placeId });
    const editPlaceModal = !this.state.editPlaceModal;
    this.setState({ editPlaceModal });
  }

  /**
   * Toggle if the edit place modal is showing or not.
   */
  toggleEditPlaceTypeModal() {
    const editPlaceTypeModal = !this.state.editPlaceTypeModal;
    this.setState({ editPlaceTypeModal });
  }

  /**
   * Toggle if the new sensor modal is showing or not.
   * @param {number} placeId ID of place to add the new sensor to
   */
  toggleNewSensorModal(placeId) {
    this.setState({ editingPlace: placeId });
    const newSensorModal = !this.state.newSensorModal;
    this.setState({ newSensorModal });
  }

  /**
   * Toggle if the edit sensor modal is showing or not.
   * @param {number} sensorId ID of the sensor being edited
   * @todo Duplicate code. This should be refactored to sensorsGrid
   */
  toggleEditSensorModal(sensorId) {
    this.setState({ editingSensor: sensorId });
    const editSensorModal = !this.state.editSensorModal;
    this.setState({ editSensorModal });
  }

  /**
   * Toggle if the upload image modal is showing or not.
   * @param {number} sensorId ID of sensor to add image to.
   * @todo Duplicate code. This should be refactored to sensorsGrid
   */
  toggleUploadShow(sensorId) {
    this.setState({ editingSensor: sensorId });
    const uploadModalShow = !this.state.uploadModalShow;
    this.setState({ uploadModalShow });
  }

  render() {
    const { title, projectId, placeTypeId, projectName, calibrationType } = this.props;
    console.log("place grid", this.props.calibrationType)
    const {
      place_set,
      error,
      isLoaded,
      newPlaceModal,
      editPlaceModal,
      editPlaceTypeModal,
      newSensorModal,
      editSensorModal,
      editingPlace,
      editingSensor,
      uploadModalShow,
      
    } = this.state;

    const placeType = this.props.placeType.toUpperCase();

    if (error) {
      return <div data-testid="placesError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="placesLoader" active inline="centered" />;
    }

    return (
      <div>
        <h1 style={{ display: "inline" }}>{title}</h1>

        <Button
          color="green"
          className="ui right floated button"
          size="large"
          onClick={() => this.toggleNewPlaceModal()}
        >
          {`NEW ${placeType}`}
        </Button>
        <Button
          color="green"
          className="ui right floated button"
          size="large"
          onClick={() => this.toggleEditPlaceTypeModal(placeTypeId)}
        >
          {`EDIT PLACE TYPE`}
        </Button>
        {place_set.map((place, key) => {
          return (
            <Grid celled key={key} style={{ wordWrap: "break-word" }}>
              <Grid.Row color="grey">
                <Grid.Column verticalAlign="middle" width={5}>
                  <h3>{place.name}</h3>
                </Grid.Column>
                <Grid.Column
                  textAlign="center"
                  verticalAlign="middle"
                  width={3}
                ></Grid.Column>
                <Grid.Column
                  verticalAlign="middle"
                  textAlign="center"
                  width={4}
                >
                  <Button
                    color="green"
                    onClick={() => this.toggleEditPlaceModal(place.id)}
                  >
                    {`Edit ${placeType}`}
                  </Button>
                </Grid.Column>
                <Grid.Column textAlign="center" width={4}>
                  <Button
                    color="green"
                    onClick={() => this.toggleNewSensorModal(place.id)}
                  >
                    New Sensor
                  </Button>
                </Grid.Column>
              </Grid.Row>
              <SensorsGrid
                calibrationType={this.props.calibrationType}
                sensors={place.sensor_set}
                toggleEditSensor={this.toggleEditSensorModal}
                toggleUploadShow={this.toggleUploadShow}
              />
            </Grid>
          );
        })}

        <UploadImageModal
          modalShow={uploadModalShow}
          sensorId={editingSensor}
          reloadSensors={this.loadCityPlaces}
          onClose={this.toggleUploadShow}
        />
        <EditPlaceTypeModal
          modalShow={editPlaceTypeModal}
          placeTypeId={placeTypeId}
          cityId={projectId}
          reloadPlaceTypes={this.props.reloadCity}
          onClose={this.toggleEditPlaceTypeModal}
        />
        <NewPlaceModal
          modalShow={newPlaceModal}
          projectId={projectId}
          cityId={projectId}
          placeTypeId={placeTypeId}
          cityName={projectName}
          reloadPlaces={this.loadCityPlaces}
          onClose={this.toggleNewPlaceModal}
          placeType={placeType}
        />
        <EditPlaceModal
          modalShow={editPlaceModal}
          placeId={editingPlace}
          reloadPlaces={this.loadCityPlaces}
          onClose={this.toggleEditPlaceModal}
          placeType={placeType}
        />
        <NewSensorModal
          modalShow={newSensorModal}
          projectId={projectId}
          projectName={projectName}
          placeId={editingPlace}
          reloadSensors={this.loadCityPlaces}
          onClose={this.toggleNewSensorModal}
        />
        <EditSensorModal
          modalShow={editSensorModal}
          sensorId={editingSensor}
          projectId={projectId}
          reloadSensors={this.loadCityPlaces}
          onClose={this.toggleEditSensorModal}
        />
      </div>
    );
  }
}

PlacesGrid.propTypes = {
  /** ID of the project that the places are a part of */
  placeTypeId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** ID of the city that the places are a part of */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Name of the city that the places are a part of */
  projectName: PropTypes.string,
  /** Title to use above the places grid */
  title: PropTypes.string.isRequired,
  /** ID of the project that the places are a part of */
  calibrationType: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
};
