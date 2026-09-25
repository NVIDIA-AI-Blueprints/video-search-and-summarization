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
import React, { Component } from "react";
import { withAlert } from "react-alert";
import { Button, Form, Header, Loader } from "semantic-ui-react";
// import { Link } from 'react-router-dom';

import {
  setEdited,
  updateDataValidation,
  updateDataValue
} from "../common/utils";

import { API_ENDPOINT } from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";
/**
 * Form for uploading a calibration image from either a RTSP stream or a jpg/png
 * screenshot.
 */
class UploadFloorPlanProjectImageForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      projectData: {
        floorPlanImageUrl: "",
        sensorsList:[]
      },
      dataValidation: {
        floorPlanImageUrl: false
      },
      isEdited: {
        floorPlanImageUrl: false
      },
      isLoadingRTSP: false,
      isLoaded: false,
      error: false,
      sensorsList:[]
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.setImageResolution = this.setImageResolution.bind(this);
    this.loadImageResolution = this.loadImageResolution.bind(this);
    this.updateSensorsData = this.updateSensorsData.bind(this)
    this.getSensors = this.getSensors.bind(this)

  }

  /**
   * Get rtsp url oon component mount
   */
  async componentDidMount() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const { projectData, dataValidation } = this.state;
        const { rtspURL, sensor_set } = res.data;
        //TODO do i need this
        if (rtspURL) {
          let newData = updateDataValue(projectData, "rtspURL", rtspURL);
          const newDataValidation = updateDataValidation(
            dataValidation,
            "rtspURL",
            rtspURL
          );
          newData["sensorsList"] = sensor_set
          console.log("ufcim", newData)
          this.setState({
            isLoaded: true,
            projectData: newData,
            dataValidation: newDataValidation
          });
        } else {
          this.setState({ isLoaded: true });
        }
      });
  }

      /**
   * Backup the previous map file before downloading a new map file in case the
   * new map file errors while downloading the map.
   */
      async getSensors(data) {
        const { projectId } = this.props;
        let sensors = []
        await axios
          .get(`${API_ENDPOINT}/projects/${projectId}/`)
          .then(res => {
            console.log("ufcim g", data)
            // this.updateSensorsData(res.data.sensor_set, data )
            this.setState({ sensorsList: res.data.sensor_set });
            console.log("ufcim", this.state)
            // sensors= res.data.sensor_set
          })
          .catch(error => {});
        // return sensors
      }


      async pushSensorData(id,data){
        await axios
        .patch(`${API_ENDPOINT}/sensors/${id}/`, {
          data
        })
        .then( res => {
            console.log ("ufcim s", id, data)
            this.props.alert.success(`Uploaded floorplan image for ${id}`);
          }
        )
        .catch(error => console.error("err", error));
      }

      updateSensorsData(sensorsList, data){
        console.log("ufcim usd", data, sensorsList)
        if (sensorsList.length >0){
          sensorsList.forEach( sensor => {
            const sensorId = sensor.id
            this.pushSensorData(sensorId,data)
            // const sensor = sensors.filter(({sensorId})=> sensorName.includes(sensorId))
            // const sensor = sensors.filter(item => item.sensorId === sensorName)

            // console.log("output pu", sensor[0])
            // const {id, sensorId}  = sensor[0]

            // console.log("output pus", sensor, coordinatessensorId)
            // const coordinates = JSON.stringify((labelData.mapLabelData[sensorId]));
            // console.log("output puc", id, coordinates)
            // this.pushSensorData({id,})
            })
        }

      }

  /**
 * Request the calculation of the homography to be done in the backend.
 */
    async syncFPImages(projectId) {
    //update
    await axios
      .get(`${API_ENDPOINT}/syncFloorPlan/${projectId}/`)
      .then()
      .catch(error => console.error(error));
  }

  updateProject(projectId,data){
    console.log("ufcim uc", data)
    axios.patch(`${API_ENDPOINT}/projects/${projectId}/`, data).then( res => {
      const name = res.data.name
      console.log("ufcim uca", res)
      this.props.alert.success(`Uploaded floorplan image for ${name}`)
    })
      .catch(error => console.error(error))
  }

  /**
   * Upload and save an image to the backend from a file obtained using the file
   * explorer interface.
   * @param {number} projectId Public key representing the project ID
   * @param {object} filename Information about the selected image to use for
   * calibration
   */
  async handleUploadImage(projectId, filename) {
    let form_data = new FormData();
    form_data.append("floorPlanImageUrl", filename, filename.name);
    // const {sensorsList} = this.state

    console.log("ufcim c",  filename, filename.name, form_data, this.state)
    this.getSensors(form_data)

    try {
          // form_data.append("projectPolygon", "[]");

        const response1 = await this.updateProject(projectId,form_data)
        console.log("Updated Project", response1)
        const response2 = await this.syncFPImages(projectId)
        console.log("Sync Floorplans", response2)
        const response3 = await this.props.reloadProject();
        console.log("Reload Projects", response3)
        // await axios.all([, this.syncFPImages(projectId)])
        // .then(axios.spread(( data) => {
        //   this.props.reloadProject();
        // }))
        // .catch(error => console.error("ufcim err", error));
    }catch{

    }


  }

  /**
   * Save the resolution of the image (height and width) to the backend of the
   * project.
   */
  async setImageResolution() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const floorPlanImageUrl = getMediaUrl(res.data.floorPlanImageUrl);
        if (floorPlanImageUrl) {
          this.loadImageResolution(floorPlanImageUrl);
        }
      })
      .catch(error => console.error(error));
  }

  /**
   * Load the resolution of the image once it has been uploaded to the backend.
   * @param {string} floorPlanImageUrl Filepath to image uploaded and saved in backend
   */
  async loadImageResolution(floorPlanImageUrl) {
    const { projectId } = this.props;
    let img = new Image();
    img.onload = async function() {
      const { height, width } = this;

      const floorPlanImHeight = height
      const floorPlanImWidth = width
      // const { invertImHeight, invertImWidth} = this;
      await axios
        .patch(`${API_ENDPOINT}/projects/${projectId}/`, { floorPlanImWidth, floorPlanImHeight })
        .then()
        .catch();
    };
    img.src = floorPlanImageUrl;
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
    const { projectData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(projectData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    this.setState({
      isEdited: newEdited,
      projectData: newData,
      dataValidation: newDataValidation
    });
  }

  render() {
    const {
      projectId,
      onClose,
      onDelete,
      onSaveData,
      reloadProject
    } = this.props;
    const {
      dataValidation,
      projectData,
      isEdited,
      isLoadingRTSP,
      isLoaded,
      error
    } = this.state;
    const { rtspURL } = projectData;
    console.log("UPFM", projectId, this.props)
    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }


    return (
      <Form>
        <h1>Upload Via Screenshot File:</h1>
        <label>
          <input
            onChange={async e => {
              await this.handleUploadImage(projectId, e.target.files[0]);
              this.setImageResolution();
              reloadProject();
            }}
            type="file"
            accept="image/png, image/jpeg"
          />
        </label>
        <hr />
        {/* <Link to={`/projects/mtmc/${projectId}`}> */}
          <Form.Button
            color="purple"
            onClick={() => onClose()}
            data-testid="UploadFormClose"
          >
            Close
          </Form.Button>
        {/* </Link> */}
        {onDelete && (
          <div>
            <hr />
            <Header>DELETE PROJECT</Header>
            <p>The button bellow will delete the project and all its data.</p>
            <Button negative onClick={() => onDelete()}>
              DELETE PROJECT
            </Button>
          </div>
        )}
      </Form>
    );
  }
}

export default withAlert()(UploadFloorPlanProjectImageForm);

UploadFloorPlanProjectImageForm.propTypes = {
  /** ID of project to upload the image to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Reload the project when the image has been uploaded */
  reloadProject: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func
};
