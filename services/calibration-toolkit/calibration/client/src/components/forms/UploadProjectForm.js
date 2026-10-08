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
import { Form, Header, Button, Loader } from "semantic-ui-react";
import axios from "axios";
import update from "immutability-helper";

import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";

import {API_ENDPOINT} from "../common/axios_instance";
import { valid } from "joi";
import { getMediaUrl } from "../common/MediaUrl";
import DOMPurify from "dompurify";
/**
 * Form for uploading a calibration image from either a RTSP stream or a jpg/png
 * screenshot.
 */
class UploadProjectForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      projectData: {
        calibrationJsonTemp: "",
        imageMetaDataJsonTemp: "",
        imageFiles: "",
        id:"",
        mmsURL:"",
      },
      projectId:"",
      parsedData: {},
      uploadFiles : false,
      projectName:"",
      dataValidation: {
        calibrationJsonTemp: false,
        imageMetaDataJsonTemp: false,
        imageFiles: false,
        url: false,
      },
      isEdited: {
        calibrationJsonTemp: false,
        imageMetaDataJsonTemp: false,
        imageFiles: false,
        url: false,
      },
      isLoadingImportProject: false,
      isLoaded: false,
      isUploadedSuccesful: false,
      error: false,
      isUploading: false
    };

    this.sanitizeInput = this.sanitizeInput.bind(this)
    this.setImageResolution = this.setImageResolution.bind(this);
    this.loadImageResolution = this.loadImageResolution.bind(this);
    this.readFileAsync = this.readFileAsync.bind(this)
    this.onUploadData = this.onUploadData.bind(this)
    this.handleDataChange = this.handleDataChange.bind(this);
    this.setAllEdited = this.setAllEdited.bind(this);
    this.checkSuccessfulUpload = this.checkSuccessfulUpload.bind(this);
  }

  sanitizeInput(input) {
      const allowedValues = ["mtmc", "cartesian", "image", "geo"]; // Example: only allow predefined types
      if (allowedValues.includes(input.calibrationType)) {
          return input;
      } else {
          throw new Error("Invalid calibration type");
      }
  }

  async createNewProject(){
    const randomId = Math.floor(Math.random()*100000);
    let name = `NewUploadProject_${randomId}`
    let calibrationType = "mtmc"
    let projectData = {name,calibrationType}
    projectData = this.sanitizeInput(projectData);

    await axios
    .post(`${API_ENDPOINT}/projects/`, projectData)
    .then(res => {
        const projectData = res.data;
        const {id,name } = projectData
        console.log("upprmo", projectData)
        this.setState({ isLoaded: true, projectId: id, projectName: name});
        this.props.reloadProject();
        // this.forceUpdate()
      // this.props.alert.success("Created Project!");
      // this.props.reloadSensors();
        // this.setState()
    })
    .catch(error => {
      const { alert } = this.props;
      if (error.response) {
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
   * Get rtsp url oon component mount
   */
  async componentDidMount() {
    const { projectId } = this.props;
    if (projectId) {
      await axios
        .get(`${API_ENDPOINT}/projects/${projectId}/`)
        .then(res => {
          const projectData = res.data;
          this.setState({ isLoaded: true, projectData});
        })
        .catch(error => {
            this.setState({
              isLoaded: true,
              error
            });
          });
    } else {
      this.createNewProject()
    }
  }
  /**
   * Handle the action of the user choosing to delete the intersection. Deletes
   * the intersection from the backend based on its ID and reloads the page to
   * show the intersection removal.
   */
  async handleDelete(projectId) {
    await axios
      .delete(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(() => {
        this.props.alert.error(`Upload was not successful.`);
      })
      .catch(error => console.error(error));
  }

  /**
   * Request the calculation of the homography to be done in the backend.
   */
  async checkSuccessfulUpload(projectId) {
    //update
    console.log("uppm", projectId)
    // if (projectId) {
    //   await axios
    //     .get(`${API_ENDPOINT}/projects/${projectId}/`)
    //     .then(res => {
    //       const projectData = res.data;
    //       console.log("uppm", projectData)
    //       if (projectData.calibrationJsonTemp === "" && projectData.imageMetaDataJsonTemp === ""){
    //         console.log("uppm", "Blanks", projectId)
    //         this.handleDelete(projectId)
    //         // this.props.reloadProject()

    //       }
    //     })
    //     .catch(error => {
    //       console.error(error)
    //       });
    // }
    console.log("uppm", this.state.parsedData, this.state.uploadFiles, this.state.isUploadedSuccesful)
    if (this.state.isUploadedSuccesful === false) {
      // Call the backend API here
      // Example: axios.post(API_ENDPOINT, this.state.parsedData)
      // console.log('Calling backend API with parsed data:', this.state.parsedData);
      //console.log("I am here", projectId);
      this.handleDelete(projectId)
      this.props.reloadProject()

    } else {
      // Show an error message or handle the case where parsedData doesn't contain two files
      // console.error('Data must contain exactly three files.');
      // this.props.alert.error(`Data must contain exactly three files`);
      console.error("upload succesful ")

    }

  }
  /**
   * Request the calculation of the homography to be done in the backend.
   */
  async onUploadData(projectId) {
    //update
    console.log("uppm", projectId)
    // if (projectId) {
    //   await axios
    //     .get(`${API_ENDPOINT}/projects/${projectId}/`)
    //     .then(res => {
    //       const projectData = res.data;
    //       console.log("uppm", projectData)
    //       if (projectData.calibrationJsonTemp === "" && projectData.imageMetaDataJsonTemp === ""){
    //         console.log("uppm", "Blanks", projectId)
    //         this.handleDelete(projectId)
    //         // this.props.reloadProject()

    //       }
    //     })
    //     .catch(error => {
    //       console.error(error)
    //       });
    // }
    console.log("uppm", this.state.parsedData, this.state.uploadFiles)
    if (Object.keys(this.state.parsedData).length === 2 && this.state.uploadFiles) {
      // Call the backend API here
      // Example: axios.post(API_ENDPOINT, this.state.parsedData)
      // console.log('Calling backend API with parsed data:', this.state.parsedData);
      // console.log("I am here")
      await axios
      .get(`${API_ENDPOINT}/uploadFiles/${projectId}/`)
      .then(resp => {
        this.setState({isUploadedSuccesful: true})
        // if resp.data
        if (resp.data === "True") {
          this.props.alert.success("Successful Upload")
          console.log("resp", resp)
          this.props.reloadProject()
        } else{
          this.props.alert.error("Upload was not Successful. Check logs..")
          console.log("resp", resp)
          this.handleDelete(projectId)
          this.props.reloadProject()
        }

      }
      )
      .catch(
        error =>{
          console.error(error)
          this.props.alert.error("Upload was not Successful")
          this.handleDelete(projectId)
        }
      );
    } else {
      // Show an error message or handle the case where parsedData doesn't contain two files
      console.error('Data must contain exactly three files.');
      this.props.alert.error(`Data must contain exactly three files`);

    }

  }

  /**
   * Upload and save an image to the backend from a file obtained using the file
   * explorer interface.
   * @param {number} sensorId Public key representing the sensor ID
   * @param {object} files Information about the selected image to use for
   * calibration
   */
  async handleUploadJson(e) {
    const files = e.target.files;
    if (files) {
      const promises = Array.from(files).map(file => this.readFileAsync(file));
      try {
        const parsedDataArray = await Promise.all(promises);
        const parsedData = {};
        parsedDataArray.forEach(item => {
          parsedData[item.fileName] = item.data;
        });
        console.log('Parsed Data:', parsedData, this.state.projectName, window.location.origin );

        // Now you have parsedData object containing parsed JSON from all files
        // You can use parsedData in your logic here
        // For example, sending the parsed data to the backend

        const projectData = {
          // your project data here
          calibrationJsonTemp: JSON.stringify(parsedData['calibration.json']),
          imageMetaDataJsonTemp: JSON.stringify(parsedData['imageMetadata.json']),
        };
        console.log("project data",`${API_ENDPOINT}/projects/${this.state.projectId}/`, this.state.projectId, projectData, this.state.projectName)

        await axios
        .patch(`${API_ENDPOINT}/projects/${this.state.projectId}/`, projectData)
        .then(() =>{
          this.setState({parsedData})
          // this.props.reloadProject();
          this.props.alert.success(`Uploaded project json`);
        })
        .catch(error => {
          console.log(API_ENDPOINT)
          console.log(error)
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

        // this.props.importProject();
        // this.props.reloadProject();
      } catch (error) {
        console.error('Error parsing or uploading files:', error);
      }
    }
  }


  /**
   * Upload and save an image to the backend from a file obtained using the file
   * explorer interface.
   * @param {number} sensorId Public key representing the sensor ID
   * @param {object} files Information about the selected image to use for
   * calibration
   */
  async handleUploadImageZip(files) {
    this.setState({ isUploading: true });
    console.log("upfiz",files)
    // const validFiles = this.checkJSON(files)

    let form_data = new FormData();
    Array.from(files).map(file =>{
      console.log("upfizw", file, file.name)
      if (file.name.endsWith("Images.zip")){
        console.log("upfizs")
        form_data.append("imageFiles", file, file.name);
      }else{
        this.props.alert.error("Images.zip is not named properly")
      }
    })
    console.log("upfizz", form_data , `${API_ENDPOINT}/projects/${this.state.projectId}/`)
    
    try {
      const res = await axios.patch(`${API_ENDPOINT}/projects/${this.state.projectId}/`, form_data);
      console.log(res.data)
      this.setState({uploadFiles: true})
      this.props.alert.success(`Uploaded Images.zip`);
    } catch (error) {
      console.error(error);
      this.props.alert.error("Failed to upload Images.zip");
    } finally {
      this.setState({ isUploading: false });
    }
  }

  /**
   * Save the resolution of the image (height and width) to the backend of the
   * sensor.
   */
  async setImageResolution() {
    const { sensorId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/sensors/${sensorId}/`)
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
    const { sensorId } = this.props;
    let img = new Image();
    img.onload = async function() {
      const { height, width } = this;

      const floorPlanImHeight = height
      const floorPlanImWidth = width
      // const { invertImHeight, invertImWidth} = this;
      await axios
        .patch(`${API_ENDPOINT}/sensors/${sensorId}/`, { floorPlanImWidth, floorPlanImHeight })
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

  setAllEdited() {
    const { isEdited } = this.state;
    const { projectId } = this.props;
    if (!projectId) {
      let updatedEdited = {};
      Object.keys(isEdited).forEach(key => {
        updatedEdited[key] = true;
      });
      this.setState({ isEdited: updatedEdited });
    }
  }


  async readFileAsync(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();

      // Check if the file name ends with "a.json" or "b.json"
      if (file.name.endsWith('calibration.json') || file.name.endsWith('imageMetadata.json')) {
        reader.onload = function(event) {
          try {
            const jsonData = JSON.parse(event.target.result);
            resolve({ fileName: file.name, data: jsonData });
          } catch (error) {
            reject(error);
          }
        };

        reader.onerror = function(event) {
          reject(new Error('Error reading the file.'));
        };

        reader.readAsText(file);
      } else {
        reject(new Error('Invalid file format. Please upload a .json file ending with "a.json" or "b.json".'));
      }
    });
  }


  render() {
    const {
      onClose,
      onDelete,
      onSaveData,
      reloadProject
    } = this.props;
    const {
      dataValidation,
      projectId,
      isEdited,
      isLoadingImportProject,
      projectData,

      isLoaded,
      error
    } = this.state;
    const {
      mmsURL
    } = projectData;
    // const { rtspURL } = sensorData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    if (isLoadingImportProject) {
      return (
        <div style={{ textAlign: "center" }}>
          <h1>Trying to Import project...</h1>
          <p>Please wait. This may take a few seconds.</p>
          <Loader active inline="centered" />
        </div>
      );
    }
    return (
      <Form>
        <h1>Upload Calibration.json and ImageMetadata.json:</h1>
        <label>
          <input
            onChange={async e => {
               await this.handleUploadJson( e);
              // this.setImageResolution();
              // reloadProject();
               this.props.reloadProject();

            }}
            type="file"
            multiple
            accept=".json"
            disabled={this.state.isUploading}
          />
        </label>
        <hr />
        <h1>Upload Images.zip:</h1>
        <label>
          <input
            onChange={async e => {
              await this.handleUploadImageZip( e.target.files);
              // this.setImageResolution();
              // reloadProject();
              this.props.reloadProject();
            }}
            type="file"
            multiple
            accept=".zip"
            disabled={this.state.isUploading}
          />
        </label>
        {/* <h1>Metropolis Media Server Address:</h1>
        <Form.Input
            // label="Metropolis Media Server Address"
            content="mmsURL"
            value={mmsURL}
            error={
              isEdited.mmsURL && !dataValidation.mmsURL
                ? "Invalid NVStreamer or VST URL Name. Maximum Length: 200 Characters."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="ProjectMMSInput"
          /> */}
        <hr />
        <Form.Group>
          <Form.Button
            color="green"
            onClick={async () => {
              this.setState({ isLoadingImportProject: true });
              await onSaveData(projectData, dataValidation, this.state.projectId);
              await this.onUploadData(this.state.projectId)
              this.setState({ isLoadingImportProject: false });
            }}
            data-testid="UploadFormClose"
            disabled={this.state.isUploading || this.state.isLoadingImportProject}
            loading={this.state.isUploading || this.state.isLoadingImportProject}
          >
            Upload
          </Form.Button>
          <Form.Button
            color="purple"
            onClick={async () => {
              await this.checkSuccessfulUpload(this.state.projectId)
              this.props.reloadProject()
              onClose()}}
            data-testid="UploadFormClose"
            disabled={this.state.isUploading || this.state.isLoadingImportProject}
          >
            Close
          </Form.Button>
        </Form.Group>
        {/* {onDelete && (
          <div>
            <hr />
            <Header>DELETE CAMERA</Header>
            <p>The button bellow will delete the sensor and all its data.</p>
            <Button negative onClick={() => onDelete()}>
              DELETE CAMERA
            </Button>
          </div>
        )} */}
      </Form>
    );
  }
}

export default withAlert()(UploadProjectForm);

UploadProjectForm.propTypes = {
  /** Handle the action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Reload the sensor when the image has been uploaded */
  reloadProject: PropTypes.func,
  /** Handle the action of closing the form */
  onClose: PropTypes.func,
};
