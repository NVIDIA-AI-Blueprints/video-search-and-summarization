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
import { Card, Grid, Button, Loader, Header } from "semantic-ui-react";
import { withAlert } from "react-alert";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Grid of projects. Each project entry in the grid shows the place that is
 * associated with the project.
 */
class ProjectsGrid extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      projects: []
    };

    this.handleNewProject = this.handleNewProject.bind(this);
  }

  /**
   * Load all projects from the backend on component mount.
   */
  async componentDidMount() {
    await axios
      .get(`${API_ENDPOINT}/projects/`)
      .then(res => {
        const projects = res.data;
        this.setState({
          isLoaded: true,
          projects
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
   * Handle the new project selection. Creates a new project with the name
   * NEW_PROJECT and saves it in the backend.
   * @todo Find a way so that the name is different than NEW_PROJECT if a
   * project named NEW_PROJECT already exists.
   */
  async handleNewProject() {
    await axios
      .post(`${API_ENDPOINT}/projects/`, { name: "NEW_PROJECT" })
      .then(res => {
        const newProject = res.data;
        this.setState({
          projects: this.state.projects.concat([newProject])
        });
        alert.success(`NEW_PROJECT created`);
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

  render() {
    const { title } = this.props;

    const { projects, error, isLoaded } = this.state;

    if (error) {
      return (
        <div>
          <h2>Error: {error.message}</h2>
        </div>
      );
    } else if (!isLoaded) {
      return <Loader active inline="centered" />;
    }

    const renderProjectCard = project => {
      const { id, name } = project;
      let info = "";
      project.city_set.length
        ? (info = `Place: ${project.city_set[0].name}`)
        : (info = `No city added.`);
      return (
        <Grid.Column key={id}>
          <Link to={`/projects/${id}`}>
            <Card
              fluid
              link
              header={name}
              meta={`Click to edit place.`}
              description={info}
            />
          </Link>
        </Grid.Column>
      );
    };

    return (
      <div data-testid="ProjectsGrid">
        <Header as="h1">
          {title}
          <Button
            color="green"
            className="ui right floated button"
            size="large"
            onClick={this.handleNewProject}
          >
            NEW PROJECT
          </Button>
        </Header>
        <Grid
          stackable
          columns={1}
          style={{ wordWrap: "break-word" }}
          data-testid="ProjectsGrid"
        >
          {Array.isArray(projects) && projects.map(renderProjectCard)}
        </Grid>
      </div>
    );
  }
}

export default withAlert()(ProjectsGrid);

ProjectsGrid.propTypes = {
  /** Title to use for the grid of projects */
  title: PropTypes.string,
  /** Indicator whether or not to show the new project button */
  newButton: PropTypes.bool
};
