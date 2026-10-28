// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";

import CssClasses from "./MenuItem.module.css";

/**
 * Main App Menu Item
 */
const MenuItem = (props) => {
  return (
    <div
      className={CssClasses.main}
      title={props.tooltip}
      onClick={() => props.onClick()}
    >
      - {props.content}
    </div>
  );
};

MenuItem.propTypes = {
  /**
   * Content Label
   */
  content: PropTypes.string,
  /**
   * Tooltip text to show on Menu Item Hover
   */
  tooltip: PropTypes.string,
  /**
   * On click callback function
   */
  onClick: PropTypes.func,
};

export default MenuItem;
