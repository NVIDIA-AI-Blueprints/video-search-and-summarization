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
import { Button, Loader,Grid, GridColumn  } from "semantic-ui-react";
import Menubar from "../common/Menubar";
import TopMenu from "../common/TopMenu/TopMenu"
import MTMCLeftMenu from "../common/LeftMenu/MTMCLeftMenu";
import MainContainer from "../common/MainContainer/MainContainer";
import Tabs from "../common/Tabs";
import DiscoverModal from "../modals/DiscoverModal";
import EditProjectModal from "../modals/EditProjectModal";
import EditPlaceTreeHierachyModal from "../modals/EditPlaceTreeHierarchyModal";
import NewPlaceTypeModal from "../modals/NewPlaceTypeModal";
import SensorsPage from "./SensorsPage";
import CorridorsGrid from "./CorridorsGrid";
import IntersectionsGrid from "./IntersectionsGrid";
import PlacesGrid from "./PlacesGrid";
import { Link } from "react-router-dom";
import DOMPurify from "dompurify";
import { useAlert } from "react-alert";
import './ProjectPage.css';

import {API_ENDPOINT} from "../common/axios_instances";
import { getMediaUrl } from "../../common/MediaUrl";
import { floor } from "mathjs";

/**
 * Page displaying all the sensor, intersection, and corridor information for
 * the given Project. A Project page takes no props, except for the ProjectId passed
 * though the URL.
 */
export default class MTMCProjectPage extends Component {
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

    this.handleDelete = this.handleDelete.bind(this);
    this.loadProject = this.loadProject.bind(this);
    this.reloadSensors = this.reloadSensors.bind(this)
    this.toggleProjectModal = this.toggleProjectModal.bind(this);
    this.toggleDiscoverModal = this.toggleDiscoverModal.bind(this)
    this.toggleEditPlaceTreeHierarchyModal = this.toggleEditPlaceTreeHierarchyModal.bind(this)
    this.renderTabs = this.renderTabs.bind(this);

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
      const { placeTypes_set,placeTypeHierarchy, calibrationType, floorPlanImageUrl } = DOMPurify.sanitize(res.data);
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

  /**
   * Render sensors, intersections, corridors, and any additional places tabs
   * in the Project display.
   */
  renderTabs() {
    const { projectName, placeTypes_set , calibrationType} = this.state;
    const { match } = this.props;
    const { projectId } = match.params;
    console.log("MTMCPAGE", projectId)
    let projectTabs = [
      <div key="Sensors" label="Sensors">
        <SensorsPage
          projectId={projectId}
          projectName={projectName}
          title="SENSORS:"
        />
      </div>
    ];
    //todo Render corridors/intersections only for GIS
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
    projectTabs.push(
      <div key="New PlaceType" label="New PlaceType">
        <NewPlaceTypeModal
          modalShow={true}
          projectId={projectId}
          projectName={projectName}
          reloadPlaces={this.loadProject}
          onClose={() => document.getElementById("Tab_Sensors").click()}
        />
      </div>
    );

    return projectTabs;
  }

  render() {
    const {
      projectName,
      editProjectModal,
      // editPlaceTreeHierarchyModal,
      discoverSensorModal,
      floorPlanImageUrl,
      error,
      isLoaded,
      // placeTypeHierarchy,
      // placeTypes_set
    } = this.state;
    // console.log("cp" ,  floorPlanImageUrl)

    const { match } = this.props;
    const { projectId } = match.params;
    const locationState = DOMPurify.sanitize(this.props.location.state);

    const menus = [
      {
        name: '1.) Upload Floor Plan',
        link: '/'
      },
      {
        name: '2.) Setup Sensors',
        link: '/'
        // icon: 'checkmark box',
        // submenus: [
        //   { name: 'input' },
        //   { name: 'range-picker' }
        // ]
      },
      {
        name: '3.) Setup Place Hierachy',
        link: '/'
        // icon: 'sitemap',
      },
      {
        name: '4.) Setup Floor Plan',
        link: '/'
        // icon: 'calendar check',
      },
      {
        name: '5.) Calibrate Sensors',
        link: '/'
        // icon: 'grid layout',
      },
      {
        name: '6.) Export',
        link: '/'
        // icon: 'bar chart',
      }
    ]

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
        <MTMCLeftMenu menus={menus} projectId={projectId} />
        <MainContainer>
          <h1 style={{ display: "inline", wordWrap: "break-word" }}>
            {projectName}
          </h1>
          <h1></h1>
          <Grid>
            <Grid.Row columns="equal">
            <Grid.Column>
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
                {/* <Button
                  color="green"
                  className="ui right floated button"
                  size="large"
                  onClick={this.toggleEditPlaceTreeHierarchyModal}
                >
                  Edit Place Hierarchy
                </Button> */}
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
              {/* <EditPlaceTreeHierachyModal
                key={placeTypes_set.length}
                modalShow={editPlaceTreeHierarchyModal}
                projectId={projectId}
                reloadProject={this.loadProject}
                onClose={this.toggleEditPlaceTreeHierarchyModal}
                onDelete={this.handleDelete}
                placeTypeHierarchy={placeTypeHierarchy}
              /> */}
              <DiscoverModal
                modalShow={discoverSensorModal}
                projectId={projectId}
                projectName={projectName}
                reloadSensors={this.loadProject}
                onClose={this.toggleDiscoverModal}
                onDelete={this.handleDelete}
              />
          <Grid.Row>
            <Grid.Column>
              <h1>Step 4: Calibrate Sensors</h1>
            </Grid.Column>
          </Grid.Row>
          </Grid>
          <Tabs defaultTab={!!locationState ? locationState.prevPath : "Sensors"}>
            {this.renderTabs()}
          </Tabs>
        </MainContainer>
      </div>
    );
  }
}
