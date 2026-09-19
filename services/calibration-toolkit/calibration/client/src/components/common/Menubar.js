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
import { Link } from "react-router-dom";
import DropdownItem from "./DropdownItem";
import axios from "axios";
import { Container, Menu, Icon, Dropdown } from "semantic-ui-react";
import logo from '../../assets/NVIDIA_LOGO_H_small.png';
import classes from './Menubar.css';
import SideMenuBar from "./SideMenuBar";
import DiscoverModal from "../modals/DiscoverModal";
import EditCityModal from "../modals/EditCityModal";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "./axios_instance";

/**
 * Menubar at the top of pages to display quick links between different pages.
 */
export default class Menubar extends Component {
  constructor(props) {
    super(props);

    this.state = {
      projectOptions: [],
      activeProject: null,
      activeProjectId: null,
      editCityModal: false,
      discoverSensorModal:false,


    };
    this.toggleCityModal = this.toggleCityModal.bind(this);
    this.toggleDiscoverModal = this.toggleDiscoverModal.bind(this)

  }

  /**
   * Load projects to be displayed in the projects dropdown menu when the
   * component mounts
   */
  async componentDidMount() {
    const { active } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/`)
      .then(res => {
        const projectOptions = res.data.map(project => {
          console.log("project op", project)
          if (Number(active) === Number(project.id)) {
            this.setState({
              activeProject: project.name,
              activeProjectId: project.id
            });
          }
          return { key: project.id, text: project.name, value: project.name };
        });
        this.setState({
          projectOptions
        });
      })
      .catch(error => {
        this.setState({
          error
        });
      });
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

  render() {
    const { active, children } = this.props;
    const { projectOptions, activeProject, activeProjectId,
            editCityModal, discoverSensorModal, projectName, projectId

          } = this.state;
    console.log("project page", active, activeProjectId, projectOptions, activeProject)
    return (
      <div style={{ background: "#FFFFFF", minHeight: "100vh" }}>
        <Menu   color="black" inverted>
          <Container>
            <Link to="/">
              <Menu.Item header>
                  <img
                      // style={{height: auto, width: auto }}
                      // resizeMode={'contain'}   /* <= changed  */
                      style={{width: 'auto', height: '25px'}}
                      className={classes.nvidialogo}
                      alt="Nvidia"
                      src={logo}
                  />
                </Menu.Item>
            </Link>
            <Menu.Item active={!!activeProject}>
              <Dropdown
                text={
                  !!activeProject
                    ? activeProject.length > 20
                      ? activeProject.substring(0, 16).concat("...")
                      : activeProject
                    : "Projects"
                }
                pointing="top"
              >
                <Dropdown.Menu>
                  {projectOptions.map((project, key) => {
                    return (
                      <DropdownItem
                        project={project}
                        key={key}
                        index={key}
                        active={active}
                      />
                    );
                  })}
                </Dropdown.Menu>
              </Dropdown>
            </Menu.Item>
            <Menu.Item active={active === "edit location"} onClick={this.toggleCityModal}>Edit Location</Menu.Item>
            <Menu.Item onClick={this.toggleDiscoverModal}>Setup Sensors</Menu.Item>
            <Menu.Item>Setup Floor Plan</Menu.Item>
            <Menu.Item>Calibrate Sensors</Menu.Item>
            <Link to={`/projects/${activeProjectId}`}>
              <Menu.Item active={active === "export"}>Export Sensors</Menu.Item>
            </Link>
            <Link to="/help/">
              <Menu.Item active={active === "help"}>
                <Icon name="help" style={{ marginRight: "5px" }} />
                Help
              </Menu.Item>
            </Link>
          </Container>
        </Menu>
        <Container>
          {/* <SideMenuBar></SideMenuBar> */}
          {children}
          <EditCityModal
              modalShow={editCityModal}
              cityId={projectId}
              reloadCity={this.loadCity}
              onClose={this.toggleCityModal}
              onDelete={this.handleDelete}
            />

            <DiscoverModal
              modalShow={discoverSensorModal}
              projectId={projectId}
              projectName={projectName}
              reloadSensors={this.loadCity}
              onClose={this.toggleDiscoverModal}
              onDelete={this.handleDelete}
            />
        </Container>
      </div>
    );
  }
}

Menubar.propTypes = {
  /** String representing the active tab on the menubar */
  active: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
};
