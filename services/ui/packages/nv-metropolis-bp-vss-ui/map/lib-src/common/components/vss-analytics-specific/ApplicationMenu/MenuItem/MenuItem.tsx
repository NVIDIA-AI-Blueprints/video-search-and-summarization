// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

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

export default MenuItem;
