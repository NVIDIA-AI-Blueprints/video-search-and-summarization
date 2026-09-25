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
import Tabs from "../common/Tabs";
import DiscoverModal from "../modals/DiscoverModal";
import EditCityModal from "../modals/EditCityModal";
// import EditPlaceTreeHierachyModal from "../modals/EditPlaceTreeHierarchyModal";
import NewPlaceTypeModal from "../modals/NewPlaceTypeModal";
import SensorsPage from "./SensorsPage";
import CorridorsGrid from "./CorridorsGrid";
import IntersectionsGrid from "./IntersectionsGrid";
import PlacesGrid from "./PlacesGrid";
import { Link } from "react-router-dom";
import DOMPurify from "dompurify";

import { useAlert } from "react-alert";


import {API_ENDPOINT} from "../common/axios_instance";
import { getMediaUrl } from "../common/MediaUrl";

/**
 * Page displaying all the sensor, intersection, and corridor information for
 * the given city. A city page takes no props, except for the cityId passed
 * though the URL.
 */
export default class CityPage extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      cityName: null,
      editCityModal: false,
      editPlaceTreeHierarchyModal: false,
      floorPlanImageUrl: "",
      discoverSensorModal:false,
      placeTypes_set: [],
      placeTypeHierarchy: {},
      calibrationType: ""
    };

    this.handleDelete = this.handleDelete.bind(this);
    this.loadCity = this.loadCity.bind(this);
    this.reloadSensors = this.reloadSensors.bind(this)
    this.toggleCityModal = this.toggleCityModal.bind(this);
    this.toggleDiscoverModal = this.toggleDiscoverModal.bind(this)
    this.toggleEditPlaceTreeHierarchyModal = this.toggleEditPlaceTreeHierarchyModal.bind(this)
    this.renderTabs = this.renderTabs.bind(this);

  }



  /**
   * Run the loadCity function on component mount.
   */
  componentDidMount() {
    this.loadCity();
  }

  /**
   * Load all the data of the particular city. The city ID is passed via the
   * URL.
   */
  async loadCity() {
    const { match } = this.props;
    const { cityId } = match.params;
    await axios.get(`${API_ENDPOINT}/cities/${cityId}/`).then(res => {
      const cityName = res.data.name;
      const { placeTypes_set,placeTypeHierarchy, calibrationType, floorPlanImageUrl } = res.data;
      this.setState({
        isLoaded: true,
        cityName,
        placeTypes_set,
        placeTypeHierarchy,
        calibrationType,
        floorPlanImageUrl: getMediaUrl(floorPlanImageUrl)
      });
    });
    this.forceUpdate()
  }


  /**
   * Get all sensors that belong to the specific city, based on city ID.
   */
  async reloadSensors() {
    const { cityId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/cities/${cityId}/`)
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
   * Handle the action of deleting a city. Deletes the city from the backend
   * based on the city ID passed via the URL. Pushes the user back to the
   * projects page.
   */
  async handleDelete() {
    const { match, history } = this.props;
    const { cityId, projectId } = match.params;
    await axios
      .delete(`${API_ENDPOINT}/cities/${cityId}/`)
      .then(res => {
        console.log("Deleted city");
      })
      .catch(error => console.error("Delete city error", error));

    history.push(`/projects/${projectId}`);
  }

  /**
   * Toggle if the edit city modal is shown or not.
   */
  toggleCityModal() {
    const { editCityModal } = this.state;
    this.setState({ editCityModal: !editCityModal });
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
   * in the city display.
   */
  renderTabs() {
    const { cityName, placeTypes_set , calibrationType} = this.state;
    const { match } = this.props;
    const { projectId, cityId } = match.params;
    let cityTabs = [
      <div key="Sensors" label="Sensors">
        <SensorsPage
          projectId={projectId}
          cityId={cityId}
          cityName={cityName}
          title="SENSORS:"
        />
      </div>
    ];
    //todo Render corridors/intersections only for GIS
    // cityTabs.push(
    //   <div key="Corridors" label="Corridors">
    //     <CorridorsGrid
    //       projectId={projectId}
    //       cityId={cityId}
    //       cityName={cityName}
    //       title="CORRIDORS:"
    //     />
    //   </div>
    // );
    // cityTabs.push(
    //   <div key="Intersections" label="Intersections">
    //     <IntersectionsGrid
    //       projectId={projectId}
    //       cityId={cityId}
    //       cityName={cityName}
    //       title="INTERSECTIONS:"
    //     />
    //   </div>
    // );
    if (placeTypes_set.length) {
      placeTypes_set.forEach(type => {
        cityTabs.push(
          <div key={`${type.placeType}`} label={`${type.placeType}`}>
            <PlacesGrid
              projectId={projectId}
              cityId={cityId}
              placeType={type.placeType}
              placeTypeId={type.id}
              cityName={cityName}
              title={`${type.placeType}`}
              reloadCity={this.loadCity}
              calibrationType={calibrationType}
            />
          </div>
        );
      });
    }
    cityTabs.push(
      <div key="New PlaceType" label="New PlaceType">
        <NewPlaceTypeModal
          modalShow={true}
          projectId={projectId}
          cityId={cityId}
          cityName={cityName}
          reloadPlaces={this.loadCity}
          onClose={() => document.getElementById("Tab_Sensors").click()}
        />
      </div>
    );

    return cityTabs;
  }

  render() {
    const {
      cityName,
      editCityModal,
      // editPlaceTreeHierarchyModal,
      discoverSensorModal,
      floorPlanImageUrl,
      error,
      isLoaded,
      // placeTypeHierarchy,
      // placeTypes_set
    } = this.state;

    const { match } = this.props;
    const { projectId, cityId } = match.params;
    const locationState = DOMPurify.sanitize(this.props.location.state);

    if (error) {
      return <div>Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader active inline="centered" />;
    }

    return (

      <Menubar active={projectId}>
        <h1 style={{ display: "inline", wordWrap: "break-word" }}>
          {cityName}
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
                onClick={this.toggleCityModal}
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
                    (floorPlanImageUrl ) ? `/mtmc/floorplan/${cityId}`: "#"
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
            <EditCityModal
              modalShow={editCityModal}
              cityId={cityId}
              reloadCity={this.loadCity}
              onClose={this.toggleCityModal}
              onDelete={this.handleDelete}
            />
            {/* <EditPlaceTreeHierachyModal
              key={placeTypes_set.length}
              modalShow={editPlaceTreeHierarchyModal}
              cityId={cityId}
              reloadCity={this.loadCity}
              onClose={this.toggleEditPlaceTreeHierarchyModal}
              onDelete={this.handleDelete}
              placeTypeHierarchy={placeTypeHierarchy}
            /> */}
            <DiscoverModal
              modalShow={discoverSensorModal}
              cityId={cityId}
              cityName={cityName}
              reloadSensors={this.loadCity}
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

      </Menubar>
    );
  }
}
