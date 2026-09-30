// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import { Icon } from 'semantic-ui-react';
import './Header.css';

class Header extends Component {
  render() {
    const menu = this.props.menu
    return (
      <div className="header-container">
        <h2 className="top-header">{menu}</h2>
        <div className="sub-header">
          <h5>
            <Icon name="calendar outline" />
            {menu} &emsp; / &emsp;
            <Icon name="calendar outline" />
            {menu}
          </h5>
        </div>
      </div>
    );
  }
}

export default Header;
