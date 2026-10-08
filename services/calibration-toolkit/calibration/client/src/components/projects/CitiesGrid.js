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
import { Link } from "react-router-dom";
import { Button, Card, Grid, Header, Loader } from "semantic-ui-react";
import NewCityModal from "../modals/NewCityModal";
// import DiscoverModal from "../modals/DiscoverModal";

import {API_ENDPOINT} from "../common/axios_instances";

/**
 * Grid of cities. Each item in the grid will show the city, text explaining
 * what can be edited in the city, and the number of sensors/calibrated sensors.
 */
export default class CitiesGrid extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      cities: [],
      newCityModal: false,
      // newDiscoverModal: false
    };

    this.toggleModalShow = this.toggleModalShow.bind(this);
    // this.toggleDiscoverModalShow = this.toggleDiscoverModalShow.bind(this);
    this.loadCities = this.loadCities.bind(this);
  }

  /**
   * Run the loadCities function when the component mounts.
   */
  componentDidMount() {
    this.loadCities();
  }

  /**
   * Load all cities that are part of a specific project. At time of writing,
   * only one city is allowed per project.
   */
  async loadCities() {
    const { projectId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const cities = res.data.city_set;
        this.setState({
          isLoaded: true,
          cities
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
   * Get the total number of fully calibrated sensors within the city.
   * @param {object} city Object containing the city data.
   */
  getNumCalibCams(city) {
    let numCalibrated = 0;
    if (city.sensor_set.length) {
      city.sensor_set.forEach(sensor => {
        if (sensor.isValidated) {
          numCalibrated = numCalibrated + 1;
        }
      });
    }
    return numCalibrated;
  }

  /**
   * Toggle whether or not the new city modal is shown.
   */
  toggleModalShow() {
    const newCityModal = !this.state.newCityModal;
    this.setState({ newCityModal });
  }

  // /**
  //  * Toggle whether or not the new city modal is shown.
  //  */
  // toggleDiscoverModalShow() {
  //   const newDiscoverModal = !this.state.newDiscoverModal;
  //   this.setState({ newDiscoverModal });
  // }

  render() {
    const { linkPrefix, title, projectId, projectName } = this.props;

    const { cities, error, isLoaded, newCityModal,  } = this.state;

    if (error) {
      return <div data-testid="citiesError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="citiesLoader" active inline="centered" />;
    }

    const renderProjectCard = city => {
      const { id, name } = city;
      const info = `${city.sensor_set.length} sensors, ${this.getNumCalibCams(
        city
      )} calibrated`;
      return (
        <Grid.Column key={id}>
          <Link to={`${linkPrefix}${id}`}>
            <Card
              fluid
              link
              header={name}
              meta={`Click to edit location and sensors.`}
              description={info}
            />
          </Link>
        </Grid.Column>
      );
    };

    const renderedButton = (
      <Button
        color="green"
        style={{ padding: "1.5em" }}
        size="large"
        onClick={this.toggleModalShow}
      >
        <div>NEW LOCATION</div>
      </Button>
    );

    // const renderedDiscoverButton = (
    //   <Button
    //     color="green"
    //     style={{ padding: "1.5em" }}
    //     size="large"
    //     onClick={this.toggleDiscoverModalShow}
    //     floated="right"
    //   >
    //     <div>DISCOVER CAMERAS</div>
    //   </Button>
    // );

    return (
      <div data-testid="citiesGrid">
        <Header as="h1">{title}</Header>
        <Grid stackable columns={2} style={{ wordWrap: "break-word" }}>
          {cities.map(renderProjectCard)}
          {cities.length > 0 ? null : (
            <Grid.Row>
              <Grid.Column floated="left" width={5}>{renderedButton}</Grid.Column>
              {/* <Grid.Column floated="right" width={5}>{renderedDiscoverButton}</Grid.Column> */}
            </Grid.Row>
          )}
        </Grid>
        <NewCityModal
          modalShow={newCityModal}
          projectId={projectId}
          projectName={projectName}
          reloadCities={this.loadCities}
          onClose={this.toggleModalShow}
        />
        {/* <NewDiscoverModal
          modalShow={newDiscoverModal}
          projectId={projectId}
          projectName={projectName}
          reloadCities={this.loadCities}
          onClose={this.toggleDiscoverModalShow}
        /> */}
      </div>
    );
  }
}

CitiesGrid.propTypes = {
  /** ID of the project to load cities from */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Name of the project to load cities from */
  projectName: PropTypes.string,
  /** Link prefix used for redirecting pages */
  linkPrefix: PropTypes.string,
  /** Title to use for cities grid */
  title: PropTypes.string
};
