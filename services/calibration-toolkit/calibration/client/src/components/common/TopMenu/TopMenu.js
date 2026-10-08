// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import { Link } from 'react-router-dom';
import { Menu, Icon, Dropdown } from 'semantic-ui-react';
// import TopSearch from './TopSearch';
import MyMenu from './MyMenu';
import logo from '../../../assets/NVIDIA_LOGO_H_small.png';
import Notification from '../Notification/Notification';
import './TopMenu.css';

class TopMenu extends Component {
  state = { activeItem: 'inbox' };

  handleItemClick = (e, { name }) => this.setState({ activeItem: name });

  render() {
    const { activeItem } = this.state;

    let iconStyle = {
      margin: '0 10px 0 0'
    };

    return (
      <Menu pointing secondary className="top-menu">
        <Menu.Menu postion="left" className="menu-logo">
          <Menu.Item header>
            <img
                // style={{height: auto, width: auto }}
                // resizemode={'contain'}   /* <= changed  */
                style={{width: 'auto', height: '25px'}}
                // className={.nvidialogo}
                alt="Nvidia"
                src={logo}
            />
          </Menu.Item>
        </Menu.Menu>
        <Menu.Menu className="center menu">
          <Link to="/">
            <Menu.Item
              name="home"
              active={activeItem === 'home'}
              onClick={this.handleItemClick}
            >
              <Icon name="home" size="large" style={iconStyle} />
              <span>Home</span>
            </Menu.Item>
          </Link>

          {/* <Menu.Item
            name="portfolio"
            active={activeItem === 'portfolio'}
            onClick={this.handleItemClick}
          >
            <Icon name="cubes" size="large" style={iconStyle} />
            <span>Portfolio</span>
            <Dropdown>
              <Dropdown.Menu>
                <Dropdown.Header>Categories</Dropdown.Header>
                <Dropdown.Item>Home Goods</Dropdown.Item>
                <Dropdown.Item>Bedroom</Dropdown.Item>
                <Dropdown.Divider />
                <Dropdown.Header>Order</Dropdown.Header>
                <Dropdown.Item>Status</Dropdown.Item>
                <Dropdown.Item>Cancellations</Dropdown.Item>
              </Dropdown.Menu>
            </Dropdown>
          </Menu.Item> */}
        </Menu.Menu>

        {/* <Menu.Menu position="right">
          <Link to="/help/">
            <Menu.Item name="help" onClick={this.handleItemClick}>
              <Icon name="help" size="large" style={iconStyle} />
            </Menu.Item>
          </Link>
        </Menu.Menu> */}
      </Menu>
    );
  }
}

export default TopMenu;
