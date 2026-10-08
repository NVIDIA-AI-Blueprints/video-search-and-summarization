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
import Modal from "react-modal";
import update from "immutability-helper";
import axios from "axios";
import { Grid, Button, Loader } from "semantic-ui-react";
import { modalLayer1 } from "../../../common/utils";
import UploadFloorPlanProjectImageForm from "../../../forms/UploadFloorPlanProjectImageForm";
import TopMenu from "../../../common/TopMenu/TopMenu"
import CartesianLeftMenu from "../../../common/LeftMenu/CartesianLeftMenu";
import MainContainer from "../../../common/MainContainer/MainContainer";
import './CartesianCalibrateSensorsPage.css'
import Tabs from "../../../common/Tabs";
import SensorsPage from "../../../projects/SensorsPage";
import NewPlaceTypeModal from "../../../modals/NewPlaceTypeModal";
import DOMPurify from "dompurify";

import { API_ENDPOINT } from "../../../common/axios_instance";
import { getMediaUrl } from "../../../common/MediaUrl";

/**
 * Modal to use as popup for adding an image/screenshot to the city backend
 * via a file upload or an RTSP stream.
 */
class CartesianCalibrateSensorsPage extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      projectName: null,
      editProjectModal: false,
      editPlaceTreeHierarchyModal: false,
      floorPlanImageUrl: "",
      discoverSensorModal:false,
      placeTypes_set: [],
      placeTypeHierarchy: {},
      calibrationType: ""
    };
    this.handleSaveData = this.handleSaveData.bind(this);
    this.goBack = this.goBack.bind(this)
    this.loadProject = this.loadProject.bind(this);

  }
  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    // Modal.setAppElement("body");
    this.loadProject();

  }

  /**
   * Load all the data of the particular Project. The Project ID is passed via the
   * URL.
   */
   async loadProject() {
    console.log("UFPIP", this.props)
    const { match } = this.props;
    const { projectId } = match.params;
    await axios.get(`${API_ENDPOINT}/projects/${projectId}/`).then(res => {
      const projectName = res.data.name;
      const { placeTypes_set,placeTypeHierarchy, calibrationType, floorPlanImageUrl } = res.data;
      this.setState({
        isLoaded: true,
        projectName,
        placeTypes_set,
        placeTypeHierarchy,
        calibrationType,
        floorPlanImageUrl: getMediaUrl(floorPlanImageUrl)
      });
    });
    this.forceUpdate()
  }


  /**
   * Go back to the Sensors tab in the city page.
   */
   goBack = () => {
    const { projectId } = this.state;
    let path = `/projects/cartesian/calibrateSensors/${projectId}/`;
    let { history } = this.props;
    history.push({
      pathname: path,
      // state: { prevPath: "Sensors" }
    });
  };



  syncDataAllSensors(){
    return
  }

  /**
   * Render sensors, intersections, corridors, and any additional places tabs
   * in the Project display.
   */
   renderTabs() {
    const { projectName, placeTypes_set , calibrationType} = this.state;
    const { match } = this.props;
    const { projectId } = match.params;
    console.log("CartesianPAGE", projectId)
    let projectTabs = [
      <div key="Sensors" label="Sensors">
        <SensorsPage
          projectId={projectId}
          projectName={projectName}
          title="SENSORS:"
        />
      </div>
    ];
    //todo Render corridors/intersections only for Cartesian
    // ProjectTabs.push(
    //   <div key="Corridors" label="Corridors">
    //     <CorridorsGrid
    //       projectId={projectId}
    //       projectName={projectName}
    //       title="CORRIDORS:"
    //     />
    //   </div>
    // );
    // ProjectTabs.push(
    //   <div key="Intersections" label="Intersections">
    //     <IntersectionsGrid
    //       projectId={projectId}
    //       projectName={projectName}
    //       title="INTERSECTIONS:"
    //     />
    //   </div>
    // );
    // if (placeTypes_set.length) {
    //   placeTypes_set.forEach(type => {
    //     projectTabs.push(
    //       <div key={`${type.placeType}`} label={`${type.placeType}`}>
    // https://mail.nvidia.com/        <PlacesGrid
    //           projectId={projectId}
    //           placeType={type.placeType}
    //           placeTypeId={type.id}
    //           projectName={projectName}
    //           title={`${type.placeType}`}
    //           reloadProject={this.loadProject}
    //           calibrationType={calibrationType}
    //         />
    //       </div>
    //     );
    //   });
    // }
    // projectTabs.push(
    //   <div key="New PlaceType" label="New PlaceType">
    //     <NewPlaceTypeModal
    //       modalShow={true}
    //       projectId={projectId}
    //       projectName={projectName}
    //       reloadPlaces={this.loadProject}
    //       onClose={() => document.getElementById("Tab_Sensors").click()}
    //     />
    //   </div>
    // );

    return projectTabs;
  }
  /**
   * Handle event that user saves the data input into the form. The city ID is
   * used to patch the city in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSaveData(changedData, dataValidation) {
    // const { projectId } = this.props;
    const {projectId} = this.props.match.params
    let projectData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        projectData = update(projectData, { [key]: { $set: changedData[key] } });
        if (key === "floorPlanImageUrl"){

        }
      }
    });

    if (Object.entries(projectData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }


    await axios
      .patch(`${API_ENDPOINT}/projects/${projectId}/`, projectData)
      .then(() => {
        this.props.alert.success("RTSP Saved");
        this.props.reloadCity();
      })
      .catch(error => {
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
  }

  render() {
    const { modalShow, onClose } = this.props;
    const projectId = DOMPurify.sanitize(this.props.match.params.projectId)
    const locationState = DOMPurify.sanitize(this.props.location.state);

    console.log("ufppip", projectId, this.props.match, this.props)
    return (
      <div>
        <TopMenu />
        <CartesianLeftMenu projectId={projectId} />
        <MainContainer>
          <h1 style={{ display: "inline", wordWrap: "break-word" }}>
            {projectId}
          </h1>
          <h1></h1>
          <Grid
            // style={{ overlay: { zIndex: modalLayer1 } }}
            // isOpen={modalShow}
            // contentLabel="City FloorPlan Input"
          >
            {/* Test */}

          </Grid>
            <Tabs defaultTab={!!locationState ? locationState.prevPath : "Sensors"}>
            {this.renderTabs()}
          </Tabs>
        </MainContainer>
      </div>
    );
  }
}

export default withAlert()(CartesianCalibrateSensorsPage);

CartesianCalibrateSensorsPage.propTypes = {
  /** Indiactor whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the city the image is being uploaded to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the city after uploading the image */
  // reloadCity: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired
};
