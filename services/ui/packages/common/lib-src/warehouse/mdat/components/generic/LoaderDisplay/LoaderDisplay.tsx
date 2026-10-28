// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import {
  DoneOutline as DoneOutlineIcon,
  Error as ErrorIcon,
} from "common/warehouse/mui-icons";

import UserMessage from "./../../../utils/UserMessage";

import CssClasses from "./LoaderDisplay.module.css";

// TODO: replace spinner with Kaizen UI Spinner

// Same as background in .outermostBoundary (#ddd)
const BACKGROUND_RGB = "221, 221, 221";

/**
 * Loader Display Element.
 * Presents Loading, Failure and Success scenarios.
 */
const LoaderDisplay = (props) => {
  let iconDiv, iconDivContent, userMessageDiv;
  let size = { fontSize: "1vw" }; // default size

  if (props.size === LoaderDisplay.SIZE.MEDIUM) {
    size = { fontSize: "2vw" };
  } else if (props.size === LoaderDisplay.SIZE.LARGE) {
    size = { fontSize: "3vw" };
  }

  if (props.type === LoaderDisplay.TYPE.LOADING) {
    // TODO: Make loading icon follow different sizes after using Kaizen Spinner
    iconDivContent = <div className={CssClasses.loaderIcon} />;
  } else if (props.type === LoaderDisplay.TYPE.LOAD_FAILURE) {
    iconDivContent = <ErrorIcon className={CssClasses.errorIcon} />;
  } else if (props.type === LoaderDisplay.TYPE.LOAD_SUCCESS) {
    iconDivContent = <DoneOutlineIcon className={CssClasses.successIcon} />;
  }

  if (iconDivContent) {
    iconDiv = <div className={CssClasses.iconDiv}>{iconDivContent}</div>;
  }

  if (props.userMessage instanceof UserMessage) {
    let userMessageDivPostActionElementDiv;
    let postActionElement = props.userMessage.getPostActionElement();
    if (postActionElement !== null && postActionElement !== undefined) {
      userMessageDivPostActionElementDiv = (
        <div className={CssClasses.userMessagePostActionElementDiv}>
          <h4>{postActionElement}</h4>
        </div>
      );
    }

    let userMessageTextDiv = (
      <div className={CssClasses.userMessageTextDiv}>
        {props.userMessage.getShortString()}
      </div>
    );

    userMessageDiv = (
      <div className={CssClasses.userMessageDiv}>
        {userMessageTextDiv}
        {userMessageDivPostActionElementDiv}
      </div>
    );
  } else if (typeof props.userMessage === "string") {
    userMessageDiv = (
      <div className={CssClasses.userMessageDiv}>
        <div className={CssClasses.userMessageTextDiv}>{props.userMessage}</div>
      </div>
    );
  }

  const backgroundOpacity =
    props.backgroundOpacity !== undefined ? props.backgroundOpacity : 1;
  const rootStyle = {
    ...size,
    ...(backgroundOpacity === 0
      ? { background: "transparent" }
      : backgroundOpacity < 1
        ? { background: `rgba(${BACKGROUND_RGB}, ${backgroundOpacity})` }
        : {}),
  };

  return (
    <div
      className={CssClasses.outermostBoundary}
      style={rootStyle}
    >
      {iconDiv}
      {userMessageDiv}
    </div>
  );
};

/**
 * @public
 * Enum for types
 */
LoaderDisplay.TYPE = {
  LOADING: 0,
  LOAD_FAILURE: 1,
  LOAD_SUCCESS: 2,
};

/**
 * @public
 * Enum for size types
 */
LoaderDisplay.SIZE = {
  SMALL: 0,
  MEDIUM: 1,
  LARGE: 2,
};

LoaderDisplay.propTypes = {
  /**
   * Type of Loader Display. One of Enum LoaderDisplay.TYPE
   */
  type: PropTypes.oneOf(_.toArray(LoaderDisplay.TYPE)).isRequired,
  /**
   * UserMessage Object to show apt message to user
   */
  userMessage: PropTypes.oneOfType([
    PropTypes.instanceOf(UserMessage),
    PropTypes.string,
  ]),
  /**
   * Display Size of icons/text
   */
  size: PropTypes.oneOf(_.toArray(LoaderDisplay.SIZE)),
  /**
   * Opacity of the root background (0 = transparent, 1 = fully opaque). Default: 1
   */
  backgroundOpacity: PropTypes.number,
};

export default LoaderDisplay;
