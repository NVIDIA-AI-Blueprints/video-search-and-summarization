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
import React, { Component } from "react";
// import arrayMove from 'array-move';
import { Button, Loader,Grid, GridColumn, Form, Segment  } from "semantic-ui-react";
import TopMenu from "../../../common/TopMenu/TopMenu"
import MTMCLeftMenu from "../../../common/LeftMenu/MTMCLeftMenu";
import MainContainer from "../../../common/MainContainer/MainContainer";
import DOMPurify from "dompurify";

import ProjectsPage from "../../../projects/ProjectPage"
import {  withAlert } from "react-alert";
import './ExportSensorsPage.css';

import { API_ENDPOINT } from "../../../common/axios_instance";
/**
 * Page displaying all the sensor, intersection, and corridor information for
 * the given Project. A Project page takes no props, except for the ProjectId passed
 * though the URL.
 */
class ExportSensorsPage extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      projectName: null,
      editProjectModal: false,
      editPlaceTreeHierarchyModal: false,
      discoverSensorModal:false,
      placeTypes_set: [],
      placeTypeHierarchy: {},
      calibrationType: ""
    };

    this.handleDelete = this.handleDelete.bind(this);
    this.loadProject = this.loadProject.bind(this);
    this.reloadSensors = this.reloadSensors.bind(this)
    this.toggleProjectModal = this.toggleProjectModal.bind(this);
    this.toggleDiscoverModal = this.toggleDiscoverModal.bind(this)
    this.toggleEditPlaceTreeHierarchyModal = this.toggleEditPlaceTreeHierarchyModal.bind(this)
    // this.renderTabs = this.renderTabs.bind(this);
    // this.openModule = this.openModule.bind(this);

  }



  /**
   * Run the loadProject function on component mount.
   */
  componentDidMount() {
    this.loadProject();
  }

  /**
   * Load all the data of the particular Project. The Project ID is passed via the
   * URL.
   */
  async loadProject() {
    const { match } = this.props;
    const { projectId } = match.params;
    await axios.get(`${API_ENDPOINT}/projects/${projectId}/`).then(res => {
      const projectName = res.data.name;
      const { placeTypes_set,placeTypeHierarchy, calibrationType } = res.data;
      this.setState({
        isLoaded: true,
        projectName,
        placeTypes_set,
        placeTypeHierarchy,
        calibrationType,
      });
    });
    this.forceUpdate()
  }


  /**
   * Get all sensors that belong to the specific Project, based on Project ID.
   */
  async reloadSensors() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const sensors = res.data.sensor_set;
        this.setState({
          isLoaded: true,
          sensors
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
   * Handle the action of deleting a Project. Deletes the Project from the backend
   * based on the Project ID passed via the URL. Pushes the user back to the
   * projects page.
   *
   * @TODO work on this
   */
  async handleDelete() {
    const { match, history } = this.props;
    const {  projectId } = match.params;
    await axios
      .delete(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        console.log("Deleted projects");
      })
      .catch(error => console.error("Delete project error", error));

    history.push(`/projects`);
  }

  /**
   * Toggle if the edit Project modal is shown or not.
   */
  toggleProjectModal() {
    const { editProjectModal } = this.state;
    this.setState({ editProjectModal: !editProjectModal });
  }


  /**
   * Toggle if the discover sensors modal is shown or not.
   */
  toggleDiscoverModal() {
    const { discoverSensorModal } = this.state;
    this.setState({ discoverSensorModal: !discoverSensorModal });
  }

  /**
   * Toggle if the discover sensors modal is shown or not.
   */
  toggleEditPlaceTreeHierarchyModal() {
    const { editPlaceTreeHierarchyModal } = this.state;
    console.log(editPlaceTreeHierarchyModal)
    this.setState({ editPlaceTreeHierarchyModal: !editPlaceTreeHierarchyModal });
  }




  render() {
    const {
      // projectName,
      // editProjectModal,
      // editPlaceTreeHierarchyModal,
      // discoverSensorModal,
      // floorPlanImageUrl,
      error,
      isLoaded,
      // placeTypeHierarchy,
      // placeTypes_set
    } = this.state;

    const { match } = this.props;
    const { projectId } = match.params;
    const locationState = DOMPurify.sanitize(this.props.location.state);


    if (error) {
      return <div>Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader active inline="centered" />;
    }

    return (
      <div>
      {/* <Menubar active={projectId}>      </Menubar> */}
      {/* <Menubar active={projectId}>      </Menubar> */}
        <TopMenu />
        <MTMCLeftMenu projectId={projectId}  />
        <MainContainer>
          {/* <Grid>
            <h1 style={{ display: "inline", wordWrap: "break-word" }}>
              {this.state.projectName}
            </h1>
          <h1></h1>
          </Grid> */}
      <Grid columns="equal" className="blendx_input">
        <Grid.Row> height={5}</Grid.Row>
        <Grid.Row>
          <Grid.Column width={8}>
            <Segment>
              <h1 style={{ display: "inline", wordWrap: "break-word" }}>
                {this.state.projectName}
              </h1>
            </Segment>
          </Grid.Column>
        </Grid.Row>
      </Grid>
          {/* {this.props.children} */}


          <Grid>
            <Grid.Row columns="equal">
            {/* <Grid.Column>
                <h1>Step 1: Upload FloorPlan</h1>
            </Grid.Column>
            <Grid.Column>
                <Button
                  color="green"
                  className="ui left floated button"
                  size="large"
                  onClick={this.toggleProjectModal}
                >
                  UPLOAD FLOORPLAN
                </Button>
                <Button
                  color="green"
                  className="ui right floated button"
                  size="large"
                  onClick={this.toggleEditPlaceTreeHierarchyModal}
                >
                  Edit Place Hierarchy
                </Button>
            </Grid.Column>
            </Grid.Row>
            <Grid.Row columns="equal">
            <Grid.Column >
                <h1>Step 2: Import MMS</h1>
            </Grid.Column>
            <Grid.Column>
                <Button
                  color="green"
                  className="ui left floated button"
                  size="large"
                  onClick={this.toggleDiscoverModal}
                >
                  DISCOVER SENSORS
                </Button>
            </Grid.Column>
            </Grid.Row>
            <Grid.Row columns="equal">
            <Grid.Column >
                <h1>Step 3: Setup Floor Plan</h1>
            </Grid.Column>
            <Grid.Column>
              <Link to={
                      (floorPlanImageUrl ) ? `/mtmc/floorplan/${projectId}`: "#"
                    }
                    >
                <Button
                  color="green"
                  className="ui left floated button"
                  size="large"
                  secondary={!(floorPlanImageUrl)}
                  onClick={() =>
                    !(floorPlanImageUrl)
                      // ? alert.show("Please upload floorplan first.")
                      ?<div class="ui negative message">
                        <i class="close icon"></i>
                        <div class="header">
                          Please upload floorplan first.
                        </div>
                      </div>
                      :   null}
                >
                  SETUP FLOOR PLAN
                </Button>
              </Link>
            </Grid.Column>
            </Grid.Row>
              <EditProjectModal
                modalShow={editProjectModal}
                projectId={projectId}
                reloadProject={this.loadProject}
                onClose={this.toggleProjectModal}
                onDelete={this.handleDelete}
              />
              <EditPlaceTreeHierachyModal
                key={placeTypes_set.length}
                modalShow={editPlaceTreeHierarchyModal}
                projectId={projectId}
                reloadProject={this.loadProject}
                onClose={this.toggleEditPlaceTreeHierarchyModal}
                onDelete={this.handleDelete}
                placeTypeHierarchy={placeTypeHierarchy}
              /> */}
              {/* <DiscoverModal
                modalShow={discoverSensorModal}
                projectId={projectId}
                projectName={projectName}
                reloadSensors={this.loadProject}
                onClose={this.toggleDiscoverModal}
                onDelete={this.handleDelete}
              /> */}
          {/* <Grid.Row>
            <Grid.Column>
              <h1>Step 4: Calibrate Sensors</h1>
            </Grid.Column> */}
          </Grid.Row>
          </Grid>'
          <ProjectsPage projectId={projectId}></ProjectsPage>
          {/* <Tabs defaultTab={!!locationState ? locationState.prevPath : "Sensors"}>
            {this.renderTabs()}
          </Tabs>  */}
        </MainContainer>
      </div>
    );
  }
}

export default withAlert()(ExportSensorsPage);