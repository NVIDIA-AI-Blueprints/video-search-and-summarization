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
import { Link } from "react-router-dom";
import { Grid, Button, Loader } from "semantic-ui-react";
import NewSensorModal from "../modals/NewSensorModal";
import EditSensorModal from "../modals/EditSensorModal";
import NewCorridorModal from "../modals/NewCorridorModal";
import EditCorridorModal from "../modals/EditCorridorModal";
import SensorsGrid from "./SensorsGrid";
import UploadImageModal from "../modals/UploadImageModal";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Grid of corridors. Each grid entery has the option to view the corridor (
 * shows sensors and cooridor geometry on google map), edit the corridor data,
 * or add a new sensor to the corridor. Each entry also lists all sensors
 * that belong to that corridor via the SensorsGrid.
 */
export default class CorridorsGrid extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      corridors: [],
      editCorridorModal: false,
      newCorridorModal: false,
      newSensorModal: false,
      editSensorModal: false,
      editingCorridor: null,
      editingSensor: null,
      uploadModalShow: false,
      calibrationType:"",
    };

    this.toggleNewCorridorModal = this.toggleNewCorridorModal.bind(this);
    this.toggleEditCorridorModal = this.toggleEditCorridorModal.bind(this);
    this.loadProjectCorridors = this.loadProjectCorridors.bind(this);
    this.toggleNewSensorModal = this.toggleNewSensorModal.bind(this);
    this.toggleEditSensorModal = this.toggleEditSensorModal.bind(this);
    this.toggleUploadShow = this.toggleUploadShow.bind(this);
  }

  /**
   * Run loadProjectCorridors function on component mount.
   */
  componentDidMount() {
    this.loadProjectCorridors();
  }

  /**
   * Load all corridors that belong to a specific project.
   */
  async loadProjectCorridors() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const cleanData = res.data
        const corridors = JSON.parse(DOMPurify.sanitize(JSON.stringify(cleanData.corridor_set)));
        const calibrationType = DOMPurify.sanitize(cleanData.calibrationType)
        this.setState({
          isLoaded: true,
          corridors,
          calibrationType: calibrationType
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
   * Toggle if the new corridor modal is showing or not.
   */
  toggleNewCorridorModal() {
    const newCorridorModal = !this.state.newCorridorModal;
    this.setState({ newCorridorModal });
  }

  /**
   * Toggle if the edit corridor modal is showing or not.
   * @param {number} corridorId ID of corridor being edited
   */
  toggleEditCorridorModal(corridorId) {
    this.setState({ editingCorridor: corridorId });
    const editCorridorModal = !this.state.editCorridorModal;
    this.setState({ editCorridorModal });
  }

  /**
   * Toggle if the new sensor modal is showing or not.
   * @param {number} corridorId ID of corridor to add the new sensor to
   */
  toggleNewSensorModal(corridorId) {
    this.setState({ editingCorridor: corridorId });
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
    const { title, projectId,  projectName } = this.props;

    const {
      corridors,
      error,
      isLoaded,
      newCorridorModal,
      editCorridorModal,
      newSensorModal,
      editSensorModal,
      editingCorridor,
      editingSensor,
      uploadModalShow,
      calibrationType,
      // projectId
    } = this.state;

    if (error) {
      return <div data-testid="corridorsError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="corridorsLoader" active inline="centered" />;
    }

    return (
      <div data-testid="corridorsGrid">
        <h1 style={{ display: "inline" }}>{title}</h1>

        <Button
          color="green"
          className="ui right floated button"
          size="large"
          onClick={this.toggleNewCorridorModal}
        >
          NEW CORRIDOR
        </Button>
        {corridors?.map((corridor, key) => {
          return (
            <Grid celled key={key} style={{ wordWrap: "break-word" }}>
              <Grid.Row color="grey">
                <Grid.Column verticalAlign="middle" width={5}>
                  <h3>{corridor.name}</h3>
                </Grid.Column>
                <Grid.Column
                  textAlign="center"
                  verticalAlign="middle"
                  width={3}
                >
                  <Link to={`/corridor/${projectId}/${corridor.id}`}>
                    <Button color="green">View Corridor</Button>
                  </Link>
                </Grid.Column>
                <Grid.Column
                  verticalAlign="middle"
                  textAlign="center"
                  width={4}
                >
                  <Button
                    color="green"
                    onClick={() => this.toggleEditCorridorModal(corridor.id)}
                  >
                    Edit Corridor
                  </Button>
                </Grid.Column>
                <Grid.Column textAlign="center" width={4}>
                  <Button
                    color="green"
                    onClick={() => this.toggleNewSensorModal(corridor.id)}
                  >
                    New Sensor
                  </Button>
                </Grid.Column>
              </Grid.Row>
              {/* <SensorsGrid
                sensors={corridor.sensor_set}
                toggleEditSensor={this.toggleEditSensorModal}
                toggleUploadShow={this.toggleUploadShow}
                calibrationType={calibrationType}
              /> */}
            </Grid>
          );
        })}

        <UploadImageModal
          modalShow={uploadModalShow}
          sensorId={editingSensor}
          reloadSensors={this.loadProjectCorridors}
          onClose={this.toggleUploadShow}
        />
        <NewCorridorModal
          modalShow={newCorridorModal}
          projectId={projectId}
          projectName={projectName}
          reloadCorridors={this.loadProjectCorridors}
          onClose={this.toggleNewCorridorModal}
        />
        <EditCorridorModal
          modalShow={editCorridorModal}
          corridorId={editingCorridor}
          reloadCorridors={this.loadProjectCorridors}
          onClose={this.toggleEditCorridorModal}
        />
        <NewSensorModal
          modalShow={newSensorModal}
          projectId={projectId}
          projectName={projectName}
          corridorId={editingCorridor}
          reloadSensors={this.loadProjectCorridors}
          onClose={this.toggleNewSensorModal}
        />
        <EditSensorModal
          modalShow={editSensorModal}
          sensorId={editingSensor}
          projectId={projectId}
          reloadSensors={this.loadProjectCorridors}
          onClose={this.toggleEditSensorModal}
        />
      </div>
    );
  }
}

CorridorsGrid.propTypes = {
  /** ID of the project that the corridors are a part of */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** Name of the project that the corridors are a part of */
  projectName: PropTypes.string,
  /** Title to use above the corridors grid */
  title: PropTypes.string.isRequired
};
