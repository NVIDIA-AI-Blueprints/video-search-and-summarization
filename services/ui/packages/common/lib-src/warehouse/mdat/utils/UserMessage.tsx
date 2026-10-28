// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import _ from "lodash";
import { Axios } from "common/warehouse";
import { Chip } from "common/warehouse/mui";

/**
 * Processes Axios AJAX Error Objects, provides descriptive
 * messages in different forms, adds more information about
 * the error, stores post error actions
 */
export default class UserMessage {
  /**
   * @public
   * Enum
   */
  static SRC_TYPE = {
    GENERIC: "generic",
    AXIOS_ERROR: "axios-error",
  };

  /**
   * @public
   * Enum
   */
  static MESSAGE_LEVEL = {
    INFO: "Info",
    WARNING: "Warning",
    ERROR: "Error",
  };

  /**
   * Process generic error type
   */
  _processGenericError(errorObj) {
    if (_.isString(errorObj)) {
      return errorObj;
    }
    return errorObj.message;
  }

  /**
   * Process Axios canceled error
   */
  _processAxiosCanceledError(errorObj) {
    this.msgLevel = UserMessage.MESSAGE_LEVEL.INFO;
    return " Canceled - " + errorObj.message;
  }

  /**
   * Extract response error message from Axios response
   */
  _extractResponseErrorMsg(response) {
    if (_.isString(response.data)) {
      return response.data;
    }
    if (response.data) {
      return JSON.stringify(response.data);
    }
    return "No message in response";
  }

  /**
   * Build status text string
   */
  _buildStatusText(status, statusText) {
    if (statusText !== "") {
      return " " + statusText;
    }
    return "";
  }

  /**
   * Process Axios response error
   */
  _processAxiosResponseError(errorObj, longMessage) {
    const responseErrorMsg = this._extractResponseErrorMsg(errorObj.response);
    this.shortMessage = responseErrorMsg;
    const statusText = this._buildStatusText(
      errorObj.response.status,
      errorObj.response.statusText
    );
    return (
      longMessage +
      " (status - " +
      errorObj.response.status +
      statusText +
      ") " +
      ": " +
      responseErrorMsg
    );
  }

  /**
   * Process Axios request error (no response received)
   */
  _processAxiosRequestError(errorObj, longMessage) {
    let errorText = "Server did not respond";
    if (errorObj.message && errorObj.message !== "") {
      errorText += " (" + errorObj.message + ")";
    }
    this.shortMessage = errorText;
    const statusText = this._buildStatusText(
      errorObj.request.status,
      errorObj.request.statusText
    );
    return (
      longMessage +
      " (status - " +
      errorObj.request.status +
      statusText +
      ") " +
      ": " +
      errorText
    );
  }

  /**
   * Process Axios request creation failure
   */
  _processAxiosRequestCreationError(errorObj, longMessage) {
    let errorText = "Request Creation Failure";
    if (errorObj.message && errorObj.message !== "") {
      errorText += " (" + errorObj.message + ")";
    }
    this.shortMessage = errorText;
    return longMessage + ": " + errorText;
  }

  /**
   * Process Axios error object
   */
  _processAxiosError(errorObj) {
    if (Axios.isCancel(errorObj)) {
      const message = this._processAxiosCanceledError(errorObj);
      this.shortMessage = message;
      return message;
    }

    let longMessage = "";
    if (errorObj.config) {
      longMessage = errorObj.config.method + " " + errorObj.config.url;
    }

    if (errorObj.message) {
      longMessage += " - " + errorObj.message;
    }

    if (errorObj.response) {
      return this._processAxiosResponseError(errorObj, longMessage);
    }
    if (errorObj.request) {
      return this._processAxiosRequestError(errorObj, longMessage);
    }
    return this._processAxiosRequestCreationError(errorObj, longMessage);
  }

  /**
   * Process error based on source type
   */
  _processError(srcType, errorObj) {
    if (srcType === UserMessage.SRC_TYPE.GENERIC) {
      const message = this._processGenericError(errorObj);
      this.shortMessage = message;
      return message;
    }
    if (srcType === UserMessage.SRC_TYPE.AXIOS_ERROR) {
      if (_.isString(errorObj)) {
        this.shortMessage = errorObj;
        return errorObj;
      }
      return this._processAxiosError(errorObj);
    }
    throw new Error("unexpected srcType argument");
  }

  /**
   * Format long message with metadata
   */
  _formatLongMessage(msgLevel, timestampMs, srcTitle, longMessage) {
    return (
      "[" +
      msgLevel +
      "] " +
      "[" +
      new Date(timestampMs).toLocaleString() +
      "] " +
      srcTitle +
      ": " +
      longMessage
    );
  }

  /**
   * Create post action element if provided
   */
  _createPostActionElement(postActionElementTitle, postActionCB) {
    if (!postActionElementTitle || !postActionCB) {
      return null;
    }
    return (
      <Chip
        variant="filled"
        color="secondary"
        onClick={postActionCB}
        label={postActionElementTitle}
      />
    );
  }

  /**
   * @public
   * Constructor
   *
   * @param srcType enum UserMessage.prototype.SRC_TYPE enum values
   * @param msgLevel UserMessage.prototype.MESSAGE_LEVEL enum values
   * @param errorObj could be string literal, String(), Error() or Object with message property
   * @param srcTitle string for purpose of the action that led to this user message creation
   * @param postActionElementTitle string on post action button
   * @param postActionCB function callback for post action
   */
  constructor(
    srcType,
    msgLevel,
    srcTitle,
    errorObj,
    postActionElementTitle = null,
    postActionCB = null
  ) {
    this.timestampMs = _.now();
    this.msgLevel = msgLevel;
    this.longMessage = "no error message to parse";
    this.shortMessage = this.longMessage;

    this.longMessage = this._processError(srcType, errorObj);

    this.shortMessage = srcTitle + ": " + this.shortMessage;
    this.longMessage = this._formatLongMessage(
      this.msgLevel,
      this.timestampMs,
      srcTitle,
      this.longMessage
    );

    this.postActionElement = this._createPostActionElement(
      postActionElementTitle,
      postActionCB
    );
  }

  /**
   * @public
   */
  getShortString = () => {
    return this.shortMessage;
  };

  /**
   * @public
   */
  getLongString = () => {
    return this.longMessage;
  };

  /**
   * @public
   *
   * returns creation timestamp in epoch milliseconds
   */
  getTimestampMs = () => {
    return this.timestampMs;
  };

  /**
   * @public
   * Assuming target use in React Application, this returns a React Component
   * with long message and post action element
   */
  getShortElement = () => {
    return (
      <div>
        {this.shortMessage}
        {this.postActionElement}
      </div>
    );
  };

  /**
   * @public
   * Assuming target use in React Application, this returns a React Component
   * with long message and post action element
   */
  getLongElement = () => {
    return (
      <div>
        {this.longMessage}
        {this.postActionElement}
      </div>
    );
  };

  /**
   * @public
   * Assuming target use in React Application, this returns a React Component
   * for post action as a button
   */
  getPostActionElement = () => {
    return this.postActionElement;
  };

  /**
   * @public
   * returns MESSAGE_LEVEL: INFO, WARNING, ERROR
   */
  getMessageLevel = () => {
    return this.msgLevel;
  };
}
