// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Menu as MenuIcon } from "common/warehouse/mui-icons";
import PropTypes from "prop-types";

import MenuItem from "./MenuItem/MenuItem";

import CssClasses from "./ApplicationMenu.module.css";

/**
 * Main Application Menu - Shows a list of Application Action items in the
 * menu list and Handles display of an action's display panel upon user selection.
 * Passed as a child to ApplicationHeader Element.
 */
export default class ApplicationMenu extends React.PureComponent {
  constructor(props) {
    super(props);
    this.state = {
      showMenu: false,
      // Key is react component, value is boolean
      mapShowItemPanelFlags: this.createDisplayFlagsMapForChildrenPanels(
        props.children
      ),
    };
  }

  createDisplayFlagsMapForChildrenPanels = (children) => {
    let map = new Map();

    if (!children) return map;
    React.Children.forEach(children, (childPanel) => {
      map.set(childPanel, false);
    });

    return map;
  };

  onClickApplicationMenuIcon = () => {
    this.setState({ showMenu: !this.state.showMenu });
  };

  // On Click an item in the displayed App Menu List, set flag to display the corresponding panel
  onClickMenuItem = (itemPanel) => {
    let map = Object.assign(new Map(), this.state.mapShowItemPanelFlags);

    map.set(itemPanel, true);

    // Don't show menu list anymore since Item's Panel will appear on the screen
    this.setState({
      showMenu: !this.state.showMenu,
      mapShowItemPanelFlags: map,
    });
  };

  onCloseTargetPanel = (itemPanel) => {
    let map = Object.assign(new Map(), this.state.mapShowItemPanelFlags);

    map.set(itemPanel, false);

    this.setState({
      mapShowItemPanelFlags: map,
    });
  };

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (this.props.children !== nextProps.children) {
      this.setState({
        showMenu: false,
        // Key is react component, value is boolean
        mapShowItemPanelFlags: this.createDisplayFlagsMapForChildrenPanels(
          nextProps.children
        ),
      });
    }
  }

  render() {
    let menu;
    let tooltip = "Click to open App menu";
    let panels = [];

    if (!this.props.children) {
      tooltip = "(No menu options found to be displayed)";
    } else {
      let menuItems = [];

      React.Children.forEach(this.props.children, (childPanel) => {
        if (this.state.showMenu === true) {
          // Create Menu Item for the child
          // Assumes child class to have static properties as used here
          menuItems.push(
            <MenuItem
              key={`appmenuitem-${childPanel.type.prototype.PANEL_TITLE}`}
              content={childPanel.type.prototype.PANEL_TITLE}
              tooltip={childPanel.type.prototype.TOOL_TIP}
              onClick={() => this.onClickMenuItem(childPanel)}
            />
          );
        }
        // If the panel needs to be shown then add it to panels array
        if (this.state.mapShowItemPanelFlags.get(childPanel) === true) {
          // Set onClose for the Panel component
          // Assumption: The Panel Component uses an onClose function
          panels.push(
            React.cloneElement(childPanel, {
              key: "appmenuitempanel-" + childPanel.type.name,
              onClose: this.onCloseTargetPanel.bind(this),
            })
          );
        }
      });

      if (this.state.showMenu === true) {
        menu = <div className={CssClasses.menu}>{menuItems}</div>;
        tooltip = "Click to close App Menu";
      }
    }

    return (
      <React.Fragment>
        <MenuIcon
          title={tooltip}
          className={CssClasses.menuIcon}
          onClick={() => this.onClickApplicationMenuIcon()}
        />
        {menu}
        {panels}
      </React.Fragment>
    );
  }
}

ApplicationMenu.propTypes = {
  /**
   * Array of App Menu Actions Panel Components
   */
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.element),
    PropTypes.element,
  ]),
};
