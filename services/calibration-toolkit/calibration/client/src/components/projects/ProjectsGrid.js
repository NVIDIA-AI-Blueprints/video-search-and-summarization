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
import {  Grid, Button, Loader, Header} from "semantic-ui-react";
import { withAlert } from "react-alert";

import NewProjectModal from "../modals/NewProjectModal";
import EditProjectModal from "../modals/EditProjectModal";
import UploadProjectModal from "../modals/UploadProjectModal";


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
      projects: [],
      newProjectModal: false,
      uploadProjectModal: false,
      editProjectModal: false,
      editingProject: null
    };

    // this.handleNewProject = this.handleNewProject.bind(this);
    this.toggleEditProjectModal = this.toggleEditProjectModal.bind(this)
    this.toggleUploadProjectModal = this.toggleUploadProjectModal.bind(this)
    this.toggleNewProjectModal = this.toggleNewProjectModal.bind(this)
    this.loadProject = this.loadProject.bind(this)
    this.getCalibrationName = this.getCalibrationName.bind(this)
  }

  /**
   * run Load projects from the backend on component mount.
   */
  async componentDidMount() {
    this.loadProject()
  }

  /**
   * List projects.
   */
  async loadProject() {
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


  // /**
  //  * Handle the new project selection. Creates a new project with the name
  //  * NEW_PROJECT and saves it in the backend.
  //  * @todo Find a way so that the name is different than NEW_PROJECT if a
  //  * project named NEW_PROJECT already exists.
  //  */
  // async handleNewProject() {
  //   await axios
  //     .post(`${API_ENDPOINT}/projects/`, { name: "NEW_PROJECT" })
  //     .then(res => {
  //       const newProject = res.data;
  //       this.setState({
  //         projects: this.state.projects.concat([newProject])
  //       });
  //       alert.success(`NEW_PROJECT created`);
  //     })
  //     .catch(error => {
  //       const { alert } = this.props;
  //       if (error.response) {
  //         const { data } = error.response;
  //         Object.keys(data).forEach(key => {
  //           alert.error(`Error in ${key}. ${data[key]}`);
  //         });
  //       } else if (error.request) {
  //         console.error("No response from server.");
  //       }
  //     });
  // }


    /**
   * Toggle if the edit city modal is shown or not.
   */
     toggleEditProjectModal(projectId) {
      this.setState({ editingProject: projectId });
      const editProjectModal = !this.state.editProjectModal;
      this.setState({ editProjectModal });
    }

  /**
   * Toggle whether or not the new project modal
   */
  toggleNewProjectModal() {
    const newProjectModal = !this.state.newProjectModal;

    this.setState({ newProjectModal})
  }

    /**
   * Toggle whether or not the import project modal shows
   */
    toggleUploadProjectModal() {
      const uploadProjectModal = !this.state.uploadProjectModal;
      this.setState({ uploadProjectModal})
    }


  getCalibrationName(calibType){
    let calibName = ""
    if (calibType === "mtmc"){
      calibName = "Multi Sensor Tracking"
    }
    else if (calibType === "geo"){
      calibName = "GIS Calibration"
    }
    else if (calibType === "cartesian"){
      calibName = "Cartesian Calibration"
    }
    else if (calibType === "floorplan"){
      calibName = "Floorplan Calibration"
    }
    else if (calibType === "image"){
      calibName = "Image Calibration"
    }
    return calibName
  }


  render() {
    const { title } = this.props;

    const {
      projects,
      error,
      isLoaded,
      newProjectModal,
      uploadProjectModal,
      editProjectModal,
      editingProject
    } = this.state;

    // console.log ("pp1", projects)
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
      // console.log("pp", project)
      const { id, name, calibrationType } = project;
      // let info = "";
      // project.city_set.length
      //   ? (info = `Place: ${project.city_set[0].name}`)
      //   : (info = `No city added.`);
      const projectTypeName = this.getCalibrationName(calibrationType)
      const projectType = `${projectTypeName}`;
      return (
        <Grid.Row key={id}>
          <Grid.Column verticalAlign="middle" width={3}>
            {name}
          </Grid.Column>
          <Grid.Column verticalAlign="middle" horizontalAlign="center" width={5}>
            {projectType}
          </Grid.Column>
          <Grid.Column verticalAlign="middle" horizontalAlign="right"  width={6}>
            <div>
              <Link to={`/projects/${project.calibrationType}/${id}`}>
                <Button
                  className="ui compact left floated button"
                  // onClick={() => this.toggleEditProjectModal(id)}
                  color="green"
                  icon="folder"
                  label="Enter Project"
                  size="tiny"
                />
              </Link>
            <Button
                    onClick={() => this.toggleEditProjectModal(id)}
                    color="green"
                    icon="pencil"
                    label="Edit Project"
                    size="tiny"
            />
            </div>
          </Grid.Column>
          {/* <Link to={`/projects/${project.calibrationType}/${id}`}> */}
            {/* <Card
                fluid
                href={`/projects/${project.calibrationType}/${id}`}
                header={name}
                meta={`Click to edit project.`}
                // extra={projectType}
                extra={<Button Icon="edit" onClick={this.toggleEditProjectModal(id)}>{projectType}</Button>}
              /> */}
          {/* </Link> */}
        </Grid.Row>
      );
    };
    // const renderedEditButton =(
    //   <Button
    //   color="green"
    //   className="ui right floated button"
    //   size="large"
    //   onClick={this.toggleEditProjectModal(id)}
    // >
    //   Edit PROJECT
    // </Button>
    // )
    // const renderedNewButton =(
    //   <Button
    //   color="green"
    //   className="ui right floated button"
    //   size="large"
    //   onClick={this.toggleNewProjectModal}
    // >
    //   NEW PROJECT
    // </Button>
    // )
    return (
      <div data-testid="ProjectsGrid">
        <Header as="h1">
          {title}
          {
            (<Button
              color="green"
              className="ui right floated button"
              size="large"
              onClick={this.toggleNewProjectModal}
            >
              NEW PROJECT
            </Button>)
          }
          {
            (<Button
              color="green"
              className="ui right floated button"
              size="large"
              onClick={this.toggleUploadProjectModal}
            >
              IMPORT PROJECT
            </Button>)
          }
        </Header>
        <Grid
          stackable
          columns={1}
          style={{ wordWrap: "break-word" }}
          data-testid="ProjectsGrid"
        >
          <Grid.Row></Grid.Row>
          <Grid.Row color="grey" key={"header"}>
            <Grid.Column verticalAlign="middle" width={3}>
              <h3>Project Name</h3>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={5}>
              <h3>Calibration Project Type</h3>
            </Grid.Column>
            <Grid.Column verticalAlign="middle" textAlign="center" width={6}>
              <h3>Actions</h3>
            </Grid.Column>
          </Grid.Row>
          {Array.isArray(projects) && projects.map(renderProjectCard)}
        </Grid>
        <NewProjectModal
          modalShow={newProjectModal}
          reloadProject={this.loadProject}
          onClose={this.toggleNewProjectModal}
        />
        <UploadProjectModal
        modalShow={uploadProjectModal}
        reloadProject={this.loadProject}
        onClose={this.toggleUploadProjectModal}
         />
        <EditProjectModal
          modalShow={editProjectModal}
          projectId={editingProject}
          reloadProject={this.loadProject}
          onClose={this.toggleEditProjectModal}
        />

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
