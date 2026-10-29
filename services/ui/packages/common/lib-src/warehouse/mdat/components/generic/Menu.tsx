// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Button, IconButton } from "common/warehouse/mui";
import { Menu as MenuIcon } from "common/warehouse/mui-icons";
import PropTypes from "prop-types";
import React from "react";

import CssClasses from "./Menu.module.css";

/**
 * Custom Menu Component with Material-icon 'menu' as the clickable icon
 * and popup menu list with items
 */
class CustomMenu extends React.PureComponent {
  state = { open: false };

  handleOpen = () => this.setState({ open: true });
  handleClose = () => this.setState({ open: false });

  render() {
    return (
      <div className={CssClasses.wrap}>
        <IconButton
          className={CssClasses.trigger}
          aria-controls="simple-menu"
          aria-haspopup="true"
          aria-expanded={this.state.open}
          title="Menu"
          onClick={this.state.open ? this.handleClose : this.handleOpen}
        >
          <MenuIcon title="Menu" />
        </IconButton>
        {this.state.open ? (
          <ul className={CssClasses.menu} role="menu">
            {this.props.items.map((item, key) => (
              <li key={key}>
                <Button
                  variant="text"
                  role="menuitem"
                  className={CssClasses.item}
                  onClick={(e) => {
                    item.onClick(e);
                    this.handleClose();
                  }}
                >
                  {item.label}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }
}

CustomMenu.propTypes = {
  /**
   * Array of object: [{ label string, onClickCallback }]
   */
  items: PropTypes.array.isRequired,
};

export default CustomMenu;
