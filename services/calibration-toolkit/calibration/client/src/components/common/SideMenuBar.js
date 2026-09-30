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
import { Header, Image, Segment, Sidebar, Container, Menu, Label, Input, Icon, Dropdown } from "semantic-ui-react";
// import logo from '../../assets/NVIDIA_LOGO_H_small.png';
import classes from './Menubar.css';
import { NavLink } from 'react-router-dom';
import MenuBar from "../common/Menubar";

import {API_ENDPOINT} from "./axios_instance";

/**
 * Menubar at the top of pages to display quick links between different pages.
 */
export default class SideMenuBar extends Component {
  constructor(props) {
    super(props);

    this.state = {
      projectOptions: [],
      activeProject: null,
      activeItem: 'inbox'
    };
    this.handleItemClick = this.handleItemClick.bind(this);
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
          if (Number(active) === Number(project.id)) {
            this.setState({ activeProject: project.name });
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

  handleItemClick = (e, { name }) => this.setState({ activeItem: name })

  render() {
    const { active, children } = this.props;
    const { projectOptions, activeProject, activeItem } = this.state;

    return (
      <div style={{ background: "#FFFFFF", minHeight: "100vh" }}>
        {/* <MenuBar active ="help"> </MenuBar> */}

        <Menu vertical  right>

          <Menu.Item
            as={NavLink} exact to="/mtmc/setupFloorPlan"
            name='floorplan'
            active={activeItem === 'floorplan'}
            onClick={this.handleItemClick}
          >
            Setup Floor Plan
          </Menu.Item>

          <Menu.Item
            as={NavLink} exact to="/mtmc/setupSensors"
            name='sensors'
            active={activeItem === 'sensors'}
            onClick={this.handleItemClick}
          >
            Setup Sensors
          </Menu.Item>

        </Menu>

        <Container>{children}</Container>
      </div>
    );
  }
}

SideMenuBar.propTypes = {
  /** String representing the active tab on the menubar */
  active: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
};
