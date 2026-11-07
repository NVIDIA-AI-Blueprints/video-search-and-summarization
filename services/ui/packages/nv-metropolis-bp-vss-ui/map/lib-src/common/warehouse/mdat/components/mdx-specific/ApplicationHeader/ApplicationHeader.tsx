// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";

import classes from "./ApplicationHeader.module.css";

/**
 * Application's main Header.
 * Encapsulates static logo, a Title, an Application Level Menu
 */
const ApplicationHeader = (props) => {
  return (
    <React.Fragment>
      <div className={classes.main}>
        {props.logo ? (
          <img className={classes.nvidialogo} alt="Nvidia" src={props.logo} />
        ) : null}
        <div className={classes.titleMain}>{props.textTitle}</div>
        <div className={classes.titleFooter}>{props.textTitleFooter}</div>
        <div className={classes.appActionIcons}>{props.children}</div>
      </div>
    </React.Fragment>
  );
};

ApplicationHeader.propTypes = {
  /**
   * Single Application Menu Element. Refer to ApplicationMenu Component in this library
   */
  children: PropTypes.oneOfType([PropTypes.array, PropTypes.element]),
  /**
   * Main Title Text String
   */
  textTitle: PropTypes.string,
  /**
   * Main Title Footer Text String
   */
  textTitleFooter: PropTypes.string,
  /**
   * Application Logo (png, jpg, svg etc.)
   */
  logo: PropTypes.string,
};

export default ApplicationHeader;
