// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";

import classes from "./Widget.module.css";

/**
 * A semi transparent div element
 */
const Widget = (props) => {
  let style = {
    top: props.top,
    bottom: props.bottom,
    left: props.left,
    right: props.right,
    height: props.height,
    width: props.width,
    zIndex: props.zIndex,
  };

  if (props.showShadow === true) {
    style = Object.assign(
      {
        boxShadow:
          "0 4px 8px 0 rgba(0, 0, 0, 0.2), 0 6px 20px 0 rgba(0, 0, 0, 0.19)",
      },
      style
    );
  }

  return (
    <div className={classes.widget} style={style}>
      {props.children}
    </div>
  );
};

Widget.propTypes = {
  children: PropTypes.node.isRequired,
  /**
   * top position on screen. Same as CSS top property.
   */
  top: PropTypes.string,
  /**
   * bottom position on screen.
   * Same as CSS property.
   * Is required if no top.
   */
  bottom: PropTypes.string,
  /**
   * left position on screen.
   * Same as CSS property.
   */
  left: PropTypes.string,
  /**
   * right position on screen.
   * Same as CSS property.
   * Is required if no left.
   */
  right: PropTypes.string,
  /**
   * width on screen.
   * Same as CSS property.
   */
  width: PropTypes.string,
  /**
   * height on screen.
   * Same as CSS property.
   */
  height: PropTypes.string,
  /**
   * zIndex integer value as string.
   * Same as CSS property.
   */
  zIndex: PropTypes.string,
  /**
   * Bool value to add predefined box-shadow css property.
   */
  showShadow: PropTypes.bool,
};

export default Widget;
