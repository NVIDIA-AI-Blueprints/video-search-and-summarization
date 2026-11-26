// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { BeautifyNameString, Widget } from "common/map";

import CssClasses from "./KpiPlaceTitle.module.css";

/**
 * Stateless Location Title Display
 */
const KpiPlaceTitle = (props) => {
  return (
    <Widget
      bottom="0"
      left="0"
      height="2.75rem"
      width="27%"
      zIndex="25"
    >
      <div className={CssClasses.text} title="Place name">
        {BeautifyNameString(props.title)}
      </div>
    </Widget>
  );
};

export default KpiPlaceTitle;
