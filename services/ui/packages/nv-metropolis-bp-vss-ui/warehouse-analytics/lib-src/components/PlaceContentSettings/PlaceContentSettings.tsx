// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  IconButton,
  Typography,
} from "common/warehouse/mui";
import {
  ExpandMore as ExpandMoreIcon,
  Settings as SettingsIcon,
} from "common/warehouse/mui-icons";

import RoomOccupancySettingPanel from "./SettingsPanels/RoomOccupancySettingPanel";

import CssClasses from "./PlaceContentSettings.module.css";

/**
 * Settings Menu within a Place Content View Context
 */
export default class PlaceContentSettings extends React.PureComponent {
  state = {
    showMenu: false,
  };

  onSettingsBtnClick = () => {
    this.setState({ showMenu: !this.state.showMenu });
  };

  // Helper function to create an item from a Panel class
  createMenuListItemWithPanel = (panelClass, panelTitle) => {
    // Passes props from this component to the created panel component, as is
    let panel = React.createElement(panelClass, this.props);

    // Expects panelClass to have title
    return (
      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>
            {panelTitle ? panelTitle : panelClass.prototype.PANEL_TITLE}
          </Typography>
        </AccordionSummary>
        <AccordionDetails>{panel}</AccordionDetails>
      </Accordion>
    );
  };

  render() {
    let menu = null;

    if (this.state.showMenu === true) {
      menu = (
        <div className={CssClasses.menuList}>
          {this.createMenuListItemWithPanel(RoomOccupancySettingPanel)}
        </div>
      );
    }

    return (
      <div className={CssClasses.main}>
        <IconButton
          className={`${CssClasses.settingsBtn} ${this.state.showMenu ? CssClasses.settingsBtnActive : ""}`}
          data-tooltip={this.state.showMenu ? undefined : "Room settings"}
          aria-label="Room settings"
          aria-expanded={this.state.showMenu}
          title="Room settings"
          onClick={this.onSettingsBtnClick}
        >
          <SettingsIcon />
        </IconButton>
        {menu}
      </div>
    );
  }
}
