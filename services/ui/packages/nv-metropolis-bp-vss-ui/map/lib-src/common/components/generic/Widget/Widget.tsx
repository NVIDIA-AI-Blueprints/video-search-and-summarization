// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

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

export default Widget;
