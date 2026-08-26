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
import { Checkbox, Grid, Button, Loader } from "semantic-ui-react";
import NewIntersectionModal from "../modals/NewIntersectionModal";
import EditIntersectionModal from "../modals/EditIntersectionModal";
import NewSensorModal from "../modals/NewSensorModal";
import EditSensorModal from "../modals/EditSensorModal";
import SensorsGrid from "./SensorsGrid";
import UploadImageModal from "../modals/UploadImageModal";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Grid of corridors. Each grid entery shows the intersection name, if the road
 * network for the intersection has been generated/validated, a button to edit
 * the intersection, and a button to add a sensor to the intersection.
 */
export default class IntersectionsGrid extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      intersections: [],
      editIntersectionModal: false,
      newIntersectionModal: false,
      newSensorModal: false,
      editSensorModal: false,
      editingIntersection: null,
      editingSensor: null,
      uploadModalShow: false,
      calibrationType: "",
    };

    this.toggleNewIntersectionModal = this.toggleNewIntersectionModal.bind(
      this
    );
    this.toggleEditIntersectionModal = this.toggleEditIntersectionModal.bind(
      this
    );
    this.loadProjectIntersection = this.loadProjectIntersection.bind(this);
    this.toggleNewSensorModal = this.toggleNewSensorModal.bind(this);
    this.toggleEditSensorModal = this.toggleEditSensorModal.bind(this);
    this.toggleUploadShow = this.toggleUploadShow.bind(this);
  }

  /**
   * Run the loadProjectIntersection function on component mount.
   */
  componentDidMount() {
    this.loadProjectIntersection();
  }

  /**
   * All all intersections belonging to the given city.
   */
  async loadProjectIntersection() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        //const cleanData = DOMPurify.sanitize(res.data)
        const intersections = JSON.parse(DOMPurify.sanitize(JSON.stringify(res.data.intersection_set)));
        const calibrationType = DOMPurify.sanitize(res.data.calibrationType);
        console.log("ig", intersections)
        this.setState({
          isLoaded: true,
          intersections,
          calibrationType:calibrationType

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
   * Toggle whether or not the new intersection modal is being shown.
   */
  toggleNewIntersectionModal() {
    const newIntersectionModal = !this.state.newIntersectionModal;
    this.setState({ newIntersectionModal });
  }

  /**
   * Toggle wether or not the edit intersection modal is being shown.
   * @param {number} intersectionId ID of intersection being edited.
   */
  toggleEditIntersectionModal(intersectionId) {
    this.setState({ editingIntersection: intersectionId });
    const editIntersectionModal = !this.state.editIntersectionModal;
    this.setState({ editIntersectionModal });
  }

  /**
   * Toggle wether or not the new sensor modal is being shown.
   * @param {number} intersectionId ID of intersection to add the sensor to.
   */
  toggleNewSensorModal(intersectionId) {
    this.setState({ editingIntersection: intersectionId });
    const newSensorModal = !this.state.newSensorModal;
    this.setState({ newSensorModal });
  }

  /**
   * Toggle wther or not the edit sensor modal is being shown.
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
    const { title, projectId, projectName } = this.props;

    const {
      intersections,
      error,
      isLoaded,
      newIntersectionModal,
      editIntersectionModal,
      newSensorModal,
      editSensorModal,
      editingIntersection,
      editingSensor,
      uploadModalShow,
      calibrationType
    } = this.state;

    if (error) {
      return <div data-testid="IntersectionsError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="IntersectionsLoader" active inline="centered" />
      );
    }

    return (
      <div data-testid="IntersectionsGrid">
        <h1 style={{ display: "inline" }}>{title}</h1>
        <Button
          color="green"
          className="ui right floated button"
          size="large"
          onClick={this.toggleNewIntersectionModal}
        >
          NEW INTERSECTION
        </Button>
        <Link to={`/links/projects/validation/${projectId}`}>
          <Button
            color="green"
            className="ui right floated button"
            size="large"
          >
            VIEW ALL ROAD LINKS
          </Button>
        </Link>
        {intersections?.map((intersection, key) => {
          return (
            <Grid celled key={key} style={{ wordWrap: "break-word" }}>
              <Grid.Row color="grey">
                <Grid.Column verticalAlign="middle" width={6}>
                  <h3>{intersection.name}</h3>
                </Grid.Column>
                <Grid.Column verticalAlign="middle" width={2}>
                  <Checkbox
                    checked={intersection.linksAreDrawn}
                    label="Network"
                  />
                  <Checkbox
                    checked={intersection.linksAreValid}
                    label="Validated"
                  />
                </Grid.Column>
                <Grid.Column
                  verticalAlign="middle"
                  textAlign="center"
                  width={4}
                >
                  <Button
                    color="green"
                    onClick={() =>
                      this.toggleEditIntersectionModal(intersection.id)
                    }
                  >
                    Edit Intersection
                  </Button>
                </Grid.Column>
                <Grid.Column textAlign="center" width={4}>
                  <Button
                    color="green"
                    onClick={() => this.toggleNewSensorModal(intersection.id)}
                  >
                    New Sensor
                  </Button>
                </Grid.Column>
              </Grid.Row>
              {/* <SensorsGrid
                sensors={intersection.sensor_set}
                toggleEditSensor={this.toggleEditSensorModal}
                toggleUploadShow={this.toggleUploadShow}
                placeId={key}
                calibrationType={calibrationType}
              /> */}
            </Grid>
          );
        })}

        <UploadImageModal
          modalShow={uploadModalShow}
          sensorId={editingSensor}
          reloadSensors={this.loadProjectIntersection}
          onClose={this.toggleUploadShow}
        />

        <NewIntersectionModal
          modalShow={newIntersectionModal}
          projectId={projectId}
          // cityId={cityId}
          projectName={projectName}
          reloadIntersections={this.loadProjectIntersection}
          onClose={this.toggleNewIntersectionModal}
        />
        <EditIntersectionModal
          modalShow={editIntersectionModal}
          intersectionId={editingIntersection}
          reloadIntersections={this.loadProjectIntersection}
          onClose={this.toggleEditIntersectionModal}
        />
        <NewSensorModal
          modalShow={newSensorModal}
          projectId={projectId}
          intersectionId={editingIntersection}
          reloadSensors={this.loadProjectIntersection}
          onClose={this.toggleNewSensorModal}
        />
        <EditSensorModal
          modalShow={editSensorModal}
          sensorId={editingSensor}
          projectId={projectId}
          reloadSensors={this.loadProjectIntersection}
          onClose={this.toggleEditSensorModal}
        />
      </div>
    );
  }
}

IntersectionsGrid.propTypes = {
  /** ID of project the intersections belong to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** ID of city to load the interesections */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Name of the city the intersections belong to */
  cityName: PropTypes.string,
  /** Title to use for intersections grid */
  title: PropTypes.string
};
