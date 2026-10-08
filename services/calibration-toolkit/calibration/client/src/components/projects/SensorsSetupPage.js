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
import { Loader, Button, Grid } from "semantic-ui-react";

import axios from "axios";
import { withAlert } from "react-alert";
import update from "immutability-helper";

import EditSensorModal from "../modals/EditSensorModal";
import NewSensorModal from "../modals/NewSensorModal";
import UploadImageModal from "../modals/UploadImageModal";
import UploadFloorPlanImageModal from "../modals/UploadFloorPlanImageModal";
import SensorsGrid from "./SensorsGrid";
import { floorPlanRoiId } from "../common/utils";



import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";


/**
 * Page to display the sensor grid with all sensors in the city as entries in
 * the grid.
 */
class SensorsSetupPage extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      sensors: [],
      sensorOnly : false,
      calibrationType: "",
      modalShow: false,
      editingSensor: null,
      newSensorModal: false,
      uploadModalShow: false,
      uploadFloorPlanModalShow: false,
      floorPlan: ""
    };

    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.handleDeleteSensor = this.handleDeleteSensor.bind(this);
    this.reloadSensors = this.reloadSensors.bind(this);
    this.toggleUploadShow = this.toggleUploadShow.bind(this);
    this.toggleUploadFloorPlanShow = this.toggleUploadFloorPlanShow.bind(this)
  }

  /**
   * Run the reloadSensors function on component mount.
   */
  componentDidMount() {
    this.reloadSensors();

  }

  /**
   * Get all sensors that belong to the specific city, based on city ID.
   */
  async reloadSensors() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const sensors = res.data.sensor_set;
        console.log("MTMC SPS1", sensors)
        const {sensorOnly, calibrationType, floorPlanImageUrl} = res.data
        this.setState({
          isLoaded: true,
          sensors,
          sensorOnly : sensorOnly,
          calibrationType: calibrationType,
          floorPlan: getMediaUrl(floorPlanImageUrl)

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
   * Handle the action of deleting the sensor. Removes the sensor from the
   * backend based on its ID, and removes it from the state based on its index
   * in the sensor array used for plotting the grid.
   * @param {number} sensorId Represents the ID of the sensor to delete.
   * @param {number} index Represents where the sensor is in the sensors array
   * to remove the sensor from the state.
   */
  async handleDeleteSensor(sensorId, index) {
    await axios
      .delete(`${API_ENDPOINT}/sensors/${sensorId}/`)
      .then(res => {
        const { sensors } = this.state;
        const newSensors = update(sensors, { $splice: [[index, 1]] });
        this.setState({ sensors: newSensors });
        this.props.alert.show(`Sensor ${sensorId} deleted.`);
      })
      .catch(error => console.error(error));
  }

  /**
   * Toggle if the edit sensor modal is shown or not.
   * @param {number} sensorId ID of the sensor that is being edited in the modal
   */
  toggleModalShow(sensorId) {
    this.setState({ editingSensor: sensorId });
    const modalShow = !this.state.modalShow;
    this.setState({ modalShow });
  }

  /**
   * Toggle if the new sensor modal is shown or not.
   * @param {null} sensorId Pass the null value to show that new sensor is being
   * added (i.e. not editing a sensor)
   */
  toggleUploadShow(sensorId) {
    this.setState({ editingSensor: sensorId });
    const uploadModalShow = !this.state.uploadModalShow;
    this.setState({ uploadModalShow });
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

  render() {
    console.log("am i")
    const { title } = this.props;
    const {  projectId } = this.props;
    const {
      modalShow,
      newSensorModal,
      sensors,
      sensorOnly,
      calibrationType,
      error,
      isLoaded,
      editingSensor,
      uploadModalShow,
      uploadFloorPlanModalShow,
      floorPlan
    } = this.state;
    console.log("cp12", calibrationType, projectId)
    if (error) {
      return <div>Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader active inline="centered" />;
    }


    return (
      <div className="project-images">
        <div>
          <h1 style={{ display: "inline" }}>{title}</h1>

          <Button
            color="green"
            className="ui right floated button"
            size="large"
            onClick={() =>
              this.setState({ newSensorModal: !this.state.newSensorModal })
            }
          >
            NEW SENSOR
          </Button>
        </div>
        <Grid celled style={{ wordWrap: "break-word" }}>
          <Grid.Row color="grey" key={"header"}>
            <Grid.Column verticalAlign="middle" width={3}>
              <h3>Sensor Name</h3>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={5}>
              <h3>Image File</h3>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={2}>
              <h3>Status</h3>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={6}>
              <h3>Actions</h3>
            </Grid.Column>
          </Grid.Row>
          <SensorsGrid
            // sensorOnly={sensorOnly}
            calibrationType={calibrationType}
            sensors={sensors}
            toggleEditSensor={this.toggleModalShow}
            toggleUploadShow={this.toggleUploadShow}
            toggleUploadFloorPlanShow={this.toggleUploadFloorPlanShow}
            floorPlan={floorPlan}
          />
        </Grid>

        <UploadImageModal
          modalShow={uploadModalShow}
          sensorId={editingSensor}
          reloadSensors={this.reloadSensors}
          onClose={this.toggleUploadShow}
        />
        <UploadFloorPlanImageModal
          modalShow={uploadFloorPlanModalShow}
          sensorId={editingSensor}
          reloadSensors={this.reloadSensors}
          onClose={this.toggleUploadFloorPlanShow}
        />

        <EditSensorModal
          modalShow={modalShow}
          projectId={projectId}
          sensorId={editingSensor}
          reloadSensors={this.reloadSensors}
          onClose={this.toggleModalShow}
        />
        <NewSensorModal
          modalShow={newSensorModal}
          projectId={projectId}
          reloadSensors={this.reloadSensors}
          onClose={() =>
            this.setState({ newSensorModal: !this.state.newSensorModal })
          }
        />
      </div>
    );
  }
}

export default withAlert()(SensorsSetupPage);

SensorsSetupPage.propTypes = {
  /** ID of project the sensors page is within */
  projectId: PropTypes.number,

  // projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Name of city */
  projectName: PropTypes.string,
  /** Title to be shown on sensors page */
  title: PropTypes.string.isRequired
};
